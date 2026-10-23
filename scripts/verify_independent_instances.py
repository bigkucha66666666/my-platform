"""Real entrypoint acceptance using ONLY a newly owned temporary PostgreSQL cluster.

Requires local PostgreSQL binaries and psycopg2 in Python or --driver-path.
No existing DB, .env, server, provider or Git state is changed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import http.cookiejar
import importlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler, HTTPRedirectHandler
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from instance_config import load_pair, prepare_pair
from run_instance import init_local

# Test instrumentation exists only inside this disposable venv, never in the app.
# Observe actual setup in both native entrypoints, without changing their routes.
PROBE = '''
import hashlib,json,os,sys,time
from pathlib import Path
marker = Path.cwd() / 'acceptance-probe.json'
if marker.is_file():
    import otree.main
    original = otree.main.setup
    def observed_setup():
        original()
        from otree.database import engine
        from dynamic_bottleneck_round.agents.prefetch_coordinator import CACHE_ROOT
        role = 'timeout' if 'timeout_worker' in sys.argv[0] else 'web'
        info = dict(pid=os.getpid(), ppid=os.getppid(), database=engine.url.database,
            runtime=str(Path.cwd()), tmp=os.environ['TMPDIR'], cache=str(CACHE_ROOT),
            journal=os.environ['DYNAMIC_ROOM_SYNC_DB'], port=sys.argv[-1],
            timeout_secret_hash=hashlib.sha256(os.environ['PROJECT_TIMEOUT_WORKER_SECRET'].encode()).hexdigest())
        if role == 'web':
            from dynamic_bottleneck_round import sync_runtime as runtime
            journal = runtime.get_journal()
            journal.ensure_round('same_session', 1, expected_codes=['same_participant'],
                now_ts=time.time(), decision_page_index=1, drain_limit_seconds=10,
                information_condition=os.environ['OTREE_INSTANCE_ID'].upper(), allowed_minutes=[474],
                boot_id=runtime.PROCESS_BOOT_ID, release_lead_seconds=3)
            journal.register_shell('same_session',1,'same_participant','a'*24,now_ts=time.time(),page_index=1)
            info['token'] = runtime.sign_control_token('same_session',1,'same_participant','a'*24,1)
        (Path(os.environ['TMPDIR']) / (role + '-probe.json')).write_text(json.dumps(info))
    otree.main.setup = observed_setup
'''


def free_port():
    with closing(socket.socket()) as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def stop_owned(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGCONT)
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def request(instance, path, *, payload=None, rest_key=None, opener=None, extra_headers=None):
    headers = {'otree-rest-key': rest_key or instance.rest_key,
               'Origin': f'http://127.0.0.1:{instance.port}'}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    headers.update(extra_headers or {})
    req = Request(f'http://127.0.0.1:{instance.port}{path}',
                  data=None if payload is None else json.dumps(payload).encode(), headers=headers)
    opener = opener or build_opener(ProxyHandler({}))
    start = time.monotonic()
    try:
        with opener.open(req, timeout=20) as response:
            return response.status, response.read(), time.monotonic() - start
    except HTTPError as exc:
        return exc.code, exc.read(), time.monotonic() - start


def wait_ready(instance, process):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f'{instance.instance_id} exited before ready')
        try:
            if request(instance, '/api/rooms')[0] == 200:
                return
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(.2)
    raise AssertionError('instance startup timeout')


def percentiles(samples):
    ordered = sorted(samples)
    return {name: round(ordered[min(len(ordered)-1, int((len(ordered)-1)*q))], 4)
            for name, q in [('p50_seconds', .5), ('p95_seconds', .95), ('max_seconds', 1)]}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check_admin_cookie(instance, other):
    jar = http.cookiejar.CookieJar()
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
    status, body, _ = request(instance, '/login', opener=opener)
    assert status == 200
    match = re.search(r'name="csrftoken" value="([^"]+)"', body.decode())
    assert match, 'login CSRF field missing'
    data = urlencode(dict(username='admin', password=instance.admin_password, csrftoken=match[1])).encode()
    with opener.open(Request(f'http://127.0.0.1:{instance.port}/login', data=data), timeout=10) as response:
        assert '/login' not in response.url, 'admin login failed'
    assert request(instance, '/create_session', opener=opener)[0] == 200
    cookies = '; '.join(f'{cookie.name}={cookie.value}' for cookie in jar)
    assert request(other, '/create_session', extra_headers={'Cookie': cookies},
                   opener=build_opener(ProxyHandler({}), NoRedirect()))[0] == 302


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-path')
    parser.add_argument('--pg-bin', default='/opt/homebrew/bin')
    parser.add_argument('--label', default='local')
    parser.add_argument('--hold-seconds', type=int, default=0,
                        help='Keep owned services alive briefly for local browser QA (max 300)')
    args = parser.parse_args()
    if not args.label.replace('_', '').replace('-', '').isalnum():
        parser.error('simple evidence label required')
    if not 0 <= args.hold_seconds <= 300:
        parser.error('--hold-seconds must be 0..300')
    output = ROOT / 'output/independent_instances_20261023' / args.label
    output.mkdir(parents=True, exist_ok=False)
    if args.driver_path:
        sys.path.append(str(Path(args.driver_path).resolve()))
    psycopg2 = importlib.import_module('psycopg2')
    processes, streams = [], []
    pg_started = False
    pg_bin = Path(args.pg_bin)
    summary = {'status': 'failed', 'paid_calls': 0}
    with tempfile.TemporaryDirectory(prefix='independent-otree-', dir='/private/tmp') as temporary:
        root = Path(temporary).resolve()
        cluster, pg_port = root / 'pg', free_port()
        try:
            with (output / 'postgres-init.log').open('w') as log:
                subprocess.run([str(pg_bin/'initdb'), '-D', str(cluster), '-A', 'trust',
                    '-U', 'instance_test', '--no-locale', '-E', 'UTF8'], check=True, stdout=log, stderr=log)
            (root/'socket').mkdir()
            subprocess.run([str(pg_bin/'pg_ctl'), '-D', str(cluster), '-l', str(output/'postgres.log'),
                '-o', f'-h 127.0.0.1 -p {pg_port} -k {root}/socket', '-w', 'start'], check=True)
            pg_started = True
            for instance_id in ('i0', 'i1'):
                subprocess.run([str(pg_bin/'createdb'), '-h', '127.0.0.1', '-p', str(pg_port),
                    '-U', 'instance_test', instance_id], check=True)
            config = root/'pair.json'
            init_local(config, root/'instances')
            raw = json.loads(config.read_text())
            raw['mode'] = 'production'
            for instance_id, row in raw['instances'].items():
                row['port'] = free_port()
                row['database_url'] = f'postgresql://instance_test@127.0.0.1:{pg_port}/{instance_id}'
                if args.hold_seconds:
                    row['admin_password'] = f'acceptance-admin-{instance_id}'
            config.write_text(json.dumps(raw))
            pair = load_pair(config)
            prepare_pair(pair, ROOT)
            venv.EnvBuilder(with_pip=False, system_site_packages=True).create(root/'venv')
            python = root/'venv/bin/python'
            site_packages = next((root/'venv/lib').glob('python*/site-packages'))
            (site_packages/'instance_acceptance_probe.py').write_text(PROBE)
            extra = str(Path(args.driver_path).resolve()) + '\n' if args.driver_path else ''
            (site_packages/'instance-acceptance.pth').write_text(extra + 'import instance_acceptance_probe\n')

            for instance_id, instance in pair.items():
                (instance.runtime_dir/'acceptance-probe.json').write_text('{}')
                stream = (output/f'{instance_id}.log').open('w')
                streams.append(stream)
                process = subprocess.Popen([str(python), '-B', str(ROOT/'run_instance.py'),
                    'run', str(config), instance_id], cwd=ROOT, stdout=stream, stderr=stream,
                    start_new_session=True)
                processes.append(process)
                wait_ready(instance, process)

            duplicate = subprocess.run([str(python), '-B', str(ROOT/'run_instance.py'),
                'run', str(config), 'i1'], cwd=ROOT, text=True, capture_output=True, timeout=10)
            assert duplicate.returncode == 2 and 'already running' in duplicate.stderr

            probes = {}
            for instance_id, instance in pair.items():
                deadline = time.monotonic() + 10
                while not (instance.tmp_dir/'timeout-probe.json').exists():
                    assert time.monotonic() < deadline, 'timeout child not observed'
                    time.sleep(.1)
                probes[instance_id] = {role: json.loads((instance.tmp_dir/f'{role}-probe.json').read_text())
                                       for role in ('web', 'timeout')}
                web, child = probes[instance_id].values()
                assert child['ppid'] == web['pid']
                assert web['database'] == child['database'] == instance_id
                assert web['runtime'] == child['runtime'] == str(instance.runtime_dir)
                assert web['cache'] == child['cache'] and web['cache'].startswith(str(instance.tmp_dir))
                assert web['timeout_secret_hash'] == child['timeout_secret_hash']
                assert child['port'] == str(instance.port)
                rooms = json.loads(request(instance, '/api/rooms')[1])
                assert [row['name'] for row in rooms] == [instance.room_name]
                control = {'kind': 'status', 'token': web['token']}
                assert request(instance, '/dynamic-room-sync', payload=control)[0] == 200
                other = pair['i1' if instance_id == 'i0' else 'i0']
                assert request(other, '/dynamic-room-sync', payload=control)[0] == 403
                assert request(other, '/api/rooms', rest_key=instance.rest_key)[0] == 403
                check_admin_cookie(instance, other)

                # These must be rejected before native creation commits any rows.
                create = dict(session_config_name='dynamic_bottleneck_round_prod',
                              num_participants=40, room_name=instance.room_name)
                with closing(psycopg2.connect(instance.database_url)) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT count(*) FROM otree_session')
                        before = cursor.fetchone()[0]
                    for fields in [dict(capacity_information_condition=other.condition),
                                   dict(group_treatment_spec=f'G01:H-{instance.condition};G02:HA-{other.condition}'),
                                   dict(pilot_mode_enabled=1,
                                        pilot_group_spec=f'G01:H-{other.condition},human=1,api=0,rl=0')]:
                        assert request(instance, '/api/sessions', payload=dict(
                            create, modified_session_config_fields=fields))[0] == 400
                    assert request(instance, '/api/sessions', payload=dict(create, room_name=other.room_name))[0] == 400
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT count(*) FROM otree_session')
                        assert cursor.fetchone()[0] == before
                status, body, _ = request(instance, '/api/sessions', payload=create)
                assert status == 200, f'create failed: {status}'
                created = json.loads(body)
                assert created['code']
                with closing(psycopg2.connect(instance.database_url)) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT count(*) FROM otree_participant')
                        assert cursor.fetchone()[0] == 40

            assert probes['i0']['web']['timeout_secret_hash'] != probes['i1']['web']['timeout_secret_hash']
            assert probes['i0']['web']['cache'] != probes['i1']['web']['cache']
            journal_ids = []
            for instance in pair.values():
                with closing(sqlite3.connect(instance.journal_path)) as connection:
                    journal_ids.append(connection.execute("SELECT value FROM sync_runtime_metadata WHERE key='journal_id'").fetchone()[0])
            assert journal_ids[0] != journal_ids[1]

            # Real DB lock blocks I0 native business work, while I1 uses another DB/queue.
            i0, i1 = pair.values()
            samples = []
            with closing(psycopg2.connect(i0.database_url)) as lock:
                with lock.cursor() as cursor:
                    cursor.execute('LOCK TABLE otree_session IN ACCESS EXCLUSIVE MODE')
                with ThreadPoolExecutor(max_workers=1) as executor:
                    blocked = executor.submit(request, i0, '/api/sessions')
                    try:
                        time.sleep(.3)
                        assert not blocked.done(), 'I0 did not block on the controlled DB lock'
                        for _ in range(20):
                            status, _, elapsed = request(i1, '/api/sessions')
                            assert status == 200
                            samples.append(elapsed)
                        assert not blocked.done(), 'I0 unexpectedly bypassed DB lock'
                    finally:
                        lock.rollback()
                    assert blocked.result()[0] == 200
            os.killpg(processes[0].pid, signal.SIGSTOP)
            try:
                assert request(i1, '/api/sessions')[0] == 200
            finally:
                os.killpg(processes[0].pid, signal.SIGCONT)
            stop_owned(processes[0])
            assert request(i1, '/api/rooms')[0] == 200
            # A real restart must retain the protective anomaly in its own journal.
            stream = (output/'i0-restarted.log').open('w')
            streams.append(stream)
            restarted = subprocess.Popen([str(python), '-B', str(ROOT/'run_instance.py'),
                'run', str(config), 'i0'], cwd=ROOT, stdout=stream, stderr=stream,
                start_new_session=True)
            processes.append(restarted)
            wait_ready(i0, restarted)
            for instance in (i0, i1):
                with closing(sqlite3.connect(instance.journal_path)) as connection:
                    record = json.loads(connection.execute(
                        "SELECT record FROM sync_rounds WHERE session_code='same_session' AND round_number=1").fetchone()[0])
                assert record['state']['technical_anomaly'] == (
                    'worker_restarted_during_round' if instance.instance_id == 'i0' else '')
            summary.update(status='passed', instances=2, humans_created_per_instance=40,
                native_business_block_isolated=True, stopped_instance_isolated=True,
                child_environment_verified=True, colliding_control_identity_isolated=True,
                conditions_rejected_before_creation=True, journal_ids_distinct=True,
                admin_cookie_isolated=True, same_instance_restart_protected=True,
                duplicate_launch_rejected_before_setup=True,
                i1_while_i0_blocked=percentiles(samples), sample_count=len(samples),
                limits='No 80x33 full flow, browser cookie/path/WS proxy, paid LLM or production capacity acceptance')
            if args.hold_seconds:
                targets = {key: f'http://127.0.0.1:{instance.port}' for key, instance in pair.items()}
                (output/'browser-targets.json').write_text(json.dumps(targets, indent=2))
                print('BROWSER_QA_TARGETS ' + json.dumps(targets), flush=True)
                deadline = time.monotonic() + args.hold_seconds
                while time.monotonic() < deadline:
                    time.sleep(1)
        finally:
            for process in processes:
                stop_owned(process)
            for stream in streams:
                stream.close()
            if pg_started:
                subprocess.run([str(pg_bin/'pg_ctl'), '-D', str(cluster), '-m', 'fast', '-w', 'stop'], check=True)
            (output/'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
