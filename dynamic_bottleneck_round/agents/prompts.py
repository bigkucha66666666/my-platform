"""Versioned LLM instructions; derived references use public clock data only."""
from copy import deepcopy
import json


LEGACY_PROMPT_VERSION = 'dynamic_bottleneck_prompt_v1'
CURRENT_PROMPT_VERSION = 'dynamic_bottleneck_prompt_v2'
SUPPORTED_PROMPT_VERSIONS = (LEGACY_PROMPT_VERSION, CURRENT_PROMPT_VERSION)


def validate_prompt_version(version):
    if version not in SUPPORTED_PROMPT_VERSIONS:
        raise ValueError(f'api_agent_prompt_version 无效：{version!r}')
    return version


def prompt_version_from_session(config):
    # Missing means an old saved Session, not permission to adopt today's default.
    return validate_prompt_version(config.get('api_agent_prompt_version', LEGACY_PROMPT_VERSION))


def _clock(minute):
    seconds = int(round(float(minute) * 60))
    hour, remainder = divmod(seconds, 3600)
    minute, second = divmod(remainder, 60)
    clock = f'{hour % 24:02d}:{minute:02d}'
    return f'{clock}:{second:02d}' if second else clock


def prepare_prompt_context(context, version, memory_enabled):
    validate_prompt_version(version)
    prepared = deepcopy(context)
    if not memory_enabled or not prepared.get('limited_memory'):
        prepared.pop('limited_memory', None)
    if version == CURRENT_PROMPT_VERSION:
        prepared['prompt_version'] = version
        travel = float(prepared['cost_parameters']['free_flow_travel_minutes'])
        for slot in prepared['available_slots']:
            # No capacity, loads, history, or guessed queue enters this arithmetic.
            baseline = float(slot['departure_minute']) + travel
            slot['fixed_travel_arrival_minute'] = baseline
            slot['fixed_travel_arrival_time'] = _clock(baseline)
    return prepared


def build_prompt_messages(context, version, memory_enabled, memory_max_chars):
    validate_prompt_version(version)
    if version == LEGACY_PROMPT_VERSION:
        # Preserve v1 verbatim so deployment cannot change an ongoing experiment.
        memory_instruction = ''
        if memory_enabled:
            memory_instruction = (
                ' Also return memory_summary: a concise subjective memory for the next '
                f'round, no more than {memory_max_chars} characters. Base '
                'it only on the supplied public feedback and your own result; do not '
                'invent observations or reproduce a complete round-by-round history.'
            )
        system = (
            'You are a virtual participant in a dynamic-capacity '
            'single-bottleneck departure-time experiment. Treat the persona '
            'traits as stable preferences. Any limited_memory is your own '
            'fallible prior impression, not a system fact; revise it only from '
            'the public context supplied in this request. Use only capacity information '
            'present in the context; an unrevealed current capacity is unknown. '
            'Choose one legal departure slot. Return only valid JSON with keys '
            f'departure_slot and reason.{memory_instruction} Do not include markdown.'
        )
        user = (
            'You cannot observe current-round human choices. Make a simultaneous '
            'choice from the available slots using this experiment context:\n\n'
        )
    else:
        system = (
            '你是动态瓶颈出发时间实验中的一名独立虚拟参与者，与其他主体同时决策。'
            '目标是在给定人格偏好下权衡成本，自主选择一个合法出发档位。\n'
            '【时间与客观成本】所有分钟数是同一天的时刻，时钟为对应显示。'
            '以 cost_parameters 的本轮参数为准，不用人格分数替换客观成本系数。'
            '实际到达时刻 = departure_minute + free_flow_travel_minutes + 额外瓶颈行程。'
            'queue_delay_minutes 是含瓶颈通过时间的额外行程，非负，全部计入到达时刻。'
            '早到分钟 = max(preferred_arrival_minute - 实际到达时刻, 0)；'
            '晚到分钟 = max(实际到达时刻 - preferred_arrival_minute, 0)。'
            '总成本 = fixed_travel_time_cost + queue_cost_per_minute × 额外行程'
            ' + early_cost_per_minute × 早到分钟 + late_cost_per_minute × 晚到分钟。'
            '当前场景没有额外通行收费或奖励，tolls / rewards 为空，不得虚构。\n'
            '【每档时间参照】fixed_travel_arrival_minute / fixed_travel_arrival_time'
            ' 仅为出发加固定行程、忽略额外行程的到达下界，不是实际到达预测，'
            '也不是预期总成本。实际到达不能早于这个基准。更晚出发会推迟基准；'
            '避开拥挤可能减少额外行程，但不能因此声称晚于目标的基准能准时到达。'
            '所有 available_slots 均合法，基准晚到的档位仍可自主选择。\n'
            '【信息边界】只使用 capacity_context 已公开的信息。I0 在选择时不知道'
            '当轮实际服务率，上一轮公布值不等于当轮值；I1 可以使用已公开的当轮值。'
            '两者均不知道其他主体的当轮选择或未来服务率序列，不得自行声称已知。\n'
            '【反馈与人格】history 中上一轮个人结果和公开统计是已观察事实。'
            '历史空档成本为未知，不是零成本，也不代表本轮无人选择。'
            'limited_memory 是可修正的个人印象，矛盾时以结构化事实为准。'
            '保留 persona 的风险偏好、适应速度与选择惯性；人格不改变时间算术和客观成本。'
            '当轮尚未结算，主观预期不能写成已发生的结果。不补造完整历史。\n'
            '【最终输出】仅返回一个 JSON 对象，不要 Markdown 或长篇分析。'
            'departure_slot 必须是 available_slots 中的整数；reason 用简短中文'
            '只说明最终选定档位的依据，所述档位、出发时刻和到达基准须相互对应。'
            '若改选，以最终档位为准，不保留互相矛盾的候选结论；不把未知额外行程当作零。'
        )
        if memory_enabled:
            system += (
                f'memory_summary 不超过 {memory_max_chars} 字符，仅提炼已公布反馈与自身'
                '上一轮结果形成的个人印象，允许表达不确定性；没有历史就说明暂无经验。'
                '不得将当前选择或预测记成当轮已发生结果，不得与最终行动或已有事实矛盾。'
            )
        user = (
            '请依据以下实验上下文完成本轮同时选择。先核对所选档位的出发时刻和'
            '固定行程到达基准，再给出一致的最终 JSON；无需输出核对过程。\n\n'
        )
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user + json.dumps(context, ensure_ascii=False, sort_keys=True)},
    ]
