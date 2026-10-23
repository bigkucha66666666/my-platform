"""Instance launch configuration is checked without importing oTree."""
import copy
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InstanceConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='instance-config-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / 'pair.json'
        self.raw = {'version': 1, 'mode': 'local-test', 'instances': {}}
        for number in (0, 1):
            base = self.root / f'i{number}'
            self.raw['instances'][f'i{number}'] = dict(
                port=18001 + number,
                runtime_dir=str(base / 'runtime'),
                state_dir=str(base / 'state'),
                tmp_dir=str(base / 'state/tmp'),
                journal_path=str(base / 'state/journal.sqlite3'),
                database_url=f'sqlite:///{base}/runtime/db.sqlite3',
                secret_key=str(number) * 48,
                rest_key=f'local-rest-{number}-' + str(number) * 40,
                admin_password='local-test-only',
                environment={'DYNAMIC_ROOM_RELEASE_PROTOCOL': '2',
                             'DYNAMIC_ROOM_RELEASE_DRAIN_SECONDS': '10'},
            )

    def load(self):
        from instance_config import load_pair
        self.config.write_text(json.dumps(self.raw))
        self.config.chmod(0o600)
        return load_pair(self.config)

    def test_two_complete_local_instances_and_private_config(self):
        pair = self.load()
        self.assertEqual(pair['i0'].condition, 'I0')
        self.assertEqual(pair['i1'].room_name, 'prod_room_i1')
        self.config.chmod(0o644)
        from instance_config import load_pair
        with self.assertRaisesRegex(ValueError, 'private'):
            load_pair(self.config)

    def test_missing_and_shared_resources_fail_closed(self):
        original = copy.deepcopy(self.raw)
        for field in ['database_url', 'state_dir', 'runtime_dir', 'tmp_dir',
                      'journal_path', 'secret_key', 'rest_key', 'port']:
            with self.subTest(field=field, case='missing'):
                self.raw = copy.deepcopy(original)
                del self.raw['instances']['i0'][field]
                with self.assertRaises(ValueError):
                    self.load()
            with self.subTest(field=field, case='shared'):
                self.raw = copy.deepcopy(original)
                self.raw['instances']['i1'][field] = self.raw['instances']['i0'][field]
                with self.assertRaises(ValueError):
                    self.load()

    def test_path_symlink_and_nested_resources_are_rejected(self):
        i0 = self.root / 'i0'
        i0.mkdir()
        (self.root / 'alias').symlink_to(i0, target_is_directory=True)
        for path in ['runtime_dir', 'state_dir', 'tmp_dir', 'journal_path']:
            with self.subTest(path=path):
                prior = self.raw['instances']['i1'][path]
                self.raw['instances']['i1'][path] = self.raw['instances']['i0'][path].replace('/i0/', '/alias/')
                with self.assertRaises(ValueError):
                    self.load()
                self.raw['instances']['i1'][path] = prior
        self.raw['instances']['i1']['runtime_dir'] = str(i0 / 'runtime/nested')
        with self.assertRaises(ValueError):
            self.load()

    def test_postgresql_target_ignores_different_credentials(self):
        self.raw['mode'] = 'production'
        a, b = self.raw['instances'].values()
        a['database_url'] = 'postgresql://first:secret@localhost/db_a'
        b['database_url'] = 'postgresql://second:other@127.0.0.1:5432/db_a'
        with self.assertRaisesRegex(ValueError, 'database'):
            self.load()
        b['database_url'] = 'postgresql://second:other@localhost/db_b'
        self.load()
        b['database_url'] += '?dbname=db_a'
        with self.assertRaises(ValueError):
            self.load()

    def test_database_aliases_cannot_reuse_same_database_name(self):
        self.raw['mode'] = 'production'
        a, b = self.raw['instances'].values()
        a['database_url'] = 'postgresql://first:secret@localhost/db_a'
        for alias in ['localhost.', '127.0.0.2', 'database-alias.example']:
            with self.subTest(alias=alias):
                b['database_url'] = f'postgresql://second:other@{alias}/db_%61'
                with self.assertRaisesRegex(ValueError, 'database'):
                    self.load()

    def test_one_web_process_per_instance_lock(self):
        from instance_config import acquire_instance_lock
        pair = self.load()
        for instance in pair.values():
            instance.state_dir.mkdir(parents=True, mode=0o700)
        first = acquire_instance_lock(pair['i0'])
        other = acquire_instance_lock(pair['i1'])
        try:
            self.assertTrue(os.get_inheritable(first))
            with self.assertRaisesRegex(ValueError, 'already running'):
                acquire_instance_lock(pair['i0'])
        finally:
            os.close(first)
            os.close(other)
        again = acquire_instance_lock(pair['i0'])
        os.close(again)

    def test_production_never_uses_sqlite_or_implicit_environment(self):
        self.raw['mode'] = 'production'
        with self.assertRaises(ValueError):
            self.load()
        self.raw['mode'] = 'local-test'
        for key in ['DATABASE_URL', 'TMPDIR', 'PYTHONPATH', 'OTREE_INSTANCE_ID']:
            self.raw['instances']['i0']['environment'][key] = 'bad'
            with self.assertRaises(ValueError):
                self.load()
            del self.raw['instances']['i0']['environment'][key]

    def test_clean_launch_environment_and_settings_no_dotenv_fallback(self):
        from instance_config import build_environment, validate_environment
        pair = self.load()
        env = build_environment(pair, 'i0', inherited={
            'PATH': os.environ['PATH'], 'DATABASE_URL': 'postgresql://wrong/production',
            'OTREE_IN_MEMORY': '1', 'PYTHONPATH': '/wrong',
            'DEEPSEEK_AGENT_API_KEY': 'do-not-inherit', 'PROJECT_TIMEOUT_WORKER_SECRET': 'wrong'})
        self.assertEqual(env['DATABASE_URL'], pair['i0'].database_url)
        self.assertNotIn('OTREE_IN_MEMORY', env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertNotIn('DEEPSEEK_AGENT_API_KEY', env)
        self.assertNotIn('PROJECT_TIMEOUT_WORKER_SECRET', env)
        runtime = pair['i0'].runtime_dir
        runtime.mkdir(parents=True)
        validate_environment(env, cwd=runtime)
        env.pop('DATABASE_URL')
        with self.assertRaises(ValueError):
            validate_environment(env, cwd=runtime)

    def test_runtime_copies_only_runtime_inputs_and_detects_changes(self):
        from instance_config import prepare_pair, verify_runtime
        source = self.root / 'source'
        source.mkdir()
        for name in ['settings.py', 'project_prodserver.py', 'private.env', '.env',
                     'db.sqlite3', 'notes.md']:
            (source / name).write_text('secret' if 'env' in name else 'source')
        for dirname in ['_static', '_templates', '_rooms', 'my_app', 'output', 'papers', '.git']:
            directory = source / dirname
            directory.mkdir()
            (directory / 'data.txt').write_text('runtime input')
        (source / 'my_app/__init__.py').write_text('')
        (source / '_static/.env').write_text('secret')
        pair = self.load()
        prepare_pair(pair, source)
        for instance in pair.values():
            self.assertTrue((instance.runtime_dir / '_static/data.txt').is_file())
            for forbidden in ['.env', 'private.env', 'db.sqlite3', 'output', 'papers', '.git', '_static/.env']:
                self.assertFalse((instance.runtime_dir / forbidden).exists(), forbidden)
            verify_runtime(instance)
        with self.assertRaises(FileExistsError):
            prepare_pair(pair, source)
        extra = pair['i1'].runtime_dir / 'unexpected.py'
        extra.write_text('unmanifested source')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            verify_runtime(pair['i1'])
        extra.unlink()
        (pair['i0'].runtime_dir / 'settings.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            verify_runtime(pair['i0'])

    def test_session_condition_checks_all_groups_and_top_level(self):
        from instance_config import validate_session_condition
        config = {'capacity_information_condition': 'I0'}
        groups = {'G01': {'information_condition': 'I0'}, 'G02': {'information_condition': 'I1'}}
        with self.assertRaises(ValueError):
            validate_session_condition(config, groups, condition='I0')
        groups['G02']['information_condition'] = 'I0'
        validate_session_condition(config, groups, condition='I0')
        with self.assertRaises(ValueError):
            validate_session_condition(config, groups, condition='I1')
        validate_session_condition(config, groups, condition='')

    def test_real_settings_skip_dotenv_and_keep_legacy_mode(self):
        from instance_config import build_environment
        pair = self.load()
        project = Path(__file__).resolve().parent
        for instance in pair.values():
            instance.runtime_dir.mkdir(parents=True)
            for name in ['settings.py', 'instance_config.py']:
                shutil.copyfile(project / name, instance.runtime_dir / name)
            (instance.runtime_dir / '.env').write_text('DATABASE_URL=dotenv-must-not-load\n')
            code = ('import json,os,settings; print(json.dumps(dict('
                    'rooms=[r["name"] for r in settings.ROOMS],'
                    'configs=settings.SESSION_CONFIGS,db=os.getenv("DATABASE_URL"))))')
            env = build_environment(pair, instance.instance_id)
            result = subprocess.run([sys.executable, '-B', '-c', code], cwd=instance.runtime_dir,
                env=env, text=True, capture_output=True, check=True)
            data = json.loads(result.stdout)
            self.assertEqual(data['rooms'], [instance.room_name])
            self.assertEqual(data['db'], instance.database_url)
            self.assertEqual(data['configs'][0]['capacity_information_condition'], instance.condition)
            bad = dict(env)
            del bad['DATABASE_URL']
            rejected = subprocess.run([sys.executable, '-B', '-c', 'import settings'],
                cwd=instance.runtime_dir, env=bad, text=True, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)
        legacy = subprocess.run([sys.executable, '-B', '-c', code], cwd=pair['i0'].runtime_dir,
            env={'PATH': os.environ['PATH']}, text=True, capture_output=True, check=True)
        data = json.loads(legacy.stdout)
        self.assertIn('demo_room', data['rooms'])
        self.assertEqual(data['db'], 'dotenv-must-not-load')

    def test_instance_admin_controls_use_fixed_condition(self):
        source = (Path(__file__).resolve().parent /
                  '_templates/otree/includes/DynamicSessionControls.html').read_text()
        self.assertIn('const instanceCondition = ', source)
        self.assertIn('value.endsWith(instanceCondition)', source)

    def test_creation_request_rejects_wrong_room_and_condition(self):
        from instance_session import validate_creation_request
        config = dict(name='dynamic_bottleneck_round_prod', capacity_information_condition='I0',
                      group_treatment_spec='G01:H-I0;G02:HA-I0')
        validate_creation_request(config, room_name='prod_room_i0', condition='I0')
        for room in ['prod_room_i1', 'demo_room']:
            with self.assertRaises(ValueError):
                validate_creation_request(config, room_name=room, condition='I0')
        with self.assertRaises(ValueError):
            validate_creation_request(dict(config, capacity_information_condition='I1'),
                                      room_name=None, condition='I0')

    def test_runtime_source_directory_symlink_is_rejected(self):
        from instance_config import prepare_pair
        source = self.root / 'source'
        source.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / '__init__.py').write_text('')
        (outside / 'secret.txt').write_text('must not copy')
        (source / 'app_alias').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            prepare_pair(self.load(), source)


if __name__ == '__main__':
    unittest.main()
