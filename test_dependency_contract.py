"""Keep the new-server environment aligned with the validated local baseline."""
from pathlib import Path
import unittest


class DependencyContractTests(unittest.TestCase):
    def test_runtime_versions_are_exact_and_not_a_conda_freeze(self):
        lines = (Path(__file__).parent / 'requirements.txt').read_text().splitlines()
        dependencies = [line.strip() for line in lines
                        if line.strip() and not line.lstrip().startswith('#')]
        self.assertEqual(dependencies, ['-c requirements-lock.txt', 'otree==6.0.13',
                                        'psycopg2-binary==2.9.10'])

    def test_all_runtime_dependencies_are_locked(self):
        lines = (Path(__file__).parent / 'requirements-lock.txt').read_text().splitlines()
        pins = [line.strip() for line in lines if line.strip() and not line.startswith('#')]
        self.assertEqual(len(pins), 15)
        self.assertTrue(all('==' in pin and not any(c in pin for c in '<>*,') for pin in pins))
        self.assertIn('h11==0.16.0', pins)

    def test_new_server_guide_preserves_existing_database(self):
        guide = (Path(__file__).parent / 'docs/新服务器依赖与启动说明_2026-10-19.md').read_text()
        for expected in ['Python 3.10', 'python -m pip check', 'project_prodserver.py',
                         'resetdb', '5.11.5', '6.0.13', '备份', 'OTREE_PRODUCTION=1',
                         'OTREE_SECRET_KEY', 'OTREE_AUTH_LEVEL=STUDY']:
            self.assertIn(expected, guide)

    def test_procfile_uses_one_project_launcher(self):
        self.assertEqual((Path(__file__).parent / 'Procfile').read_text().strip(),
                         'web: python project_prodserver.py')
