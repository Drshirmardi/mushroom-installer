"""Real PostgreSQL/age drill, run only by the disposable Docker fixture runner."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import manager


@unittest.skipUnless(os.environ.get('INSTALLATION_RESTORE_TEST') == '1', 'requires the isolated PostgreSQL Docker fixture')
class PostgresRestoreTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('INSTALLATION_RESTORE_NETWORK'), 'requires the real Docker client fixture')
    def test_real_docker_client_passes_database_to_pg_restore(self):
        target_name = 'mushroom_docker_restore_test'
        source_name = 'mushroom_source_installation_test'
        network = os.environ['INSTALLATION_RESTORE_NETWORK']
        host = 'installer-db'
        password = 'restore-disposable-fixture'
        config = {'environments': {'production': {'home': '', 'pgMajor': '16'}}}
        with tempfile.TemporaryDirectory(prefix='mushroom-docker-restore-') as directory:
            root = Path(directory)
            config['environments']['production']['home'] = str(root)
            original = root / 'mushroom/production/secrets/database_url'
            original.parent.mkdir(parents=True)
            original.write_text(f'postgresql://postgres:{password}@{host}/{source_name}?sslmode=prefer')
            key = root / 'backup-keys/production.key'
            key.parent.mkdir()
            subprocess.run(['age-keygen', '-o', key], check=True, capture_output=True)
            target = root / 'target-url'
            manager.atomic_write(target, f'postgresql://postgres:{password}@{host}/{target_name}?sslmode=prefer')
            original_path = manager.Path

            def fixture_path(value):
                return root / 'backups' if str(value) == '/var/backups/mushroom' else original_path(value)

            client = manager.pg_client

            def fixture_client(config, environment, url=None):
                return client(config, environment, url, network=network)

            with patch.object(manager, 'STATE', root), patch.object(manager, 'Path', side_effect=fixture_path), \
                 patch.object(manager, 'pg_client', side_effect=fixture_client), \
                 patch.object(manager, 'local_database', return_value=False), \
                 patch.object(manager, 'private_file', side_effect=Path), \
                 patch.object(manager, 'database_url', side_effect=lambda value, **_: value):
                with client(config, 'production', network=network) as (command, connection):
                    manager.run(command + ['psql', *connection, '-v', 'ON_ERROR_STOP=1', '-c',
                                'CREATE TABLE docker_restore_probe (value int); INSERT INTO docker_restore_probe VALUES (17);'],
                                capture=True, sensitive=True)
                    try:
                        manager.backup(config, 'production')
                        archive = next((root / 'backups/production').glob('*.dump.age'))
                        manager.restore(config, 'production', archive, target)
                        with client(config, 'production', target.read_text(), network=network) as (command, connection):
                            rows = manager.run(command + ['psql', *connection, '-Atc', 'SELECT value FROM docker_restore_probe'],
                                               capture=True, sensitive=True)
                        self.assertEqual(rows.strip(), '17')
                        with self.assertRaisesRegex(manager.InstallationError, 'not empty'):
                            manager.restore(config, 'production', archive, target)
                        corrupt_url = target.read_text().replace(target_name, 'mushroom_corrupt_restore_test')
                        manager.atomic_write(target, corrupt_url)
                        archive.write_bytes(archive.read_bytes()[:-1])
                        with self.assertRaisesRegex(manager.InstallationError, 'authentication failed'):
                            manager.restore(config, 'production', archive, target)
                        with client(config, 'production', corrupt_url, network=network) as (command, connection):
                            self.assertEqual(manager.object_count(command, connection), 0)
                        with client(config, 'production', network=network) as (command, connection):
                            rows = manager.run(command + ['psql', *connection, '-Atc', 'SELECT value FROM docker_restore_probe'],
                                               capture=True, sensitive=True)
                        self.assertEqual(rows.strip(), '17')
                    finally:
                        with client(config, 'production', network=network) as (command, connection):
                            manager.run(command + ['psql', *connection, '-c', 'DROP TABLE docker_restore_probe'],
                                        capture=True, sensitive=True)



if __name__ == '__main__':
    unittest.main(verbosity=2)
