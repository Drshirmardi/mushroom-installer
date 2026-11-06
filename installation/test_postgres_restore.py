"""Real PostgreSQL/age drill, run only by the disposable Docker fixture runner."""
from contextlib import contextmanager, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import manager


@unittest.skipUnless(os.environ.get('INSTALLATION_RESTORE_TEST') == '1', 'requires the isolated PostgreSQL Docker fixture')
class PostgresRestoreTest(unittest.TestCase):
    def test_encrypted_dump_restores_only_to_an_empty_separate_database(self):
        host = 'installer-db'
        source = 'mushroom_source_installation_test'
        target = 'mushroom_restore_test'
        def sql(database, statement):
            return manager.run(['psql', '--host', host, '--username', 'postgres', '--dbname', database,
                                '-At', '-v', 'ON_ERROR_STOP=1', '-c', statement], capture=True, sensitive=True).strip()
        self.assertEqual(sql(source, 'SELECT count(*) FROM pg_tables WHERE schemaname=\'public\''), '0')
        sql(source, "CREATE TABLE installer_fixture (id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, body text NOT NULL); INSERT INTO installer_fixture (body) VALUES ('private fixture'), ('متن آزمایشی')")
        with tempfile.TemporaryDirectory(prefix='mushroom-restore-fixture-') as directory:
            root = Path(directory)
            state = root / 'state'
            (state / 'backup-keys').mkdir(parents=True)
            key = state / 'backup-keys/staging.key'
            subprocess.run(['age-keygen', '-o', key], check=True, capture_output=True)
            environment_root = root / 'home/mushroom/staging'
            (environment_root / 'secrets').mkdir(parents=True)
            source_url = f'postgresql://postgres:fixture@{host}/{source}'
            (environment_root / 'secrets/database_url').write_text(source_url)
            backups = root / 'backups/staging'
            backups.mkdir(parents=True)
            config = {'environments': {'staging': {'home': str(root / 'home')}}}
            @contextmanager
            def client(config, environment, target=None):
                # Replace only the Docker transport; use the real pg_dump/psql/pg_restore clients and server.
                name = urlsplit(target).path[1:] if target is not None else source
                yield [], ['--host', host, '--username', 'postgres', '--dbname', name]
            original_path = manager.Path
            def path(value):
                return root / 'backups' if str(value) == '/var/backups/mushroom' else original_path(value)
            with patch.object(manager, 'STATE', state), patch.object(manager, 'Path', side_effect=path), \
                 patch.object(manager, 'pg_client', side_effect=client), redirect_stdout(io.StringIO()):
                manager.backup(config, 'staging')
                archive = next(backups.glob('*.dump.age'))
                self.assertNotIn(b'private fixture', archive.read_bytes())
                target_url_file = root / 'restore-url'
                manager.atomic_write(target_url_file, f'postgresql://postgres:fixture@{host}/{target}')
                manager.restore(config, 'staging', archive, target_url_file)
                self.assertEqual(sql(target, 'SELECT count(*) FROM installer_fixture'), '2')
                self.assertEqual(sql(target, 'SELECT body FROM installer_fixture WHERE id=2'), 'متن آزمایشی')
                with self.assertRaisesRegex(manager.InstallationError, 'not empty'):
                    manager.restore(config, 'staging', archive, target_url_file)
                self.assertEqual(sql(source, 'SELECT count(*) FROM installer_fixture'), '2')
                manager.atomic_write(target_url_file, f'postgresql://postgres:fixture@{host}/mushroom_corrupt_restore_test')
                archive.write_bytes(archive.read_bytes()[:-10])
                with self.assertRaises(manager.InstallationError):
                    manager.restore(config, 'staging', archive, target_url_file)
                self.assertEqual(sql('mushroom_corrupt_restore_test', "SELECT count(*) FROM pg_tables WHERE schemaname='public'"), '0')


if __name__ == '__main__':
    unittest.main(verbosity=2)
