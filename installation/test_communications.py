import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import manager


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
