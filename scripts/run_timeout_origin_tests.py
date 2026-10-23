"""Run timeout-origin regressions only against disposable local databases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/timeout_origin_repair_20261019'
MODULES = [
    'test_instance_config',
    'dynamic_bottleneck_round.test_release_lead',
    'payment_info.test_rank_payment',
    'dynamic_bottleneck_round.test_result_ranking',
    'dynamic_bottleneck_round.test_adaptive_window',
    'dynamic_bottleneck_round.agents.test_adaptive_pretraining',
    'dynamic_bottleneck_round.test_staged_timing',
    'dynamic_bottleneck_round.test_system_navigation',
    'dynamic_bottleneck_round.test_decision_ingress',
    'dynamic_bottleneck_round.test_room_release',
    'dynamic_bottleneck_round.test_sync_journal',
    'dynamic_bottleneck_round.test_sync_transport',
    'dynamic_bottleneck_round.test_room_sync_framework',
    'dynamic_bottleneck_round.test_room_sync_load',
    'dynamic_bottleneck_round.test_project_prodserver',
    'dynamic_bottleneck_round.tests', 'dynamic_bottleneck_survey.tests',
    'payment_info.test_cash_payment', 'payment_info.test_cash_payment_framework',
    'test_session_data_export', 'test_session_data_export_framework',
    'admin_template_compatibility_tests',
    'test_dependency_contract',
    'test_timeout_test_runner',
]


def _stop_owned_processes(process):
    """Only the new session/group created by this runner, never its caller."""
    def signal_owned(sig):
        try:
            if os.name == 'posix':
                os.killpg(process.pid, sig)
            elif process.poll() is None:
                process.terminate() if sig == signal.SIGTERM else process.kill()
        except ProcessLookupError:
            pass
    signal_owned(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        signal_owned(signal.SIGKILL)
        process.wait(timeout=5)
    finally:
        # The direct child may have exited before its grandchildren did.
        # Their group remains ours even after the leader has been reaped.
        if os.name == 'posix':
            signal_owned(signal.SIGKILL)


def run_isolated_process(command, *, cwd, env, stream, timeout):
    with subprocess.Popen(command, cwd=cwd, env=env, stdout=stream,
                          stderr=subprocess.STDOUT,
                          start_new_session=os.name == 'posix') as process:
        try:
            try:
                return SimpleNamespace(returncode=process.wait(timeout=timeout), timed_out=False)
            except subprocess.TimeoutExpired:
                return SimpleNamespace(returncode=124, timed_out=True)
        finally:
            _stop_owned_processes(process)


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--otree-path')
    parser.add_argument('--full-flow', action='store_true')
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('modules', nargs='*')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.label):
        parser.error('Use a simple local evidence label')
    OUTPUT.mkdir(exist_ok=True)
    if args.baseline:
        release = json.loads((ROOT / 'output/deploy_cash_20261015/manifest.json').read_text())
        actual = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                  for name in release['files']}
        snapshot = OUTPUT / 'before'
        snapshot.mkdir(exist_ok=True)
        for name in actual:
            if (name.startswith('dynamic_bottleneck_round/') or name in {
                'project_asgi.py', 'project_prodserver.py', 'admin_template_compatibility_tests.py'}):
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_bytes((ROOT / name).read_bytes())
        (OUTPUT / 'baseline_manifest.json').write_text(json.dumps(dict(
            files=actual, release_differences=[n for n in actual if actual[n] != release['files'][n]],
            head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()),
            indent=2), encoding='utf-8')
    modules = args.modules or MODULES
    with tempfile.TemporaryDirectory(prefix='timeout-origin-regression-') as directory:
        for entry in ROOT.iterdir():
            if entry.is_dir() and (entry.name.startswith('_') or (entry / '__init__.py').is_file()):
                Path(directory, entry.name).symlink_to(entry, target_is_directory=True)
        paths = [str(ROOT)]
        if args.otree_path:
            target = Path(args.otree_path).resolve()
            if not (target / 'otree/__init__.py').is_file():
                parser.error('Target oTree package is missing')
            paths.insert(0, str(target))
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(paths),
            DATABASE_URL='sqlite:///db.sqlite3', OTREE_IN_MEMORY='', OTREE_PRODUCTION='0',
            OTREE_ADMIN_PASSWORD='isolated-test-only', DYNAMIC_ROOM_RELEASE_PROTOCOL='',
            DYNAMIC_ROOM_SYNC_DB=str(Path(directory, 'journal.sqlite3')),
            DEEPSEEK_AGENT_API_KEY='isolated-no-paid-provider',
            DEEPSEEK_AGENT_BASE_URL='http://127.0.0.1:9',
            RUN_ROOM_SYNC_FULL_FLOW='1' if args.full_flow else '0')
        command = [sys.executable, '-B', '-m', 'unittest', '-v', *modules]
        started = time.monotonic()
        log_path = OUTPUT / f'{args.label}.log'
        with log_path.open('w', encoding='utf-8') as stream:
            result = run_isolated_process(command, cwd=directory, env=env,
                                          stream=stream, timeout=1800)
        metadata = dict(command=command, code=result.returncode,
                        seconds=time.monotonic() - started, otree_path=args.otree_path,
                        timed_out=result.timed_out, process_cleanup='owned isolated process group',
                        full_flow=args.full_flow, isolated_directory=directory)
        (OUTPUT / f'{args.label}.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        print(json.dumps(metadata), flush=True)
        print(log_path.read_text(encoding='utf-8')[-12000:], flush=True)
        return result.returncode


def _stop_signal(signum, frame):
    # SIGTERM otherwise skips Python finally blocks and strands the group.
    raise SystemExit(128 + signum)


def main():
    previous = signal.signal(signal.SIGTERM, _stop_signal)
    try:
        return _main()
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
