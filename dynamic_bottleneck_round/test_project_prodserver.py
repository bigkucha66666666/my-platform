"""The opt-in launcher must preserve installed oTree prodserver semantics."""
import unittest
from unittest.mock import patch

import project_prodserver


class ProjectProdserverTests(unittest.TestCase):
    def test_single_worker_and_timeout_subprocess_are_preserved(self):
        with (
            patch('project_prodserver.subprocess.Popen') as timeout_worker,
            patch('project_prodserver.Config') as config,
            patch('project_prodserver.Server') as server,
        ):
            project_prodserver.main(['127.0.0.1:19999'])
        timeout_worker.assert_called_once()
        self.assertEqual(timeout_worker.call_args.args[0],
                         ['otree', 'timeoutsubprocess', '19999'])
        self.assertEqual(config.call_args.args[0], 'project_asgi:app')
        self.assertEqual(config.call_args.kwargs['workers'], 1)
        self.assertEqual(config.call_args.kwargs['ws'], 'websockets')
        server.return_value.run.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
