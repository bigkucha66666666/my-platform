import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SharedPrefetchCoordinatorTests(unittest.TestCase):
    @staticmethod
    def claim_from_child_process(directory):
        script = (
            'from pathlib import Path; '
            'from dynamic_bottleneck_round.agents import prefetch_coordinator as c; '
            'import sys; '
            'c.CACHE_ROOT = Path(sys.argv[1]); '
            "claim = c.try_claim(('SESSION_A', 1, 4)); "
            "print('claimed' if claim else 'waiting'); "
            'c.release_claim(claim)'
        )
        result = subprocess.run(
            [sys.executable, '-c', script, directory],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return result.stdout.strip()

    def test_only_one_claim_exists_until_result_is_published(self):
        from dynamic_bottleneck_round.agents import prefetch_coordinator

        with tempfile.TemporaryDirectory() as directory, patch.object(
            prefetch_coordinator, 'CACHE_ROOT', Path(directory)
        ):
            key = ('SESSION_A', 1, 4)
            first = prefetch_coordinator.try_claim(key)
            self.assertIsNotNone(first)
            self.assertIsNone(prefetch_coordinator.try_claim(key))
            self.assertEqual(self.claim_from_child_process(directory), 'waiting')
            self.assertIsNone(prefetch_coordinator.read_result(key))

            payload = {'status': 'ok', 'choices': [{'departure_slot': 2}]}
            prefetch_coordinator.publish_result(key, payload, first)

            self.assertEqual(prefetch_coordinator.read_result(key), payload)
            self.assertEqual(self.claim_from_child_process(directory), 'claimed')
            second = prefetch_coordinator.try_claim(key)
            self.assertIsNotNone(second)
            prefetch_coordinator.release_claim(second)

    def test_publish_path_failure_releases_claim_for_recovery(self):
        from dynamic_bottleneck_round.agents import prefetch_coordinator

        with tempfile.TemporaryDirectory() as directory, patch.object(
            prefetch_coordinator, 'CACHE_ROOT', Path(directory)
        ):
            key = ('SESSION_FAILURE', 1, 4)
            first = prefetch_coordinator.try_claim(key)
            with patch.object(prefetch_coordinator, '_paths', side_effect=OSError('disk unavailable')):
                with self.assertRaisesRegex(OSError, 'disk unavailable'):
                    prefetch_coordinator.publish_result(key, {'status': 'error'}, first)

            second = prefetch_coordinator.try_claim(key)
            self.assertIsNotNone(second)
            prefetch_coordinator.release_claim(second)

    def test_cleanup_failure_also_releases_claim(self):
        from dynamic_bottleneck_round.agents import prefetch_coordinator

        with tempfile.TemporaryDirectory() as directory, patch.object(
            prefetch_coordinator, 'CACHE_ROOT', Path(directory)
        ):
            key = ('SESSION_CLEANUP_FAILURE', 1, 4)
            first = prefetch_coordinator.try_claim(key)
            with (
                patch.object(prefetch_coordinator.os, 'replace', side_effect=OSError('replace failed')),
                patch.object(Path, 'unlink', side_effect=OSError('cleanup failed')),
            ):
                with self.assertRaises(OSError):
                    prefetch_coordinator.publish_result(key, {'status': 'error'}, first)

            second = prefetch_coordinator.try_claim(key)
            self.assertIsNotNone(second)
            prefetch_coordinator.release_claim(second)

    def test_read_permission_failure_is_treated_as_unavailable(self):
        from dynamic_bottleneck_round.agents import prefetch_coordinator

        with tempfile.TemporaryDirectory() as directory, patch.object(
            prefetch_coordinator, 'CACHE_ROOT', Path(directory)
        ):
            with patch.object(Path, 'open', side_effect=PermissionError('denied')):
                self.assertIsNone(prefetch_coordinator.read_result(('SESSION_DENIED', 1, 4)))

    def test_started_marker_survives_owner_exit_without_result(self):
        from dynamic_bottleneck_round.agents import prefetch_coordinator

        with tempfile.TemporaryDirectory() as directory, patch.object(
            prefetch_coordinator, 'CACHE_ROOT', Path(directory)
        ):
            key = ('SESSION_CRASH', 1, 4)
            first = prefetch_coordinator.try_claim(key)
            prefetch_coordinator.mark_started(first)
            prefetch_coordinator.release_claim(first)

            second = prefetch_coordinator.try_claim(key)
            self.assertTrue(prefetch_coordinator.was_started(second))
            prefetch_coordinator.release_claim(second)

    def test_crashed_process_releases_lock_but_preserves_attempt_marker(self):
        from dynamic_bottleneck_round.agents import prefetch_coordinator

        script = (
            'from pathlib import Path; '
            'from dynamic_bottleneck_round.agents import prefetch_coordinator as c; '
            'import os, sys; '
            'c.CACHE_ROOT = Path(sys.argv[1]); '
            "claim = c.try_claim(('SESSION_CRASHED', 1, 4)); "
            'c.mark_started(claim); '
            'os._exit(0)'
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            prefetch_coordinator, 'CACHE_ROOT', Path(directory)
        ):
            subprocess.run(
                [sys.executable, '-c', script, directory],
                capture_output=True, text=True, check=True, timeout=10,
            )
            claim = prefetch_coordinator.try_claim(('SESSION_CRASHED', 1, 4))
            self.assertIsNotNone(claim)
            self.assertTrue(prefetch_coordinator.was_started(claim))
            prefetch_coordinator.release_claim(claim)

    def test_invalid_json_result_is_treated_as_unavailable(self):
        from dynamic_bottleneck_round.agents import prefetch_coordinator

        with tempfile.TemporaryDirectory() as directory, patch.object(
            prefetch_coordinator, 'CACHE_ROOT', Path(directory)
        ):
            key = ('SESSION_BAD_CACHE', 1, 4)
            _lock_path, result_path = prefetch_coordinator._paths(key)
            result_path.write_text('{bad json', encoding='utf-8')
            self.assertIsNone(prefetch_coordinator.read_result(key))
            result_path.write_text('[]', encoding='utf-8')
            self.assertIsNone(prefetch_coordinator.read_result(key))


if __name__ == '__main__':
    unittest.main()
