"""The opt-in launcher must preserve installed oTree prodserver semantics."""
import unittest
import os
import sys
from pathlib import Path
from unittest.mock import patch, Mock

import project_prodserver


class ProjectProdserverTests(unittest.TestCase):
    def test_standard_setup_precedes_timeout_worker_with_production_flag(self):
        events = []

        def setup():
            self.assertEqual(os.environ.get('USE_TIMEOUT_WORKER'), '1')
            events.append('setup')

        with (
            patch.dict(os.environ, {'USE_TIMEOUT_WORKER': ''}),
            patch('project_prodserver.setup', side_effect=setup, create=True),
            patch('project_prodserver.subprocess.Popen',
                  side_effect=lambda *args, **kwargs: (events.append('timeout') or Mock())),
            patch('project_prodserver.Config'),
            patch('project_prodserver.Server'),
        ):
            project_prodserver.main(['127.0.0.1:19999'])
        self.assertEqual(events, ['setup', 'timeout'])

    def test_single_worker_and_timeout_subprocess_are_preserved(self):
        with (
            patch('project_prodserver.setup', create=True),
            patch('project_prodserver.subprocess.Popen') as timeout_worker,
            patch('project_prodserver.Config') as config,
            patch('project_prodserver.Server') as server,
        ):
            project_prodserver.main(['127.0.0.1:19999'])
        timeout_worker.assert_called_once()
        self.assertEqual(timeout_worker.call_args.args[0],
                         [sys.executable, str(Path(project_prodserver.__file__).with_name(
                             'project_timeout_worker.py')), '19999'])
        self.assertGreater(len(timeout_worker.call_args.kwargs['env']['PROJECT_TIMEOUT_WORKER_SECRET']), 32)
        self.assertEqual(config.call_args.args[0], 'project_asgi:app')
        self.assertEqual(config.call_args.kwargs['workers'], 1)
        self.assertEqual(config.call_args.kwargs['ws'], 'websockets')
        server.return_value.run.assert_called_once_with()

    def test_child_is_stopped_if_server_raises(self):
        with (patch('project_prodserver.setup'),
              patch('project_prodserver.subprocess.Popen') as worker,
              patch('project_prodserver.Config'),
              patch('project_prodserver.Server') as server):
            worker.return_value.poll.return_value = None
            server.return_value.run.side_effect = RuntimeError('fixture')
            with self.assertRaises(RuntimeError):
                project_prodserver.main(['127.0.0.1:19999'])
            worker.return_value.terminate.assert_called_once_with()
            worker.return_value.wait.assert_called_once()


if __name__ == '__main__':
    unittest.main()
