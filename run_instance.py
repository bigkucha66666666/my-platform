"""Prepare and run isolated I0/I1 instances; import no oTree before exec."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys

from instance_config import (acquire_instance_lock, build_environment, load_pair,
                             prepare_pair, verify_runtime)


def init_local(config, root):
    root = Path(root)
    if not root.is_absolute():
        raise ValueError('--root must be absolute and outside the source tree')
    root = root.resolve()
    raw = {'version': 1, 'mode': 'local-test', 'instances': {}}
    for number in (0, 1):
        base = root / f'i{number}'
        raw['instances'][f'i{number}'] = dict(port=8001 + number,
            runtime_dir=str(base / 'runtime'), state_dir=str(base / 'state'),
            tmp_dir=str(base / 'state/tmp'), journal_path=str(base / 'state/journal.sqlite3'),
            database_url=f'sqlite:///{base}/runtime/db.sqlite3', secret_key=secrets.token_urlsafe(48),
            rest_key=secrets.token_urlsafe(48), admin_password=secrets.token_urlsafe(18),
            environment={'DYNAMIC_ROOM_RELEASE_PROTOCOL': '2',
                         'DYNAMIC_ROOM_RELEASE_DRAIN_SECONDS': '10',
                         'DEEPSEEK_AGENT_API_KEY': 'local-test-no-paid-provider',
                         'DEEPSEEK_AGENT_BASE_URL': 'http://127.0.0.1:9'})
    config = Path(config).absolute()
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(raw, stream, indent=2)
        stream.write('\n')
    load_pair(config)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init-local')
    init.add_argument('config')
    init.add_argument('--root', required=True)
    for command in ('validate', 'prepare', 'run'):
        child = sub.add_parser(command)
        child.add_argument('config')
        if command == 'run':
            child.add_argument('instance', choices=['i0', 'i1'])
    args = parser.parse_args(argv)
    try:
        if args.command == 'init-local':
            init_local(args.config, args.root)
            print('Private local-test config created; no provider credentials inherited.')
            return
        pair = load_pair(args.config)
        if args.command == 'prepare':
            prepare_pair(pair, Path(__file__).resolve().parent)
        elif args.command == 'run':
            manifests = [verify_runtime(instance) for instance in pair.values()]
            if manifests[0] != manifests[1]:
                raise ValueError('I0/I1 runtime releases differ')
            instance = pair[args.instance]
            env = build_environment(pair, args.instance)
            # The FD intentionally survives exec; Popen's close_fds keeps it out
            # of the timeout child. Another launcher fails before oTree setup.
            lock_descriptor = acquire_instance_lock(instance)
            os.chdir(instance.runtime_dir)
            os.execve(sys.executable, [sys.executable, '-B', 'project_prodserver.py',
                      f'127.0.0.1:{instance.port}'], env)
        for instance in pair.values():
            print(f'{instance.instance_id}: 127.0.0.1:{instance.port} / {instance.room_name}')
    except (OSError, ValueError) as exc:
        parser.exit(2, f'Instance configuration/start refused: {exc}\n')


if __name__ == '__main__':
    main()
