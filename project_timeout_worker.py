"""Native oTree task loop, with authenticated same-origin HTTP transport only."""
import os
import sys
from urllib.request import install_opener

from timeout_navigation import build_timeout_opener


def install_timeout_transport(port):
    install_opener(build_timeout_opener(port, os.environ.get('PROJECT_TIMEOUT_WORKER_SECRET', '')))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].isdigit() or not 0 < int(args[0]) < 65536:
        raise SystemExit('用法: python project_timeout_worker.py PORT')
    os.environ['USE_TIMEOUT_WORKER'] = '1'
    from otree.main import setup
    setup()
    install_timeout_transport(int(args[0]))
    from otree.tasks import Worker
    Worker(int(args[0])).listen()


if __name__ == '__main__':
    main()
