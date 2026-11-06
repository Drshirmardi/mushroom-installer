"""Release updates against disposable paths; no real service or runtime changes."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import build
import manager
import updater


class UpdateValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='installer-update-')
        self.base = Path(self.temp.name)
        self.root = self.base / 'tool'
        self.state = self.base / 'private'
        self.launcher = self.base / 'bin/mushroom'
        self.state.mkdir(mode=0o700)
        self.patches = [patch.object(updater, 'ROOT', self.root), patch.object(updater, 'STATE', self.state),
                        patch.object(updater, 'LAUNCHER', self.launcher)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def archive(self, version='v1.0.1'):
        return build.build(self.base / version / 'mushroom-installer.tar.gz', version, 'a' * 40)

    def rewrite(self, archive, transform):
        with tarfile.open(archive) as bundle:
            entries = [(member, bundle.extractfile(member).read()) for member in bundle.getmembers()]
        with tarfile.open(archive, 'w:gz') as bundle:
            for member, data in transform(entries):
                member.size = len(data)
                bundle.addfile(member, io.BytesIO(data))

    def test_manifest_version_and_relocated_cli_match(self):
        archive = self.archive()
        target = self.base / 'unpacked'
        target.mkdir()
        self.assertEqual(updater.unpack(archive, target, 'v1.0.1'), 'v1.0.1')
        result = subprocess.run([sys.executable, '-B', str(target / 'installation/manager.py'), '--version'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'v1.0.1')

    def test_archive_rejects_traversal_links_duplicates_and_extra_files(self):
        for kind in ['traversal', 'link', 'duplicate', 'extra']:
            with self.subTest(kind=kind):
                archive = self.archive()
                def transform(entries):
                    member = tarfile.TarInfo('mushroom-installer/../escape' if kind == 'traversal' else 'mushroom-installer/extra')
                    if kind == 'link':
                        member.type, member.linkname = tarfile.SYMTYPE, '/etc/passwd'
                    if kind == 'duplicate':
                        member, data = entries[0]
                    else:
                        data = b'bad'
                    return [*entries, (member, data)]
                self.rewrite(archive, transform)
                with self.assertRaises(updater.UpdateError):
                    updater.install_archive(archive)
                self.assertFalse((self.root / 'current').exists())
                self.assertFalse((self.root / 'escape').exists())

    def test_content_and_expected_version_are_verified_before_activation(self):
        archive = self.archive()
        with self.assertRaises(updater.UpdateError):
            updater.install_archive(archive, 'v1.0.2')
        self.rewrite(archive, lambda entries: [(m, b'changed' if m.name.endswith('manager.py') else d) for m, d in entries])
        with self.assertRaises(updater.UpdateError):
            updater.install_archive(archive)
        self.assertFalse((self.root / 'current').exists())

    def test_check_only_never_installs(self):
        with patch.object(updater, 'release_info', return_value={'tag_name': 'v1.0.1'}), patch.object(updater, 'install_archive') as install:
            self.assertEqual(updater.update(check=True), 'v1.0.1')
        install.assert_not_called()

    def test_download_failure_and_bad_checksum_leave_existing_tool(self):
        self.root.mkdir()
        old = self.root / 'preserve'
        old.write_bytes(b'old')
        name = 'mushroom-installer.tar.gz'
        prefix = f'https://github.com/{updater.REPOSITORY}/releases/download/v1.0.1/'
        release = {'tag_name': 'v1.0.1', 'assets': [{'name': n, 'browser_download_url': prefix + n} for n in [name, name + '.sha256']]}
        with patch.object(updater, 'release_info', return_value=release), patch.object(updater, 'download', side_effect=OSError):
            with self.assertRaises(OSError):
                updater.update()
        with patch.object(updater, 'release_info', return_value=release), patch.object(updater, 'download', side_effect=[b'bad', b'0' * 64 + b'  mushroom-installer.tar.gz']):
            with self.assertRaises(updater.UpdateError):
                updater.update()
        self.assertEqual(old.read_bytes(), b'old')

    def test_panel_decline_and_offline_results_are_cached(self):
        with patch.object(updater, 'release_info', return_value={'tag_name': 'v1.0.2'}) as request, patch('builtins.input', return_value='n'):
            updater.panel_offer(self.base)
            updater.panel_offer(self.base)
            self.assertEqual(request.call_count, 1)
        (self.state / 'installer-update-check.json').unlink()
        with patch.object(updater, 'release_info', side_effect=OSError) as request:
            updater.panel_offer(self.base)
            updater.panel_offer(self.base)
            self.assertEqual(request.call_count, 1)

    def test_untrusted_version_and_manifest_paths_are_rejected(self):
        for value in ['latest', 'v1.0.1/../../x', 'v1.0.1;id', 'v1.0.1-rc', None]:
            with self.assertRaises(updater.UpdateError):
                updater.stable(value)


@unittest.skipUnless(os.name == 'posix', 'POSIX activation and flock')
class PosixUpdateTests(UpdateValidationTests):
    def test_local_bundle_persistence_uses_versioned_installation(self):
        source = self.base / 'manual'
        source.mkdir()
        updater.unpack(self.archive(), source)
        updater.install_local(source)
        self.assertEqual(updater.current_version(), 'v1.0.1')
        self.assertEqual((self.root / 'current/installation/install.sh').stat().st_mode & 0o777, 0o755)

    def test_automated_status_does_not_check_network(self):
        (self.state / 'installation.json').write_text(json.dumps({'version': 1, 'environments': {'dev': {}}}))
        with patch.object(manager, 'STATE', self.state), patch.object(manager.os, 'geteuid', return_value=0), \
                patch.object(sys, 'argv', ['mushroom', 'status', '--environment', 'dev']), \
                patch.object(manager, 'compose_command', return_value=['docker', 'compose']), patch.object(manager, 'run'), \
                patch.object(updater, 'release_info') as request:
            manager.main()
        request.assert_not_called()

    def test_legacy_upgrade_repeat_and_rollback_preserve_private_state(self):
        (self.root / 'installation').mkdir(parents=True)
        (self.root / 'installation/manager.py').write_bytes(b'legacy')
        secret = self.state / 'ga4_api_secret'
        secret.write_bytes(b'private-synthetic-value')
        secret.chmod(0o600)
        runtime = self.state / 'runtime.env'
        runtime.write_bytes(b'CUSTOM=retained\n')
        self.assertEqual(updater.install_archive(self.archive()), 'v1.0.1')
        self.assertEqual(updater.install_archive(self.archive()), 'v1.0.1')
        updater.install_archive(self.archive('v1.0.2'))
        self.assertEqual(os.readlink(self.root / 'previous'), 'releases/v1.0.1')
        updater.install_archive(self.archive())
        self.assertEqual(updater.current_version(), 'v1.0.1')
        self.assertEqual(secret.read_bytes(), b'private-synthetic-value')
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(runtime.read_bytes(), b'CUSTOM=retained\n')
        self.assertEqual((self.root / 'installation/manager.py').read_bytes(), b'legacy')

    def test_failed_startup_never_switches_current(self):
        updater.install_archive(self.archive())
        with patch.object(updater.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            with self.assertRaises(updater.UpdateError):
                updater.install_archive(self.archive('v1.0.2'))
        self.assertEqual(updater.current_version(), 'v1.0.1')

    def test_failed_launcher_write_restores_previous_activation(self):
        updater.install_archive(self.archive())
        original = updater.write
        failed = False
        def fail_once(path, *args, **kwargs):
            nonlocal failed
            if path == self.launcher and not failed:
                failed = True
                raise OSError('simulated write failure')
            return original(path, *args, **kwargs)
        with patch.object(updater, 'write', side_effect=fail_once):
            with self.assertRaises(OSError):
                updater.install_archive(self.archive('v1.0.2'))
        self.assertEqual(updater.current_version(), 'v1.0.1')

    def test_symlink_root_and_launcher_are_rejected(self):
        outside = self.base / 'outside'
        outside.mkdir()
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(updater.UpdateError):
            updater.install_archive(self.archive())
        self.root.unlink()
        self.launcher.parent.mkdir()
        self.launcher.symlink_to(outside / 'victim')
        with self.assertRaises(updater.UpdateError):
            updater.install_archive(self.archive())
        self.assertFalse((outside / 'victim').exists())

    def test_updater_uses_same_lock_as_manager(self):
        with patch.object(manager, 'STATE', self.state), manager.lock():
            with self.assertRaises(updater.UpdateError):
                with updater.operation_lock():
                    self.fail('Concurrent operation entered')

    def test_explicit_downgrade_needs_confirmation(self):
        updater.install_archive(self.archive('v1.0.2'))
        with patch.object(updater, 'release_info', return_value={'tag_name': 'v1.0.1'}), patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaises(updater.UpdateError):
                updater.update('v1.0.1')


if __name__ == '__main__':
    unittest.main()
