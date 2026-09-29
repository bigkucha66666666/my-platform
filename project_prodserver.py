"""Opt-in oTree production launcher for the room decision-release protocol.

Matches the installed oTree prodserver's single-worker uvicorn settings and
timeout subprocess.  Keep the current service command until browser/load
acceptance establishes that the new protocol meets the experiment threshold.
"""
import os
import subprocess
import sys

from otree.cli.prodserver1of2 import get_addr_port
from uvicorn.main import Config, Server


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        raise SystemExit('用法: python project_prodserver.py [端口或地址:端口]')
    addr, port = get_addr_port(args[0] if args else None)
    subprocess.Popen(
        ['otree', 'timeoutsubprocess', str(port)], env=os.environ.copy()
    )
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


if __name__ == '__main__':
    main()
