"""DeepSeek API agent foundation for single_bottleneck shadow mode.

Shadow mode calls the model and records the suggested departure slot, but it
does not add the agent to bottleneck settlement. The active-mode integration
should consume the same ``AgentDecision`` shape later and explicitly decide
whether to include the agent in the round's actor list.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Mapping, Sequence


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_TEMPERATURE = 0.0


@dataclass(frozen=True)
class DeepSeekAgentConfig:
    api_key: str = ""
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    model: str = DEFAULT_DEEPSEEK_MODEL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    temperature: float = DEFAULT_TEMPERATURE

    @classmethod
    def from_env(cls) -> "DeepSeekAgentConfig":
        return cls(
            api_key=os.environ.get("DEEPSEEK_AGENT_API_KEY", "").strip(),
            base_url=os.environ.get("DEEPSEEK_AGENT_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL).strip().rstrip("/"),
            model=os.environ.get("DEEPSEEK_AGENT_MODEL", DEFAULT_DEEPSEEK_MODEL).strip(),
            timeout_seconds=_parse_int(
                os.environ.get("DEEPSEEK_AGENT_TIMEOUT_SECONDS"),
                DEFAULT_TIMEOUT_SECONDS,
            ),
            temperature=_parse_float(
                os.environ.get("DEEPSEEK_AGENT_TEMPERATURE"),
                DEFAULT_TEMPERATURE,
            ),
        )

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


@dataclass(frozen=True)
class AgentChoiceSet:
    round_number: int
    total_rounds: int
    available_slots: Sequence[Mapping[str, object]]
    cost_parameters: Mapping[str, object]
    tolls: Sequence[Mapping[str, object]] = field(default_factory=list)
    rewards: Sequence[Mapping[str, object]] = field(default_factory=list)
    history: Mapping[str, object] = field(default_factory=dict)
    agent_id: str = ""
    persona: Mapping[str, object] = field(default_factory=dict)

    def valid_slots(self) -> set[int]:
        return {int(item["slot"]) for item in self.available_slots}


@dataclass(frozen=True)
class AgentChoice:
    departure_slot: int
    decision_source: str
    fallback_used: bool
    reason: str = ""
    latency_ms: int = 0
    raw_response_json: str = ""
    context_json: str = ""


HttpPost = Callable[[DeepSeekAgentConfig, Mapping[str, object]], Mapping[str, object]]


def build_chat_completion_payload(
    config: DeepSeekAgentConfig,
    choice_set: AgentChoiceSet,
) -> dict[str, object]:
    context = asdict(choice_set)
    return {
        "model": config.model,
        "temperature": config.temperature,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a virtual participant in a single-bottleneck departure-time experiment. "
                    "When a persona is present in the context, treat its 1-10 trait scores as "
                    "stable behavioral preferences for this Agent and remain consistent with them. "
                    "Choose one legal departure slot. Return only valid JSON with keys "
                    "departure_slot and reason. Do not include markdown."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Use the experiment context below. You cannot observe current-round human choices; "
                    "make a simultaneous choice from the available slots.\n\n"
                    f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
                ),
            },
        ],
    }


def choose_shadow_departure(
    *,
    config: DeepSeekAgentConfig,
    choice_set: AgentChoiceSet,
    http_post: HttpPost | None = None,
) -> AgentChoice:
    payload = build_chat_completion_payload(config, choice_set)
    context_json = json.dumps(asdict(choice_set), ensure_ascii=False, sort_keys=True)
    post = http_post or post_chat_completion
    started = time.monotonic()
    try:
        raw_response = post(config, payload)
        latency_ms = int((time.monotonic() - started) * 1000)
        choice = parse_deepseek_choice(raw_response, choice_set.valid_slots())
        return AgentChoice(
            departure_slot=choice.departure_slot,
            decision_source=choice.decision_source,
            fallback_used=False,
            reason=choice.reason,
            latency_ms=latency_ms,
            raw_response_json=json.dumps(raw_response, ensure_ascii=False, sort_keys=True),
            context_json=context_json,
        )
    except (DeepSeekAgentError, OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        fallback = fallback_lowest_schedule_cost(choice_set)
        return AgentChoice(
            departure_slot=fallback,
            decision_source="fallback_lowest_schedule_cost",
            fallback_used=True,
            reason=str(exc),
            latency_ms=latency_ms,
            raw_response_json="",
            context_json=context_json,
        )


def parse_deepseek_choice(
    raw_response: Mapping[str, object],
    valid_slots: Iterable[int],
) -> AgentChoice:
    choices = raw_response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise DeepSeekAgentError("DeepSeek response has no choices")

    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    if not isinstance(message, Mapping):
        raise DeepSeekAgentError("DeepSeek response choice has no message")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise DeepSeekAgentError("DeepSeek response message has empty content")

    parsed = _parse_json_content(content)
    slot = int(parsed["departure_slot"])
    valid_slot_set = {int(item) for item in valid_slots}
    if slot not in valid_slot_set:
        raise DeepSeekAgentError(f"DeepSeek returned illegal departure_slot: {slot}")

    reason = parsed.get("reason", "")
    return AgentChoice(
        departure_slot=slot,
        decision_source="deepseek_api",
        fallback_used=False,
        reason=str(reason),
        raw_response_json=json.dumps(raw_response, ensure_ascii=False, sort_keys=True),
    )


def post_chat_completion(
    config: DeepSeekAgentConfig,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    if not config.api_key:
        raise DeepSeekAgentError("DEEPSEEK_AGENT_API_KEY is empty")

    request = urllib.request.Request(
        config.chat_completions_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def fallback_lowest_schedule_cost(choice_set: AgentChoiceSet) -> int:
    costs = choice_set.cost_parameters
    early_rate = _parse_float(costs.get("early_cost_per_minute"), 1)
    late_rate = _parse_float(costs.get("late_cost_per_minute"), 3)
    preferred_arrival = _parse_float(costs.get("preferred_arrival_minute"), 480)
    free_flow_minutes = _parse_float(costs.get("free_flow_travel_minutes"), 6)

    toll_by_slot = _slot_value_map(choice_set.tolls, "charge")
    reward_by_slot = _slot_value_map(choice_set.rewards, "bonus")
    best_slot = int(choice_set.available_slots[0]["slot"])
    best_score = float("inf")
    free_flow_departure = preferred_arrival - free_flow_minutes

    for item in choice_set.available_slots:
        slot = int(item["slot"])
        departure_minute = _parse_float(item.get("departure_minute"), free_flow_departure)
        arrival_minute = departure_minute + free_flow_minutes
        early = max(0.0, preferred_arrival - arrival_minute)
        late = max(0.0, arrival_minute - preferred_arrival)
        score = (
            early_rate * early
            + late_rate * late
            + toll_by_slot.get(slot, 0.0)
            - reward_by_slot.get(slot, 0.0)
        )
        tie_breaker = abs(departure_minute - free_flow_departure)
        best_tie_breaker = abs(
            _parse_float(
                _slot_item(choice_set.available_slots, best_slot).get("departure_minute"),
                free_flow_departure,
            )
            - free_flow_departure
        )
        if (score, tie_breaker) < (best_score, best_tie_breaker):
            best_slot = slot
            best_score = score
    return best_slot


class DeepSeekAgentError(Exception):
    pass


def _parse_json_content(content: str) -> Mapping[str, object]:
    text = content.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, Mapping):
        raise DeepSeekAgentError("DeepSeek JSON content is not an object")
    if "departure_slot" not in parsed:
        raise DeepSeekAgentError("DeepSeek JSON content has no departure_slot")
    return parsed


def _slot_value_map(items: Sequence[Mapping[str, object]], value_key: str) -> dict[int, float]:
    values: dict[int, float] = {}
    for item in items:
        if "slot" not in item:
            continue
        values[int(item["slot"])] = _parse_float(item.get(value_key), 0)
    return values


def _slot_item(items: Sequence[Mapping[str, object]], slot: int) -> Mapping[str, object]:
    for item in items:
        if int(item["slot"]) == slot:
            return item
    return items[0]


def _parse_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
