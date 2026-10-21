"""The isolated acceptance runner must not leave CPU-heavy descendants."""
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_timeout_origin_tests import run_isolated_process
from scripts import run_timeout_origin_tests as runner


@unittest.skipUnless(os.name == 'posix', 'deployment/test hosts are POSIX')
class TestRunnerCleanupTests(unittest.TestCase):
    def test_cli_sigterm_unwinds_and_restores_the_callers_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        def interrupt():
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        with patch.object(runner, '_main', side_effect=interrupt):
            with self.assertRaises(SystemExit) as stopped:
                runner.main()
        self.assertEqual(stopped.exception.code, 143)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_timeout_reaps_owned_child_and_grandchild(self):
        source = '''
import os, signal, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
def stop(*args):
    child.terminate()
    child.wait(timeout=3)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
print(child.pid, flush=True)
time.sleep(30)
'''
        with tempfile.TemporaryDirectory(prefix='owned-test-process-') as directory:
            output = Path(directory, 'child.log')
            with output.open('w') as stream:
                result = run_isolated_process([sys.executable, '-c', source],
                    cwd=directory, env=os.environ.copy(), stream=stream, timeout=.5)
            self.assertEqual(result.returncode, 124)
            self.assertTrue(result.timed_out)
            pid = int(output.read_text().strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_normal_completion_retains_real_exit_code(self):
        with tempfile.TemporaryFile(mode='w+') as stream:
            result = run_isolated_process([sys.executable, '-c', 'raise SystemExit(7)'],
                cwd=Path.cwd(), env=os.environ.copy(), stream=stream, timeout=2)
        self.assertEqual(result.returncode, 7)
        self.assertFalse(result.timed_out)
