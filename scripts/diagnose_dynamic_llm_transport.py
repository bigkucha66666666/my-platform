"""Opt-in phased diagnosis: 2 calls, conditional compact third; no retries/DB.

Uses a saved independent simulation request, NOT participant data. Only messages
vary; the normal production prompt and 12-second setting are never edited.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import signal
import socket
import ssl
import time
import urllib.error
import urllib.request
from unittest.mock import patch

from dynamic_bottleneck_round.agents.deepseek_agent import (
    DeepSeekAgentError, config_from_session, parse_deepseek_choice, post_chat_completion,
)


def prompt_variants(full):
    minimal = deepcopy(full)
    minimal['messages'] = [
        {'role': 'system', 'content': 'Return only valid JSON. Do not explain.'},
        {'role': 'user', 'content': 'Return exactly {"ok":true}.'},
    ]
    compact = deepcopy(full)
    compact['messages'][0]['content'] = (
        '你是同时选择出发档位的实验参与者，按 persona 偏好自主选择，不改变客观费率。'
        '到达=出发+固定行程+非负额外瓶颈行程（含全部通过时间）。'
        'fixed_travel_arrival 是忽略额外行程的下界，不是预测。'
        '早到=max(目标-到达,0)，晚到=max(到达-目标,0)；总成本为固定成本加额外行程、'
        '早到、晚到各自乘 cost_parameters 的对应费率；无额外收费奖励。'
        'I0 不知当轮服务率，I1 只用已公开值；均不知他人当轮选择或未来序列。'
        'history 是上一轮事实，历史空档成本未知，limited_memory 仅为可能有误的印象。'
        '当轮尚未结算，不把预测当成结果。仅返回 JSON：departure_slot 为合法整数，'
        'reason 简短且与最终档位、出发时刻和到达基准一致。'
    )
    if 'memory_summary' in full['messages'][0]['content']:
        compact['messages'][0]['content'] += (
            'memory_summary 仅总结已公布历史印象，不超过400字符；无历史则说明暂无经验。'
        )
    return {'minimal': minimal, 'full': deepcopy(full), 'compact': compact}


def probe_request(config, payload):
    """Trace the existing urllib adapter without logging credentials/headers.

    A process-local POSIX timer bounds the *whole* diagnostic, unlike a socket
    inactivity timeout. This utility is serial and must run on the main thread.
    """
    started = time.monotonic()
    stage = 'response_headers'
    result = dict(request_payload=deepcopy(payload), events=[], http_status=None,
        safe_response_headers={}, response_received=False, raw_response=None,
        failed_stage='', error_type='', error='')
    original_open = urllib.request.urlopen
    original_connect = socket.create_connection
    original_wrap = ssl.SSLContext.wrap_socket

    def event(name, **values):
        result['events'].append(dict(stage=name,
            elapsed_seconds=round(time.monotonic() - started, 4), **values))

    def traced_connect(*args, **kwargs):
        nonlocal stage
        stage = 'tcp_including_dns'
        connection = original_connect(*args, **kwargs)
        event('tcp_connected')
        stage = 'response_headers'
        return connection

    def traced_wrap(context, *args, **kwargs):
        nonlocal stage
        stage = 'tls'
        connection = original_wrap(context, *args, **kwargs)
        event('tls_completed', protocol=connection.version())
        stage = 'response_headers'
        return connection

    def received_headers(response):
        result['http_status'] = response.status if hasattr(response, 'status') else response.code
        allowed = {'content-type', 'content-length', 'transfer-encoding', 'connection',
                   'server', 'date', 'x-request-id', 'x-ds-trace-id'}
        result['safe_response_headers'] = {k.lower(): v for k, v in response.headers.items()
                                           if k.lower() in allowed}
        event('response_headers_received', http_status=result['http_status'])

    class BodyTrace:
        def __init__(self, response):
            self.response = response
        def __enter__(self):
            self.response.__enter__()
            return self
        def __exit__(self, *args):
            return self.response.__exit__(*args)
        def read(self):
            nonlocal stage
            stage = 'response_body'
            data = self.response.read()
            event('response_body_completed', byte_count=len(data))
            stage = 'response_json_decode'
            return data

    def traced_open(*args, **kwargs):
        nonlocal stage
        stage = 'response_headers'
        event('request_started')
        try:
            response = original_open(*args, **kwargs)
        except urllib.error.HTTPError as exc:
            received_headers(exc)
            stage = 'http_status_error'
            exc.close()
            raise
        received_headers(response)
        return BodyTrace(response)

    def total_deadline(signum, frame):
        raise TimeoutError(f'diagnostic total deadline exceeded ({config.timeout_seconds}s)')

    previous_handler = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError('diagnostic requires a dedicated process with no active alarm')
    signal.signal(signal.SIGALRM, total_deadline)
    signal.setitimer(signal.ITIMER_REAL, config.timeout_seconds)
    try:
        with patch.object(socket, 'create_connection', traced_connect), \
             patch.object(ssl.SSLContext, 'wrap_socket', traced_wrap), \
             patch.object(urllib.request, 'urlopen', traced_open):
            result['raw_response'] = post_chat_completion(config, payload)
        result['response_received'] = True
    except Exception as exc:
        result.update(failed_stage=stage, error_type=type(exc).__name__, error=str(exc))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    result['elapsed_seconds'] = round(time.monotonic() - started, 4)
    return result


def valid_model_answer(variant, result, payload):
    if not result.get('response_received'):
        return False
    try:
        raw = result['raw_response']
        if variant == 'minimal':
            return json.loads(raw['choices'][0]['message']['content']).get('ok') is True
        context = json.loads(payload['messages'][1]['content'].split('\n\n', 1)[1])
        parse_deepseek_choice(raw, [item['slot'] for item in context['available_slots']],
                              memory_max_chars=400)
        return True
    except (DeepSeekAgentError, KeyError, ValueError, TypeError, IndexError, AttributeError):
        return False


def run_diagnosis(config, full, *, probe=None, on_progress=None):
    variants = prompt_variants(full)
    report = dict(started_utc=datetime.now(timezone.utc).isoformat(), max_http_calls=3,
        scope='Independent synthetic request, no Session writes, no retries',
        model=config.model, timeout_seconds=config.timeout_seconds, records=[])
    invoke = probe or probe_request
    queue = ['minimal', 'full']
    for variant in queue:
        payload = variants[variant]
        record = invoke(config, payload)
        record.update(variant=variant,
            valid_model_answer=valid_model_answer(variant, record, payload),
            request_bytes=len(json.dumps(payload, ensure_ascii=False).encode('utf-8')))
        report['records'].append(record)
        if variant == 'full' and report['records'][0]['valid_model_answer'] and not record['valid_model_answer']:
            queue.append('compact')
        if on_progress:
            on_progress(deepcopy(report))
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-api', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not args.real_api:
        parser.error('No calls made; --real-api is required')
    import settings
    config = config_from_session(settings.DYNAMIC_BOTTLENECK_ROUND_COMMON)
    if not config.api_key:
        parser.error('No calls made; existing API key is missing')
    source = Path(__file__).resolve().parents[1] / 'output/llm_prompt_20261015/api_comparison.json'
    saved = json.loads(source.read_text(encoding='utf-8'))
    full = next(record['request_payload'] for record in saved['records']
                if record['prompt_version'] == 'dynamic_bottleneck_prompt_v2' and record['round_number'] == 1)
    if full['model'] != config.model:
        parser.error('No calls made; model differs from saved synthetic request; review before comparing')
    # Change only this independent diagnostic object's timeout, not settings.py.
    config = replace(config, timeout_seconds=45)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        def save(report):
            output.seek(0)
            output.write(json.dumps(report, ensure_ascii=False, indent=2))
            output.truncate()
            output.flush()
        def progress(report):
            save(report)
            last = report['records'][-1]
            print(json.dumps({key: last.get(key) for key in (
                'variant', 'http_status', 'failed_stage', 'elapsed_seconds',
                'response_received', 'valid_model_answer', 'error')}, ensure_ascii=False), flush=True)
        report = run_diagnosis(config, full, on_progress=progress)
        save(report)
    print(f'Saved phased diagnostic report: {args.output}', flush=True)


if __name__ == '__main__':
    main()
