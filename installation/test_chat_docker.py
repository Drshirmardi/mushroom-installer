"""Opt-in real engine/SQL rehearsal on a clean Linux CI runner only."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

import manager


@unittest.skipUnless(os.environ.get('INSTALLER_CHAT_DOCKER_TEST') == '1', 'Requires an explicitly isolated Linux Docker runner')
class EngineRehearsal(unittest.TestCase):
    def test_fresh_engine_and_retry_preserve_archive_and_credentials(self):
        import pwd
        self.assertEqual(os.geteuid(), 0)
        user = os.environ['INSTALLER_TEST_USER']
        account = pwd.getpwnam(user)
        owner = (account.pw_uid, account.pw_gid)
        self.assertFalse(manager.run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=mushroom-dev'], capture=True).strip())
        self.assertFalse(manager.run(['docker', 'volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=mushroom-dev'], capture=True).strip())
        with tempfile.TemporaryDirectory(prefix='installer-engine-') as directory:
            os.chown(directory, *owner)
            root = Path(directory) / 'dev'
            config = {'environments': {'dev': {'home': directory, 'user': user, 'domains': {
                key: f'dev-{key}.example.test' for key in ('api', 'web', 'app', 'dashboard')}}}}
            manager.write_environment(config, 'dev', base=root, owner=owner)
            command = ['docker', 'compose', '--project-name', 'mushroom-dev', '--env-file', root / 'runtime.env',
                       '--file', root / 'state/chat.bootstrap.yaml', '--file', root / 'chat.compose.yaml',
                       '--file', root / 'chat.database.yaml', '--file', root / 'network.yaml']
            try:
                manager.prepare_chat(config, 'dev', root=root, owner=owner)
                values = manager.load_env(root / 'runtime.env')
                before = (root / 'runtime.env').read_bytes(), Path(values['EJABBERD_PRIVATE_KEY_PATH']).read_bytes()
                model = json.loads(manager.run(command + ['config', '--format', 'json'], capture=True, sensitive=True))
                self.assertEqual(model['services']['ejabberd']['pull_policy'], 'never')
                self.assertNotIn('ports', model['services']['chat-postgres'])
                pg = command + ['exec', '-T', 'chat-postgres', 'psql', '-U', values['EJABBERD_SQL_USER'], '-d', values['EJABBERD_SQL_DB'], '-At', '-v', 'ON_ERROR_STOP=1', '-c']
                # Engine schema creation proves real engine-to-PostgreSQL configuration, not just container liveness.
                for attempt in range(30):
                    tables = manager.run(pg + ["SELECT count(*) FROM pg_tables WHERE schemaname='public'"], capture=True)
                    if int(tables.strip()) > 0:
                        break
                    time.sleep(2)
                self.assertGreater(int(tables.strip()), 0)
                manager.run(pg + ['CREATE TABLE installer_retention_probe (id integer PRIMARY KEY); INSERT INTO installer_retention_probe VALUES (1)'], capture=True)
                manager.prepare_chat(config, 'dev', root=root, owner=owner)
                self.assertEqual(before, ((root / 'runtime.env').read_bytes(), Path(values['EJABBERD_PRIVATE_KEY_PATH']).read_bytes()))
                self.assertEqual(manager.run(pg + ['SELECT count(*) FROM installer_retention_probe'], capture=True).strip(), '1')
                # A deployment pull must retain the locally built, reviewed engine image.
                manager.run(command + ['pull', 'ejabberd'], capture=True)
            finally:
                if (root / 'state/chat.bootstrap.yaml').exists():
                    subprocess.run([str(v) for v in command + ['down', '--volumes']], check=False, capture_output=True)


if __name__ == '__main__':
    unittest.main()
