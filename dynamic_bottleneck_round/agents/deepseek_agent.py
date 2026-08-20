"""DeepSeek departure-time Agent for the dynamic-capacity bottleneck app."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Mapping, Sequence


DEFAULT_DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
DEFAULT_DEEPSEEK_MODEL = 'deepseek-v4-flash'
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_TEMPERATURE = 0.0
DEFAULT_LIMITED_MEMORY_MAX_CHARS = 400


class DeepSeekAgentError(Exception):
    pass


@dataclass(frozen=True)
class DeepSeekAgentConfig:
    api_key: str = ''
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    model: str = DEFAULT_DEEPSEEK_MODEL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    temperature: float = DEFAULT_TEMPERATURE
    limited_memory_enabled: bool = False
    limited_memory_max_chars: int = DEFAULT_LIMITED_MEMORY_MAX_CHARS

    @property
    def chat_completions_url(self) -> str:
        return f'{self.base_url.rstrip("/")}/chat/completions'


@dataclass(frozen=True)
class AgentChoiceSet:
    round_number: int
    total_rounds: int
    available_slots: Sequence[Mapping[str, object]]
    cost_parameters: Mapping[str, object]
    capacity_context: Mapping[str, object]
    tolls: Sequence[Mapping[str, object]] = field(default_factory=list)
    rewards: Sequence[Mapping[str, object]] = field(default_factory=list)
    history: Mapping[str, object] = field(default_factory=dict)
    agent_id: str = ''
    persona: Mapping[str, object] = field(default_factory=dict)
    limited_memory: str = ''

    def valid_slots(self) -> set[int]:
        return {int(item['slot']) for item in self.available_slots}


@dataclass(frozen=True)
class AgentChoice:
    departure_slot: int
    decision_source: str
    fallback_used: bool
    reason: str = ''
    latency_ms: int = 0
    raw_response_json: str = ''
    context_json: str = ''
    memory_summary: str | None = None


HttpPost = Callable[[DeepSeekAgentConfig, Mapping[str, object]], Mapping[str, object]]


def build_chat_completion_payload(
    config: DeepSeekAgentConfig,
    choice_set: AgentChoiceSet,
) -> dict[str, object]:
    context = asdict(choice_set)
    if not config.limited_memory_enabled or not choice_set.limited_memory:
        context.pop('limited_memory', None)
    memory_instruction = ''
    if config.limited_memory_enabled:
        memory_instruction = (
            ' Also return memory_summary: a concise subjective memory for the next '
            f'round, no more than {config.limited_memory_max_chars} characters. Base '
            'it only on the supplied public feedback and your own result; do not '
            'invent observations or reproduce a complete round-by-round history.'
        )
    return {
        'model': config.model,
        'temperature': config.temperature,
        'messages': [
            {
                'role': 'system',
                'content': (
                    'You are a virtual participant in a dynamic-capacity '
                    'single-bottleneck departure-time experiment. Treat the persona '
                    'traits as stable preferences. Any limited_memory is your own '
                    'fallible prior impression, not a system fact; revise it only from '
                    'the public context supplied in this request. Use only capacity information '
                    'present in the context; an unrevealed current capacity is unknown. '
                    'Choose one legal departure slot. Return only valid JSON with keys '
                    f'departure_slot and reason.{memory_instruction} Do not include markdown.'
                ),
            },
            {
                'role': 'user',
                'content': (
                    'You cannot observe current-round human choices. Make a simultaneous '
                    'choice from the available slots using this experiment context:\n\n'
                    f'{json.dumps(context, ensure_ascii=False, sort_keys=True)}'
                ),
            },
        ],
    }


def choose_agent_departure(
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
        choice = parse_deepseek_choice(
            raw_response,
            choice_set.valid_slots(),
            memory_max_chars=(
                config.limited_memory_max_chars
                if config.limited_memory_enabled
                else 0
            ),
        )
        return AgentChoice(
            departure_slot=choice.departure_slot,
            decision_source=choice.decision_source,
            fallback_used=False,
            reason=choice.reason,
            latency_ms=latency_ms,
            raw_response_json=json.dumps(
                raw_response,
                ensure_ascii=False,
                sort_keys=True,
            ),
            context_json=context_json,
            memory_summary=choice.memory_summary,
        )
    except (
        DeepSeekAgentError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        urllib.error.URLError,
    ) as exc:
        return AgentChoice(
            departure_slot=fallback_lowest_schedule_cost(choice_set),
            decision_source='fallback_lowest_schedule_cost',
            fallback_used=True,
            reason=str(exc),
            latency_ms=int((time.monotonic() - started) * 1000),
            context_json=context_json,
        )


def parse_deepseek_choice(
    raw_response: Mapping[str, object],
    valid_slots: Iterable[int],
    memory_max_chars: int = 0,
) -> AgentChoice:
    choices = raw_response.get('choices')
    if not isinstance(choices, list) or not choices:
        raise DeepSeekAgentError('DeepSeek response has no choices')
    message = choices[0].get('message') if isinstance(choices[0], Mapping) else None
    if not isinstance(message, Mapping):
        raise DeepSeekAgentError('DeepSeek response choice has no message')
    content = message.get('content')
    if not isinstance(content, str) or not content.strip():
        raise DeepSeekAgentError('DeepSeek response message has empty content')

    parsed = _parse_json_content(content)
    slot = int(parsed['departure_slot'])
    if slot not in {int(item) for item in valid_slots}:
        raise DeepSeekAgentError(f'DeepSeek returned illegal departure_slot: {slot}')
    return AgentChoice(
        departure_slot=slot,
        decision_source='deepseek_api',
        fallback_used=False,
        reason=str(parsed.get('reason', '')),
        memory_summary=normalize_memory_summary(
            parsed.get('memory_summary'),
            memory_max_chars,
        ),
    )


def normalize_memory_summary(value, max_chars: int) -> str | None:
    if not isinstance(value, str) or max_chars <= 0:
        return None
    normalized = ' '.join(value.split()).strip()
    if not normalized:
        return None
    return normalized[:max_chars]


def post_chat_completion(
    config: DeepSeekAgentConfig,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    if not config.api_key:
        raise DeepSeekAgentError('DEEPSEEK_AGENT_API_KEY is empty')
    request = urllib.request.Request(
        config.chat_completions_url,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {config.api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        return json.loads(response.read().decode('utf-8'))


def fallback_lowest_schedule_cost(choice_set: AgentChoiceSet) -> int:
    costs = choice_set.cost_parameters
    early_rate = _parse_float(costs.get('early_cost_per_minute'), 1)
    late_rate = _parse_float(costs.get('late_cost_per_minute'), 3)
    preferred_arrival = _parse_float(costs.get('preferred_arrival_minute'), 480)
    free_flow_minutes = _parse_float(costs.get('free_flow_travel_minutes'), 6)
    toll_by_slot = _slot_value_map(choice_set.tolls, 'charge')
    reward_by_slot = _slot_value_map(choice_set.rewards, 'bonus')
    free_flow_departure = preferred_arrival - free_flow_minutes

    ranked = []
    for item in choice_set.available_slots:
        slot = int(item['slot'])
        departure_minute = _parse_float(
            item.get('departure_minute'),
            free_flow_departure,
        )
        arrival_minute = departure_minute + free_flow_minutes
        score = (
            early_rate * max(0, preferred_arrival - arrival_minute)
            + late_rate * max(0, arrival_minute - preferred_arrival)
            + toll_by_slot.get(slot, 0)
            - reward_by_slot.get(slot, 0)
        )
        ranked.append((score, abs(departure_minute - free_flow_departure), slot))
    if not ranked:
        raise DeepSeekAgentError('Agent has no available departure slots')
    return min(ranked)[2]


def config_from_session(session_config) -> DeepSeekAgentConfig:
    return DeepSeekAgentConfig(
        api_key=os.environ.get('DEEPSEEK_AGENT_API_KEY', '').strip(),
        base_url=str(
            session_config.get(
                'api_agent_base_url',
                os.environ.get('DEEPSEEK_AGENT_BASE_URL', DEFAULT_DEEPSEEK_BASE_URL),
            )
        ).strip().rstrip('/'),
        model=str(
            session_config.get(
                'api_agent_model',
                os.environ.get('DEEPSEEK_AGENT_MODEL', DEFAULT_DEEPSEEK_MODEL),
            )
        ).strip(),
        timeout_seconds=max(
            1,
            _parse_int(
                session_config.get(
                    'api_agent_timeout_seconds',
                    os.environ.get(
                        'DEEPSEEK_AGENT_TIMEOUT_SECONDS',
                        DEFAULT_TIMEOUT_SECONDS,
                    ),
                ),
                DEFAULT_TIMEOUT_SECONDS,
            ),
        ),
        temperature=_parse_float(
            session_config.get(
                'api_agent_temperature',
                os.environ.get('DEEPSEEK_AGENT_TEMPERATURE', DEFAULT_TEMPERATURE),
            ),
            DEFAULT_TEMPERATURE,
        ),
        limited_memory_enabled=_parse_bool(
            session_config.get('api_agent_limited_memory_enabled', 0)
        ),
        limited_memory_max_chars=max(
            1,
            _parse_int(
                session_config.get(
                    'api_agent_limited_memory_max_chars',
                    DEFAULT_LIMITED_MEMORY_MAX_CHARS,
                ),
                DEFAULT_LIMITED_MEMORY_MAX_CHARS,
            ),
        ),
    )


def _parse_json_content(content: str) -> Mapping[str, object]:
    text = content.strip()
    if text.startswith('```'):
        text = '\n'.join(
            line
            for line in text.splitlines()
            if not line.strip().startswith('```')
        ).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, Mapping) or 'departure_slot' not in parsed:
        raise DeepSeekAgentError('DeepSeek JSON content has no departure_slot')
    return parsed


def _slot_value_map(
    items: Sequence[Mapping[str, object]],
    value_key: str,
) -> dict[int, float]:
    return {
        int(item['slot']): _parse_float(item.get(value_key), 0)
        for item in items
        if 'slot' in item
    }


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


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return False
