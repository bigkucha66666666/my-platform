"""Opt-in oTree production launcher for the room decision-release protocol.

Matches the installed oTree prodserver's single-worker uvicorn settings and
timeout subprocess.  Keep the current service command until browser/load
acceptance establishes that the new protocol meets the experiment threshold.
"""
import os
import subprocess
import sys
import secrets
from pathlib import Path

from otree.cli.prodserver1of2 import get_addr_port
from otree.main import setup
from uvicorn.main import Config, Server


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        raise SystemExit('用法: python project_prodserver.py [端口或地址:端口]')
    # Standard oTree CLI sets this before importing common/database and then
    # initializes ORM, model freezing, logging, i18n and browser-bot runtime.
    os.environ['USE_TIMEOUT_WORKER'] = '1'
    os.environ['PROJECT_TIMEOUT_WORKER_SECRET'] = secrets.token_urlsafe(48)
    setup()
    addr, port = get_addr_port(args[0] if args else None)
    worker = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name('project_timeout_worker.py')), str(port)],
        env=os.environ.copy()
    )
    try:
        config = Config(
            'project_asgi:app',
            host=addr,
            port=int(port),
            log_level='info',
            log_config=None,
            workers=1,
            ws='websockets',
        )
        Server(config=config).run()
    finally:
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)


if __name__ == '__main__':
    main()
