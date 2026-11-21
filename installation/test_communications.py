import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import manager
import communications


@unittest.skipUnless(hasattr(os, 'chown'), 'Linux installer')
class CommunicationsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {'environments': {'dev': {'domains': {
            'app': 'app.example.test', 'dashboard': 'dashboard.example.test',
        }, 'basePort': 3300}}}
        self.runtime = self.root / 'runtime.env'
        self.runtime.write_text(f'MUSHROOM_ENV_ROOT={self.root}\nCOMPOSE_PROJECT_NAME=mushroom-dev\n'
                                'EJABBERD_SQL_PASSWORD=retained-fixture\n')
        self.legacy_overlay = self.root / 'chat.compose.yaml'
        self.legacy_overlay.write_text('legacy fixture\n')
        self.profile = self.root / 'profile.env'
        self.profile.write_text(
            'CHAT_VIDEO_IMAGE=ghcr.io/example/mirotalk@sha256:' + '0' * 64 + '\n'
            'CHAT_VIDEO_ORIGIN=https://calls.example.test\n'
            'CHAT_TURN_SECRET=' + 'a' * 43 + '\n'
            'CHAT_STUN_URL=stun:turn.example.test:3478\n'
            'CHAT_TURN_URLS=turn:turn.example.test:3478?transport=udp\n'
            'CHAT_S3_ENDPOINT=https://objects.example.test\n'
            'CHAT_S3_BUCKET=fixture-communications\n'
            'CHAT_S3_ACCESS_KEY=fixture-access\n'
            'CHAT_S3_SECRET_KEY=fixture-secret\n')
        self.profile.chmod(0o600)
        self.owner = (os.getuid(), os.getgid())

    def test_prepares_separate_overlay_without_rotating_credentials(self):
        with patch.object(manager, 'run', return_value='') as run:
            manager.prepare_communications(self.config, 'dev', root=self.root, owner=self.owner, profile=self.profile)
            first = manager.load_env(self.runtime)
            manager.prepare_communications(self.config, 'dev', root=self.root, owner=self.owner)
        self.assertEqual(manager.load_env(self.runtime), first)
        self.assertEqual(first['CHAT_VIDEO_ENABLED'], 'false')
        self.assertEqual(first['CHAT_VIDEO_EMBED_ORIGINS'], 'https://app.example.test,https://dashboard.example.test')
        self.assertEqual(first['EJABBERD_SQL_PASSWORD'], 'retained-fixture')
        self.assertEqual(self.legacy_overlay.read_text(), 'legacy fixture\n')
        self.assertIn('@communications-postgres:', (self.root / 'communications.compose.yaml').read_text())
        run.assert_called_once()

    def test_refuses_to_rekey_retained_volume(self):
        with patch.object(manager, 'run', return_value='mushroom-dev_communications_sql\n'):
            with self.assertRaisesRegex(manager.InstallationError, 'retained volume'):
                manager.prepare_communications(self.config, 'dev', root=self.root, owner=self.owner, profile=self.profile)
        self.assertNotIn('CHAT_DB_PASSWORD', manager.load_env(self.runtime))

    def test_dev_build_mode_and_managed_turn_preserve_secrets(self):
        with self.profile.open('a') as output:
            output.write('CHAT_VIDEO_IMAGE_MODE=build\nCHAT_TURN_MANAGED=true\n'
                         'CHAT_TURN_PUBLIC_IP=8.8.8.8\nCHAT_TURN_LISTEN_PORT=3478\n'
                         'CHAT_TURN_MIN_PORT=49160\nCHAT_TURN_MAX_PORT=49200\n')
        self.profile.write_text(self.profile.read_text().replace(
            'ghcr.io/example/mirotalk@sha256:' + '0' * 64, 'mushroom-dev-mirotalk:pending'))
        with patch.object(manager, 'run', return_value=''):
            manager.prepare_communications(self.config, 'dev', root=self.root, owner=self.owner, profile=self.profile)
            first = manager.load_env(self.runtime)
            manager.prepare_communications(self.config, 'dev', root=self.root, owner=self.owner)
        self.assertEqual(manager.load_env(self.runtime), first)
        turn = (self.root / 'communications/turnserver.conf').read_text()
        self.assertIn('static-auth-secret=' + 'a' * 43, turn)
        self.assertIn('no-tls', turn)
        self.assertNotIn('pkey=', turn)

    def test_invalid_turn_range_does_not_change_runtime(self):
        original = self.runtime.read_text()
        with self.profile.open('a') as output:
            output.write('CHAT_TURN_MANAGED=true\nCHAT_TURN_PUBLIC_IP=8.8.8.8\n'
                         'CHAT_TURN_LISTEN_PORT=3478\nCHAT_TURN_MIN_PORT=49160\nCHAT_TURN_MAX_PORT=3478\n')
        with patch.object(manager, 'run', return_value=''):
            with self.assertRaisesRegex(manager.InstallationError, 'TURN listener'):
                manager.prepare_communications(self.config, 'dev', root=self.root, owner=self.owner, profile=self.profile)
        self.assertEqual(self.runtime.read_text(), original)

    def test_environment_lock_works_before_first_deployment_and_rejects_overlap(self):
        with communications.environment_lock(manager, self.root):
            with self.assertRaisesRegex(manager.InstallationError, 'Deployment or recovery'):
                with communications.environment_lock(manager, self.root):
                    self.fail('lock overlap')

    def test_storage_probe_always_attempts_cleanup_after_failed_read(self):
        with patch.object(communications, 's3_request', side_effect=[b'', ValueError('get failed'), b'']) as request:
            with self.assertRaisesRegex(ValueError, 'get failed'):
                communications.check_storage({})
        self.assertEqual([call.args[1] for call in request.call_args_list], ['PUT', 'GET', 'DELETE'])

    def test_storage_probe_rejects_anonymously_readable_object(self):
        values = {'CHAT_S3_ENDPOINT': 'https://objects.example.test', 'CHAT_S3_BUCKET': 'private-communications'}
        with patch.object(communications.secrets, 'token_bytes', return_value=b'probe'), \
                patch.object(communications, 's3_request', side_effect=[b'', b'probe', b'']) as signed, \
                patch.object(communications, 'urlopen'):
            with self.assertRaisesRegex(ValueError, 'anonymous object reads'):
                communications.check_storage(values)
        self.assertEqual(signed.call_args.args[1], 'DELETE')

    def test_private_profile_cannot_override_unrelated_runtime_keys(self):
        with self.profile.open('a') as output:
            output.write('DATABASE_URL=postgresql://unexpected\n')
        with self.assertRaisesRegex(manager.InstallationError, 'Unknown communications'):
            manager.prepare_communications(self.config, 'dev', root=self.root, owner=self.owner, profile=self.profile)

    def test_guided_setup_resumes_and_keeps_secrets_out_of_receipt(self):
        self.config['sshHost'] = '8.8.8.8'
        values = manager.load_env(self.runtime)
        values['ARVAN_ENDPOINT'] = 'https://objects.example.test'
        values['ARVAN_REGION'] = 'default'
        values['PRODUCT_MEDIA_STORAGE_MODE'] = 'arvancloud'
        self.runtime.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        def answer(label, default='', validator=None, secret=False):
            value = 'fixture-private-access' if secret else default
            return validator(value) if validator else value
        with patch.object(communications, 'root_for', return_value=self.root), \
                patch.object(manager, 'STATE', self.root / 'installer-state'), \
                patch.object(manager, 'ask', side_effect=answer), \
                patch.object(communications, 'free_range', side_effect=[3478, 49160]):
            path = communications.guided_profile(manager, self.config, 'dev')
            supplied = manager.load_env(path)
            self.assertEqual(supplied['CHAT_VIDEO_IMAGE_MODE'], 'build')
            self.assertEqual(supplied['CHAT_S3_REGION'], 'default')
            self.assertEqual(supplied['CHAT_S3_ENDPOINT'], 'https://objects.example.test')
            with patch.object(manager, 'ask', side_effect=AssertionError('Already answered')):
                self.assertEqual(communications.guided_profile(manager, self.config, 'dev'), path)
            record = (manager.STATE / 'communications/dev.json').read_text()
            self.assertNotIn('fixture-private-access', record)
            self.assertNotIn(supplied['CHAT_TURN_SECRET'], record)

    def test_s3_signature_keeps_credentials_out_of_url_and_signs_payload(self):
        values = manager.load_env(self.profile)
        values['CHAT_S3_REGION'] = 'default'
        with patch.object(communications, 'urlopen') as open_request:
            open_request.return_value.__enter__.return_value.read.return_value = b''
            communications.s3_request(values, 'PUT', '.mushroom-check/fixture', b'probe')
        request = open_request.call_args.args[0]
        self.assertEqual(request.full_url, 'https://objects.example.test/fixture-communications/.mushroom-check/fixture')
        self.assertIn('/default/s3/aws4_request', request.get_header('Authorization'))
        self.assertNotIn('fixture-secret', request.get_header('Authorization'))

    def test_call_trial_is_bounded_and_disabling_clears_it(self):
        values = manager.load_env(self.runtime)
        values['CHAT_VIDEO_IMAGE_MODE'] = 'build'
        values['CHAT_VIDEO_IMAGE'] = 'mushroom-dev-mirotalk:pending'
        with patch.object(manager, 'STATE', self.root / 'installer-state'):
            communications.save_call_mode(manager, self.root, 'dev', values, 'trial')
            trial = manager.load_env(self.runtime)
            expiry = communications.datetime.fromisoformat(trial['CHAT_VIDEO_TEST_UNTIL'].replace('Z', '+00:00'))
            self.assertGreater(expiry, communications.datetime.now(communications.timezone.utc))
            self.assertLessEqual(expiry, communications.datetime.now(communications.timezone.utc) + communications.timedelta(minutes=30))
            self.assertEqual(trial['CHAT_VIDEO_ENABLED'], 'false')
            communications.save_call_mode(manager, self.root, 'dev', trial, 'disabled')
            self.assertNotIn('CHAT_VIDEO_TEST_UNTIL', manager.load_env(self.runtime))
        old = communications.fingerprint(manager, values)
        values['CHAT_VIDEO_IMAGE'] = 'mushroom-dev-mirotalk:another-deployment'
        self.assertEqual(communications.fingerprint(manager, values), old)
        values['CHAT_VIDEO_ORIGIN'] = 'https://another.example.test'
        self.assertNotEqual(communications.fingerprint(manager, values), old)

    def test_chat_backup_targets_communications_database_when_configured(self):
        self.config['environments']['dev']['home'] = str(self.root)
        runtime = self.root / 'mushroom/dev/runtime.env'
        runtime.parent.mkdir(parents=True)
        runtime.write_text('CHAT_DB_PASSWORD=fixture\nEJABBERD_SQL_HOST=chat-postgres\n')
        with patch.object(manager, 'run', return_value='communications-container\n') as run:
            with manager.pg_client(self.config, 'dev', service='chat') as (command, connection):
                self.assertEqual(command, ['docker', 'exec', '-i', 'communications-container'])
                self.assertEqual(connection, [
                    '--username', 'mushroom_communications', '--dbname', 'mushroom_communications',
                ])
        self.assertIn('label=com.docker.compose.service=communications-postgres', run.call_args.args[0])

    def test_production_compose_keeps_managed_postgres_with_communications(self):
        production = self.root / 'mushroom/production'
        release = production / 'releases/current'
        release.mkdir(parents=True)
        (production / 'state').mkdir()
        (production / 'state/current').write_text(str(release))
        (release / 'release.env').write_text('CHAT_DB_PASSWORD=fixture\n')
        for path in (release / 'compose.yaml', production / 'network.yaml',
                     production / 'communications.compose.yaml', production / 'chat.compose.yaml',
                     production / 'postgres.compose.yaml'):
            path.touch()
        config = {'environments': {'production': {'home': str(self.root), 'user': 'deploy'}}}
        with patch.object(manager, 'local_database', return_value=True):
            command = manager.compose_command(config, 'production')
        self.assertIn(production / 'network.yaml', command)
        self.assertIn(production / 'communications.compose.yaml', command)
        self.assertIn(production / 'postgres.compose.yaml', command)
        self.assertNotIn(production / 'chat.compose.yaml', command)
