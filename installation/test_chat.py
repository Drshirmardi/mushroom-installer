"""Real RSA/file/proxy behavior; replace only external Docker/service operations."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import manager


@unittest.skipUnless(shutil.which('openssl'), 'Requires OpenSSL')
class ChatPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {'email': 'ops@example.test', 'environments': {
            name: {'user': 'fixture', 'home': str(self.root / name),
                   'domains': {key: f'{name}-{key}.example.test' for key in ('api', 'web', 'app', 'dashboard')}}
            for name in ('dev', 'staging', 'production')}}
        self.calls = []
        self.actual_run = manager.run
        self.failure = None

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, args, **kwargs):
        args = [str(arg) for arg in args]
        self.calls.append(args)
        if args[0] == 'openssl':
            return self.actual_run(args, **kwargs)
        if self.failure and self.failure in args:
            raise manager.InstallationError('Synthetic Docker failure')
        if any('FROM pg_tables' in arg for arg in args):
            return '1\n'
        return ''

    def prepare(self, environment='dev', root=None):
        root = root or self.root / environment
        with patch.object(manager, 'run', side_effect=self.execute), \
                patch.object(manager.os, 'chown', create=True), contextlib.redirect_stdout(io.StringIO()) as output:
            manager.prepare_chat(self.config, environment, root=root, owner=(1000, 1000))
        return output.getvalue()

    def test_new_and_repeated_setup_preserve_real_keys_credentials_and_application_secrets(self):
        root = manager.write_environment(self.config, 'dev', base=self.root / 'dev')
        originals = {p.name: p.read_bytes() for p in (root / 'secrets').iterdir()}
        self.prepare(root=root)
        values = manager.load_env(root / 'runtime.env')
        self.assertEqual(values['EJABBERD_SQL_HOST'], 'chat-postgres')
        self.assertEqual(values['EJABBERD_WEBSOCKET_URL'], 'wss://dev-api.example.test/xmpp-websocket')
        self.assertEqual(json.loads(Path(values['EJABBERD_PUBLIC_KEY_PATH']).read_text())['e'], 'AQAB')
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        self.prepare(root=root)
        self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})
        for name, data in originals.items():
            self.assertEqual((root / 'secrets' / name).read_bytes(), data)
        ups = [c for c in self.calls if 'up' in c]
        self.assertTrue(all(c[-2:] == ['chat-postgres', 'ejabberd'] for c in ups))
        self.assertFalse(any('down' in c or '--remove-orphans' in c for c in self.calls))

    def test_failure_can_resume_without_rotating_initialized_credentials(self):
        root = manager.write_environment(self.config, 'dev', base=self.root / 'dev')
        self.failure = 'up'
        with self.assertRaisesRegex(manager.InstallationError, 'Synthetic'):
            self.prepare(root=root)
        before = (root / 'runtime.env').read_bytes(), (root / 'secrets/ejabberd-private.pem').read_bytes()
        self.failure = None
        self.prepare(root=root)
        self.assertEqual(before, ((root / 'runtime.env').read_bytes(), (root / 'secrets/ejabberd-private.pem').read_bytes()))
        (root / 'secrets/ejabberd-private.pem').unlink()
        with self.assertRaisesRegex(manager.InstallationError, 'restore'):
            self.prepare(root=root)
        self.assertFalse((root / 'secrets/ejabberd-private.pem').exists())

    def test_existing_database_configuration_and_image_are_preserved(self):
        root = manager.write_environment(self.config, 'dev', base=self.root / 'dev')
        with (root / 'runtime.env').open('a') as stream:
            stream.write('EJABBERD_SQL_HOST=postgres\nEJABBERD_SQL_DB=existing_chat\n'
                         'EJABBERD_SQL_USER=existing_chat\nEJABBERD_SQL_PASSWORD=existing-private-value\n'
                         'EJABBERD_IMAGE=registry.example.test/chat@sha256:' + 'a' * 64 + '\n')
        self.prepare(root=root)
        values = manager.load_env(root / 'runtime.env')
        self.assertEqual(values['EJABBERD_SQL_PASSWORD'], 'existing-private-value')
        self.assertEqual(values['EJABBERD_SQL_DB'], 'existing_chat')
        self.assertFalse((root / 'chat.database.yaml').exists())
        self.assertEqual([c for c in self.calls if 'up' in c][0][-1], 'ejabberd')

    def test_mismatched_public_key_is_rejected_without_replacing_private_key(self):
        root = manager.write_environment(self.config, 'dev', base=self.root / 'dev')
        self.prepare(root=root)
        private = (root / 'secrets/ejabberd-private.pem').read_bytes()
        manager.atomic_write(root / 'secrets/ejabberd-public.jwk', '{"kty":"RSA","n":"wrong","e":"AQAB"}')
        with self.assertRaisesRegex(manager.InstallationError, 'do not match'):
            self.prepare(root=root)
        self.assertEqual(private, (root / 'secrets/ejabberd-private.pem').read_bytes())

    def test_retained_database_without_credentials_is_never_reinitialized(self):
        root = manager.write_environment(self.config, 'dev', base=self.root / 'dev')
        with patch.object(manager, 'run', return_value='mushroom-dev_chat_postgres_data\n'), \
                patch.object(manager.os, 'chown', create=True):
            with self.assertRaisesRegex(manager.InstallationError, 'restore the original runtime.env'):
                manager.prepare_chat(self.config, 'dev', root=root, owner=(1000, 1000))
        self.assertNotIn('EJABBERD_SQL_PASSWORD', manager.load_env(root / 'runtime.env'))
        self.assertFalse((root / 'secrets/ejabberd-private.pem').exists())

    def test_production_chat_is_independent_of_external_application_database(self):
        root = manager.write_environment(self.config, 'production', base=self.root / 'production',
                                         db_url='postgresql://app:private@db.example.test/app?sslmode=require')
        before = (root / 'secrets/database_url').read_bytes()
        self.prepare('production', root)
        values = manager.load_env(root / 'runtime.env')
        self.assertEqual(before, (root / 'secrets/database_url').read_bytes())
        self.assertEqual(values['EJABBERD_SQL_HOST'], 'chat-postgres')
        self.assertEqual(values['EJABBERD_SQL_DB'], 'mushroom_chat_production')

    def test_proxy_validates_chat_routes_before_writing_live_files(self):
        root = Path(self.config['environments']['dev']['home']) / 'mushroom/dev'
        manager.write_environment(self.config, 'dev', base=root)
        self.prepare(root=root)
        self.config['environments'] = {'dev': self.config['environments']['dev']}
        directory = self.root / 'caddy'
        directory.mkdir()
        def validate(args, **kwargs):
            if args[0] == 'caddy':
                snippets = list(directory.glob('mushroom-validate-*/chat/dev/chat.caddy'))
                self.assertEqual(len(snippets), 1)
                text = snippets[0].read_text()
                self.assertIn('"https://dev-app.example.test"', text)
                self.assertIn('"https://dev-dashboard.example.test"', text)
                self.assertIn('respond @chat_policy 404', text)
                raise manager.InstallationError('Invalid proxy fixture')
            return ''
        with patch.object(manager, 'run', side_effect=validate):
            with self.assertRaisesRegex(manager.InstallationError, 'Invalid proxy'):
                manager.configure_proxy(self.config, directory)
        self.assertFalse((directory / 'mushroom-chat/dev/chat.caddy').exists())
        self.assertFalse((directory / 'mushroom.caddy').exists())

    @unittest.skipUnless(os.name == 'posix' and shutil.which('docker'), 'Requires the Linux Compose plugin, not a running daemon')
    def test_actual_compose_model_includes_private_database_and_retained_engine_image(self):
        root = manager.write_environment(self.config, 'dev', base=self.root / 'dev')
        original_execute = self.execute
        def execute(args, **kwargs):
            args = [str(v) for v in args]
            if args[0] == 'runuser' and 'config' in args:
                text = self.actual_run(args[4:-2] + ['config', '--format', 'json'], capture=True, sensitive=True)
                model = json.loads(text)
                self.assertEqual(model['services']['ejabberd']['pull_policy'], 'never')
                self.assertEqual(model['services']['ejabberd']['ports'][0]['host_ip'], '127.0.0.1')
                self.assertEqual(model['services']['ejabberd']['environment']['EJABBERD_MACRO_SQL_HOST'], 'chat-postgres')
                self.assertNotIn('ports', model['services']['chat-postgres'])
                return ''
            return original_execute(args, **kwargs)
        self.execute = execute
        self.prepare(root=root)

    @unittest.skipUnless(os.name == 'posix' and shutil.which('caddy'), 'Requires real Caddy')
    def test_live_proxy_allows_only_exact_browser_origins_and_hides_policy(self):
        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
            def log_message(self, *args):
                pass
        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        spec = self.config['environments']['dev']
        self.config['environments'] = {'dev': spec}
        root = Path(spec['home']) / 'mushroom/dev'
        manager.write_environment(self.config, 'dev', base=root)
        with (root / 'runtime.env').open('a') as stream:
            stream.write(f'EJABBERD_PROXY_PORT={upstream.server_port}\n')
        directory = self.root / 'caddy'
        directory.mkdir()
        with patch.object(manager, 'run', side_effect=self.execute):
            manager.configure_proxy(self.config, directory)
        config = self.root / 'http.caddy'
        config.write_text('{\n admin off\n auto_https off\n}\n' +
                          f'http://127.0.0.1:{port} {{\n import {directory}/mushroom-chat/dev/chat.caddy\n reverse_proxy 127.0.0.1:{upstream.server_port}\n}}\n')
        process = subprocess.Popen(['caddy', 'run', '--config', str(config), '--adapter', 'caddyfile'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def status(path, origin=None):
            request = Request(f'http://127.0.0.1:{port}{path}', headers={'Origin': origin} if origin else {})
            try:
                with urlopen(request, timeout=2) as response:
                    return response.status
            except HTTPError as error:
                return error.code
        try:
            for attempt in range(50):
                try:
                    status('/')
                    break
                except OSError:
                    if attempt == 49:
                        raise
                    time.sleep(.1)
            for origin in ['https://dev-app.example.test', 'https://dev-dashboard.example.test']:
                self.assertEqual(status('/xmpp-websocket', origin), 200)
            for origin in [None, 'https://evil.example.test', 'https://dev-app.example.test.evil.test', 'http://dev-app.example.test']:
                self.assertEqual(status('/xmpp-websocket', origin), 403)
            for path in ['/v1/chat/ejabberd', '/v1/chat/ejabberd/policy']:
                self.assertEqual(status(path, 'https://dev-app.example.test'), 404)
            self.assertEqual(status('/v1/health/ready'), 200)
        finally:
            process.terminate()
            process.wait(timeout=10)
            upstream.shutdown()
            upstream.server_close()


if __name__ == '__main__':
    unittest.main()
