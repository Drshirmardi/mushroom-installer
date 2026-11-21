#!/usr/bin/env python3
"""Server preparation and operations. Python standard library only; Ubuntu 24.04."""
import argparse
import base64
import configparser
import stat
from collections import namedtuple
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import getpass
import hashlib
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unicodedata
import updater
import communications
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import Request, urlopen

BUNDLE = Path(__file__).resolve().parent.parent
BUNDLE_FILES = [
    'installation/install.sh', 'installation/manager.py', 'installation/communications.py', 'installation/updater.py', 'installation/README.md',
    'deploy/vps/dev-env.example', 'deploy/vps/env.example', 'deploy/vps/production-env.example',
    'deploy/vps/analytics.dev.env.example', 'config/ANALYTICS.md', 'config/ANALYTICS-ACCEPTANCE.md',
    'deploy/vps/OBJECT-STORAGE.md', 'deploy/vps/sadad-callback.caddy.example', 'deploy/sadad-live-runbook.md',
    'deploy/ejabberd/compose.yaml', 'deploy/ejabberd/compose.database.yaml',
    'deploy/ejabberd/env.example', 'deploy/ejabberd/chat.caddy.example', 'deploy/ejabberd/README.md',
    'deploy/ejabberd/Dockerfile', 'deploy/ejabberd/mod_sabz_chat.erl', 'deploy/ejabberd/ejabberd.yml',
    'scripts/ejabberd-public-key.mjs',
    'deploy/mirotalk/compose.yaml', 'deploy/mirotalk/call.caddy.example',
    'deploy/mirotalk/env.example',
]
STATE = Path('/etc/mushroom')
ENVIRONMENTS = ('dev', 'staging', 'production-sim', 'production')
PORTS = {'dev': 3300, 'staging': 3100, 'production-sim': 3200, 'production': 3000}
SUBNETS = {'dev': 33, 'staging': 31, 'production-sim': 32, 'production': 30}
SECRET_NAMES = (
    'database_url', 'jwt_access_secret', 'refresh_token_pepper', 'otp_pepper',
    'sms_credential_encryption_key', 'sms_credential_decryption_keys',
    'payment_credential_encryption_key', 'payment_credential_decryption_keys',
)
RUNTIME_DEFAULTS = {
    'BALE_AUTH_ENABLED': 'false', 'BALE_BOT_USERNAME': '',
    'PUBLIC_GA4_MEASUREMENT_ID': '', 'PUBLIC_ANALYTICS_CONSENT': 'denied',
    'PUBLIC_ANALYTICS_DEBUG': 'false', 'ANALYTICS_SERVER_ENABLED': 'false',
    'LOG_LEVEL': 'info', 'JWT_ACCESS_ISSUER': 'sabz-zendegi-api',
    'JWT_ACCESS_AUDIENCE': 'sabz-zendegi-frontends',
    'COMMERCE_RESERVATION_TTL_MINUTES': '20', 'BILLING_BOOKING_HOLD_MINUTES': '10',
    'PAYMENT_CREDENTIAL_ENCRYPTION_KEY_VERSION': 'v1',
    'SMS_CREDENTIAL_ENCRYPTION_KEY_VERSION': 'v1', 'SMS_TRANSPORT_MODE': 'disabled',
    'SMS_REQUEST_TIMEOUT_MS': '5000', 'SADAD_TRANSPORT_MODE': 'disabled',
    'SADAD_REQUEST_TIMEOUT_MS': '5000', 'SADAD_REDIRECT_PROFILE': '',
    'PRODUCT_MEDIA_STORAGE_MODE': 'disabled', 'R2_ENDPOINT': '', 'R2_BUCKET': '',
    'R2_REGION': 'auto', 'R2_REQUEST_TIMEOUT_MS': '5000',
    'ARVAN_ENDPOINT': '', 'ARVAN_BUCKET': '', 'ARVAN_REGION': 'default',
    'ARVAN_REQUEST_TIMEOUT_MS': '5000',
}


class InstallationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise InstallationError(message)


def run(args, *, input=None, capture=False, sensitive=False, env=None):
    # No shell interpolation; subprocess errors never echo arguments or secret-bearing output.
    result = subprocess.run([str(arg) for arg in args], input=input, text=True,
                            stdout=subprocess.PIPE if capture or sensitive else None,
                            stderr=subprocess.PIPE if capture or sensitive else None, env=env)
    require(result.returncode == 0, f'{Path(str(args[0])).name} failed (exit {result.returncode}); step remains pending.')
    return result.stdout or ''


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    require(not path.is_symlink(), f'Refusing symlink: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.mushroom-', dir=path.parent)
    try:
        os.fchmod(fd, mode) if hasattr(os, 'fchmod') else None
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_env(path):
    result = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        require('=' in line, f'Malformed setting in {path}')
        key, value = line.split('=', 1)
        require(re.fullmatch(r'[A-Z][A-Z0-9_]*', key) and key not in result, f'Invalid/duplicate setting in {path}')
        result[key] = value.strip().strip('"\'')
    return result


def hostname(value):
    value = value.strip().lower().rstrip('.')
    require(len(value) <= 253 and '.' in value and all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
        for label in value.split('.')), 'Enter a hostname, without scheme, port or path (ASCII/punycode).')
    return value


def database_url(value, production=False):
    require(not any(c.isspace() or c in '$\x00' for c in value), 'Database URL contains unsupported characters; URL-encode credentials.')
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('postgres', 'postgresql') and parsed.hostname
                 and parsed.username and parsed.password and parsed.path not in ('', '/')
                 and not parsed.fragment and (parsed.port or 5432) > 0)
    except ValueError:
        valid = False
    require(valid, 'A complete PostgreSQL URL with encoded username/password and database is required.')
    require(not any(c in unquote(parsed.username + parsed.password + parsed.path) for c in '\r\n\x00'),
            'Database URL contains an encoded control character.')
    if production:
        require(parsed.hostname not in ('postgres', 'localhost', '127.0.0.1', '::1'), 'Production uses an external PostgreSQL service.')
        require(parse_qs(parsed.query).get('sslmode', [''])[0] in ('require', 'verify-ca', 'verify-full'),
                'Production database URL must explicitly enable TLS (sslmode=require/verify-ca/verify-full).')
    return value


def validate_policy(path, environment):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    require(value.get('schemaVersion') == 1 and value.get('policyKey') == 'MEDICAL_INTAKE_PROCESSING'
            and value.get('locale') == 'fa-IR' and type(value.get('versionNumber')) is int
            and value['versionNumber'] > 0 and isinstance(value.get('title'), str)
            and 0 < len(value['title']) <= 255 and isinstance(value.get('body'), str)
            and value['body'].strip() and environment in value.get('allowedEnvironments', []),
            'Consent document is not approved for this environment or has invalid fields.')
    require(hashlib.sha256(value['body'].encode()).hexdigest() == value.get('contentHash'), 'Consent document hash is invalid.')
    return value


def terminal_input(prompt, secret=False):
    while True:
        try:
            answer = (getpass.getpass if secret else input)(prompt)
            # Python's surrogateescape can preserve invalid terminal bytes instead of raising.
            answer.encode('utf-8', errors='strict')
            return answer
        except UnicodeError:
            print('Input could not be read as UTF-8. Set your terminal character encoding to UTF-8 '
                  'and type this answer again; no value was accepted.')


def ask(label, default='', validator=None, secret=False):
    while True:
        suffix = f' [{default}]' if default and not secret else ''
        answer = terminal_input(f'{label}{suffix}: ', secret).strip() or default
        try:
            return validator(answer) if validator else answer
        except (InstallationError, ValueError) as error:
            print(str(error))


def choice(value, options):
    require(value in options, f'Choose one of: {", ".join(options)}')
    return value


def admin_details():
    phone = ask('First administrator phone (+989...)', validator=lambda v: v if re.fullmatch(r'\+989[0-9]{9}', v) else choice(v, []))
    while True:
        password = unicodedata.normalize('NFC', terminal_input('First administrator password (15..128 characters): ', secret=True))
        if 15 <= len(password) <= 128 and password.strip() and not any(unicodedata.category(c) == 'Cc' for c in password):
            if password == unicodedata.normalize('NFC', terminal_input('Repeat password: ', secret=True)):
                break
        print('Passwords must match and meet the password policy.')
    clinic = ask('Clinic name', 'کلینیک سبز زندگی', lambda v: v if 0 < len(v) <= 160 else choice(v, []))
    return {'phone': phone, 'password': password, 'clinicName': clinic, 'timeZone': 'Asia/Tehran'}


def media_configuration(values, root):
    """Collect one dev profile; never put credentials in runtime.env or saved state."""
    values = dict(values)
    root = Path(root)
    require(not root.is_symlink() and not (root / 'secrets').is_symlink(), 'Storage directories cannot be symlinks.')
    previous = values.get('PRODUCT_MEDIA_STORAGE_MODE', 'disabled')
    mode = ask('Dev object storage: disabled / r2 (Cloudflare) / arvancloud (Arvan)', previous,
               lambda v: choice(v, ['disabled', 'r2', 'arvancloud']))
    if previous in ('r2', 'arvancloud') and mode not in (previous, 'disabled'):
        print('Changing provider does not copy existing images. API and cleanup workers must use the same destination.')
        require(ask('Existing images copied at identical keys and media writes/cleanup paused (or no images exist)',
                    'no', lambda v: choice(v, ['yes', 'no'])) == 'yes',
                'Storage configuration unchanged. Prepare the transfer before switching providers.')
    values['PRODUCT_MEDIA_STORAGE_MODE'] = mode
    credentials = {}
    if mode == 'disabled':
        return values, credentials
    prefix = 'R2' if mode == 'r2' else 'ARVAN'

    def endpoint(value):
        parsed = urlsplit(value)
        port = parsed.port  # Validate malformed/out-of-range ports before saving runtime settings.
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
                and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
                and (port is None or port > 0) and not any(c.isspace() for c in value), 'Use an HTTPS S3 endpoint without a bucket path, credentials or query.')
        return value.rstrip('/')

    def credential(value):
        require(value and not any(c.isspace() for c in value), 'Storage credential must be nonempty and contain no whitespace.')
        return value

    values[f'{prefix}_ENDPOINT'] = ask(f'{prefix} S3 HTTPS endpoint (from the bucket region)', values.get(f'{prefix}_ENDPOINT', ''), endpoint)
    values[f'{prefix}_BUCKET'] = ask(f'{prefix} private bucket', values.get(f'{prefix}_BUCKET', ''),
                                    lambda v: v if re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', v) else choice(v, []))
    if mode == 'r2':
        values['R2_REGION'] = 'auto'
    else:
        values['ARVAN_REGION'] = ask('Arvan signing region (official JavaScript default: default)', values.get('ARVAN_REGION', 'default'),
                                     lambda v: v if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', v) else choice(v, []))
    values[f'{prefix}_REQUEST_TIMEOUT_MS'] = ask(f'{prefix} request timeout milliseconds (1000..10000)',
        values.get(f'{prefix}_REQUEST_TIMEOUT_MS', '5000'), lambda v: str(int(v)) if 1000 <= int(v) <= 10000 else choice(v, []))
    for suffix in ('access_key_id', 'secret_access_key'):
        name = f'{prefix.lower()}_{suffix}'
        existing = root / 'secrets' / name
        require(not existing.is_symlink(), 'Storage secret files cannot be symlinks.')
        require(not existing.exists() or existing.is_file(), 'Storage secret path must be a regular file.')
        replace = not existing.exists() or existing.stat().st_size == 0 or ask(f'Replace existing {name}', 'no', lambda v: choice(v, ['yes', 'no'])) == 'yes'
        if replace:
            credentials[name] = ask(name + ' (hidden)', secret=True, validator=credential)
    return values, credentials


def write_media_configuration(root, values, credentials, owner):
    root = Path(root)
    require(not root.is_symlink() and not (root / 'secrets').is_symlink(), 'Storage directories cannot be symlinks.')
    for path in [root / 'runtime.env', *(root / 'secrets' / name for name in credentials)]:
        require(not path.is_symlink() and (not path.exists() or path.is_file()), 'Storage settings must be regular files.')
    # Activate the profile only after its credential files are present.
    for name, value in credentials.items():
        atomic_write(root / 'secrets' / name, value)
        os.chown(root / 'secrets' / name, *owner)
    atomic_write(root / 'runtime.env', ''.join(f'{key}={value}\n' for key, value in values.items()))
    os.chown(root / 'runtime.env', *owner)


def configure_storage(config, environment):
    require(environment == 'dev' and environment in config['environments'], 'Object storage configuration currently supports --environment dev only; production stays disabled.')
    spec = config['environments'][environment]
    root = Path(spec['home']) / 'mushroom' / environment
    require(not (root / 'runtime.env').is_symlink() and (root / 'runtime.env').is_file()
            and (root / 'secrets').is_dir(), 'Finish initial environment preparation first; runtime.env must be a regular file.')
    values, credentials = media_configuration(load_env(root / 'runtime.env'), root)
    owner = (root.stat().st_uid, root.stat().st_gid)
    write_media_configuration(root, values, credentials, owner)
    spec.setdefault('runtime', {}).update({key: value for key, value in values.items()
                                         if key == 'PRODUCT_MEDIA_STORAGE_MODE' or key.startswith(('R2_', 'ARVAN_'))
                                         if not any(word in key for word in ('ACCESS_KEY', 'SECRET'))})
    spec['mediaConfigured'] = True
    save(config)
    print(f"Dev object storage configured: {values['PRODUCT_MEDIA_STORAGE_MODE']}. Credentials remain in secrets/. "
          'Deploy the matching provider-capable application and rerun the shared dev workflow to apply these settings. '
          'No connectivity or upload success is claimed by configuration alone.')


def analytics_secret(value):
    require(0 < len(value) <= 256 and all(33 <= ord(char) <= 126 for char in value),
            'Analytics secret must be 1..256 printable ASCII characters without whitespace.')
    return value


def analytics_configuration(values, root):
    """Controlled dev collection only; credentials never enter public runtime/state."""
    values = dict(values)
    root = Path(root)
    require(not root.is_symlink() and not (root / 'secrets').is_symlink(), 'Analytics directories cannot be symlinks.')
    previous = 'server' if values.get('ANALYTICS_SERVER_ENABLED') == 'true' else (
        'browser' if values.get('PUBLIC_ANALYTICS_CONSENT') == 'granted' else 'disabled')
    mode = ask('Controlled Dev analytics: disabled / browser / server', previous,
               lambda value: choice(value, ['disabled', 'browser', 'server']))
    values.update(PUBLIC_ANALYTICS_CONSENT='denied', PUBLIC_ANALYTICS_DEBUG='false', ANALYTICS_SERVER_ENABLED='false')
    if mode == 'disabled':
        return values, {}
    values['PUBLIC_GA4_MEASUREMENT_ID'] = ask('Dev GA4 Measurement ID (G-...)', values.get('PUBLIC_GA4_MEASUREMENT_ID', ''),
        lambda value: value if re.fullmatch(r'G-[A-Z0-9]{1,62}', value) else choice(value, []))
    values['PUBLIC_ANALYTICS_DEBUG'] = ask('Enable Dev DebugView: true / false', 'true',
        lambda value: choice(value, ['true', 'false']))
    credentials = {}
    if mode == 'server':
        path = root / 'secrets/ga4_api_secret'
        require(not path.is_symlink() and (not path.exists() or path.is_file()), 'Analytics secret must be a regular file.')
        replace = not path.exists() or path.stat().st_size == 0 or ask('Replace existing GA4 API secret', 'no',
            lambda value: choice(value, ['yes', 'no'])) == 'yes'
        if replace:
            credentials['ga4_api_secret'] = ask('GA4 API secret (hidden)', secret=True, validator=analytics_secret)
        else:
            analytics_secret(path.read_text().strip())
        values['GA4_API_SECRET_FILE'] = '/run/optional-secrets/ga4_api_secret'
        values['ANALYTICS_SERVER_ENABLED'] = 'true'
    values['PUBLIC_ANALYTICS_CONSENT'] = 'granted'
    return values, credentials


def configure_analytics(config, environment):
    require(environment == 'dev' and environment in config['environments'], 'Use setup-analytics --environment dev; other environments remain unchanged.')
    root = Path(config['environments'][environment]['home']) / 'mushroom' / environment
    require(not (root / 'runtime.env').is_symlink() and (root / 'runtime.env').is_file()
            and (root / 'secrets').is_dir(), 'Finish initial Dev environment preparation first.')
    values, credentials = analytics_configuration(load_env(root / 'runtime.env'), root)
    owner = (root.stat().st_uid, root.stat().st_gid)
    # Preserve the existing atomic private-file writer and activate only after the secret exists.
    if values.get('ANALYTICS_SERVER_ENABLED') == 'true' and not credentials:
        path = root / 'secrets/ga4_api_secret'
        os.chmod(path, 0o600)
        os.chown(path, *owner)
    write_media_configuration(root, values, credentials, owner)
    save(config)  # No analytics credential is copied into installation.json.
    print('Dev analytics configuration saved. Secret stays in secrets/ga4_api_secret. '
          'Deploy this analytics-capable branch and rerun the Dev workflow to apply settings. '
          'Configuration alone does not verify GA4 receipt.')



def bale_bot_token(value):
    """Validate bot tokens without ever echoing sensitive values."""
    require(bool(re.fullmatch(r'[0-9]{1,20}:[A-Za-z0-9_-]{10,235}', value)) and len(value) <= 256,
            'Bale bot token format is invalid.')
    return value


def bale_webhook_secret(value):
    require(bool(re.fullmatch(r'[A-Za-z0-9_-]{43,256}', value)),
            'Bale webhook secret must be 43..256 URL-safe characters.')
    return value


def bale_username(value):
    value = value.removeprefix('@')
    require(bool(re.fullmatch(r'[A-Za-z0-9_]{3,64}', value)),
            'Bale bot username must contain 3..64 letters, digits or underscores.')
    return value


def bale_public_origin(value):
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme == 'https' and parsed.hostname and parsed.port in (None, 443) and not (
            parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment)
    except ValueError:
        valid = False
    require(valid, 'Bale requires a public HTTPS app/API origin without a path or credentials.')
    return value.rstrip('/')


def validate_bale_runtime(values, root, environment):
    """Validate enabled Bale configuration locally, without logging credential contents."""
    flag = values.get('BALE_AUTH_ENABLED', 'false')
    require(flag in ('true', 'false'), 'Invalid Bale feature flag.')
    if flag == 'false':
        return
    require(environment == 'dev', 'Bale auth must remain disabled outside Dev.')
    root = Path(root)
    directory = root / 'secrets'
    require(root.is_dir() and not root.is_symlink() and directory.is_dir()
            and not directory.is_symlink(), 'Bale private directories are missing or unsafe.')
    app_origin = bale_public_origin(values.get('PUBLIC_APP_URL', ''))
    bale_public_origin(values.get('PUBLIC_API_URL', ''))
    bale_username(values.get('BALE_BOT_USERNAME', ''))
    origins = {origin.strip().rstrip('/') for origin in values.get('APP_ORIGINS', '').split(',')}
    require(app_origin in origins, 'Bale app origin is missing from APP_ORIGINS.')
    for name, variable, validator in (
        ('bale_bot_token', 'BALE_BOT_TOKEN_FILE', bale_bot_token),
        ('bale_webhook_secret', 'BALE_WEBHOOK_SECRET_FILE', bale_webhook_secret),
    ):
        require(values.get(variable) == '/run/optional-secrets/' + name,
                'Bale private-file runtime path is invalid.')
        path = directory / name
        require(path.is_file() and not path.is_symlink(), 'Bale credential file is missing or unsafe.')
        require((path.stat().st_mode & 0o777) == 0o600, 'Bale credential file permissions must be 0600.')
        validator(path.read_text(encoding='utf-8').strip())


def bale_configuration(values, root):
    """Collect private credentials separately; never place them in runtime.env or installation.json."""
    values, root = dict(values), Path(root)
    directory = root / 'secrets'
    require(not root.is_symlink() and directory.is_dir() and not directory.is_symlink(),
            'Bale setup requires an existing private secrets directory.')
    previous = 'enabled' if values.get('BALE_AUTH_ENABLED') == 'true' else 'disabled'
    mode = ask('Bale Mini App: disabled / enabled', previous,
               lambda value: choice(value, ['disabled', 'enabled']))
    if mode == 'disabled':
        values['BALE_AUTH_ENABLED'] = 'false'
        return values, {}
    bale_public_origin(values.get('PUBLIC_APP_URL', ''))
    bale_public_origin(values.get('PUBLIC_API_URL', ''))
    username = ask('Bale bot username (without @)', values.get('BALE_BOT_USERNAME', ''), bale_username)
    token_file, secret_file = directory / 'bale_bot_token', directory / 'bale_webhook_secret'
    for path in (token_file, secret_file):
        require(not path.is_symlink() and (not path.exists() or path.is_file()),
                'Bale credentials must be ordinary files, not symlinks.')
    credentials = {}
    if token_file.exists() and token_file.stat().st_size:
        bale_bot_token(token_file.read_text(encoding='utf-8').strip())
        replace = ask('Replace existing Bale bot token: yes / no', 'no',
                      lambda value: choice(value, ['yes', 'no'])) == 'yes'
    else:
        replace = True
    if replace:
        credentials['bale_bot_token'] = bale_bot_token(ask('Bale bot token (hidden)', secret=True, validator=bale_bot_token))
    if secret_file.exists() and secret_file.stat().st_size:
        bale_webhook_secret(secret_file.read_text(encoding='utf-8').strip())
    else:
        credentials['bale_webhook_secret'] = secrets.token_urlsafe(48)
    values.update(BALE_AUTH_ENABLED='true', BALE_BOT_USERNAME=username,
                  BALE_BOT_TOKEN_FILE='/run/optional-secrets/bale_bot_token',
                  BALE_WEBHOOK_SECRET_FILE='/run/optional-secrets/bale_webhook_secret')
    return values, credentials


def configure_bale(config, environment):
    require(environment == 'dev' and environment in config['environments'],
            'Use setup-bale --environment dev; other environments remain unchanged.')
    root = Path(config['environments'][environment]['home']) / 'mushroom' / environment
    require(not root.is_symlink() and not (root / 'runtime.env').is_symlink()
            and (root / 'runtime.env').is_file() and (root / 'secrets').is_dir(),
            'Prepare the Dev environment before configuring Bale.')
    values, credentials = bale_configuration(load_env(root / 'runtime.env'), root)
    owner = (root.stat().st_uid, root.stat().st_gid)
    if values.get('BALE_AUTH_ENABLED') == 'true':
        for name in ('bale_bot_token', 'bale_webhook_secret'):
            path = root / 'secrets' / name
            if name not in credentials:
                require(path.is_file() and not path.is_symlink(), 'Existing Bale secret is missing or unsafe.')
                os.chmod(path, 0o600)
                os.chown(path, *owner)
    # The existing atomic writer writes private secret files before enabling the public runtime flag.
    write_media_configuration(root, values, credentials, owner)
    save(config)
    if values.get('BALE_AUTH_ENABLED') == 'true':
        print('Dev Bale configuration saved. Set main mini-app URL in Bale BotFather to '
              + bale_public_origin(values['PUBLIC_APP_URL']) + '/bale')
        print('Bot link: https://ble.ir/' + values['BALE_BOT_USERNAME'] + '?startapp')
        print('Deploy Dev before running "sudo mushroom register-bale-webhook --environment dev".'
              ' The bot webhook has NOT been changed.')
    else:
        print('Dev Bale auth disabled; old secrets and any existing remote webhook are preserved.')


def bale_bot_api(token, method, payload=None):
    """Use HTTPS without command-line token arguments or token-bearing exception output."""
    bale_bot_token(token)
    require(method in ('getWebhookInfo', 'setWebhook'), 'Unsupported Bale method.')
    request = Request('https://tapi.bale.ai/bot' + token + '/' + method,
                      data=json.dumps(payload or {}).encode('utf-8'), method='POST',
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=12) as response:
            value = json.loads(response.read(8192))
    except Exception:
        raise InstallationError('Bale API request failed; check connectivity and credentials privately.') from None
    require(isinstance(value, dict) and value.get('ok') is True,
            'Bale API rejected the operation; successful registration is NOT assumed.')
    return value.get('result')


def register_bale_webhook(config, environment):
    """Refuse to override a bot which already has a different webhook target."""
    require(environment == 'dev' and environment in config['environments'],
            'Use register-bale-webhook --environment dev.')
    root = Path(config['environments'][environment]['home']) / 'mushroom' / environment
    require(not root.is_symlink() and not (root / 'runtime.env').is_symlink()
            and (root / 'runtime.env').is_file(), 'Dev runtime is missing.')
    values = load_env(root / 'runtime.env')
    require(values.get('BALE_AUTH_ENABLED') == 'true',
            'Configure and deploy Bale in Dev before registering its webhook.')
    token_file, secret_file = root / 'secrets/bale_bot_token', root / 'secrets/bale_webhook_secret'
    for path in (token_file, secret_file):
        require(path.is_file() and not path.is_symlink(), 'Bale secret is missing or unsafe.')
    token = bale_bot_token(token_file.read_text(encoding='utf-8').strip())
    secret = bale_webhook_secret(secret_file.read_text(encoding='utf-8').strip())
    expected = bale_public_origin(values.get('PUBLIC_API_URL', '')) + '/v1/integrations/bale/webhook/' + secret
    info = bale_bot_api(token, 'getWebhookInfo')
    require(isinstance(info, dict) and isinstance(info.get('url'), str),
            'Bale returned an invalid webhook status.')
    if info['url'] == expected:
        print('Bale Dev webhook already matches; no update made.')
        return
    require(not info['url'], 'Existing Bale webhook belongs to another receiver; refusing to replace it.'
            ' Review the existing bot integration first.')
    require(ask('Register Dev Bale webhook now: yes / no', 'no',
                lambda value: choice(value, ['yes', 'no'])) == 'yes',
            'Cancelled; remote webhook unchanged.')
    require(bale_bot_api(token, 'setWebhook', {'url': expected}) is True,
            'Bale did not acknowledge the webhook registration.')
    confirmed = bale_bot_api(token, 'getWebhookInfo')
    require(isinstance(confirmed, dict) and confirmed.get('url') == expected,
            'Bale webhook verification did not match the configured endpoint.')
    print('Dev Bale webhook registered; no secrets or full webhook URL were printed.'
          ' Test a real requestContact in the Bale client.')


def new_configuration():
    selected = ask('Install dev / production / both', 'both', lambda v: choice(v, ['dev', 'production', 'both']))
    environments = ['dev'] if selected == 'dev' else ['staging', 'production']
    if selected == 'both':
        environments.insert(0, 'dev')
    domain = ask('Main domain', validator=hostname)
    address = ask('Public server IPv4 address (DNS must point here)', validator=lambda v: str(ipaddress.IPv4Address(v)))
    ssh_host = ask('SSH host/IP reachable from GitHub', address,
                   lambda v: v if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', v) else choice(v, []))
    port = ask('SSH port already listening on the server', '22',
               lambda v: str(int(v)) if 1 <= int(v) <= 65535 else choice(v, []))
    repository = ask('Private GitHub repository (owner/name)', 'Drshirmardi/mushroom',
                     lambda v: v if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', v) else choice(v, []))
    approver = ask('GitHub username allowed to promote production', validator=lambda v: v if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', v) else choice(v, []))
    email = ask('Certificate contact email', validator=lambda v: v if re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}', v) else choice(v, []))
    config = {'version': 1, 'domain': domain, 'publicIp': address, 'sshHost': ssh_host,
              'sshPort': port, 'repository': repository, 'approver': approver,
              'email': email, 'environments': {}, 'steps': {}}
    config['productionAutoDeploy'] = (ask('Production updates after successful staging', 'manual',
                                           lambda v: choice(v, ['manual', 'automatic'])) == 'automatic') if 'production' in environments else False
    for environment in environments:
        config['environments'][environment] = environment_configuration(domain, environment)
    check_unique_domains(config)
    return config


def check_unique_domains(config):
    domains = [domain for spec in config['environments'].values() for domain in spec['domains'].values()]
    require(len(domains) == len(set(domains)), 'Environment hostnames must all be distinct.')


def environment_configuration(domain, environment):
    prefix = '' if environment == 'production' else f'{environment}-'
    web = domain if environment == 'production' else f'{environment}.{domain}'
    domains = {name: ask(f'{environment} {name} hostname', default, hostname)
               for name, default in [('web', web), ('app', f'{prefix}app.{domain}'),
                                     ('dashboard', f'{prefix}dashboard.{domain}'), ('api', f'{prefix}api.{domain}')]}
    user = 'mushroom-prod' if environment == 'production' else 'mushroom-deploy'
    sms = ask(f'{environment} SMS runtime (provider credentials are configured later in Dashboard)', 'disabled',
              lambda v: choice(v, ['disabled', 'live']))
    payment = ask(f'{environment} Sadad runtime (live requires registered callback/static outbound IP)', 'disabled',
                  lambda v: choice(v, ['disabled', 'live']))
    runtime = {'SMS_TRANSPORT_MODE': sms, 'SADAD_TRANSPORT_MODE': payment}
    if payment == 'live':
        runtime['SADAD_REDIRECT_PROFILE'] = ask('Merchant-approved Sadad redirect profile', validator=lambda v: choice(v, [
            'PDF_PURCHASE', 'PHP_VPG_PURCHASE', 'DOTNET_PURCHASE_INDEX']))
    backup = ask(f'{environment} enable daily encrypted local backup', 'yes', lambda v: choice(v, ['yes', 'no']))
    retention = ask('Keep local encrypted backups for how many days (7..365)', '14',
                    lambda v: int(v) if 7 <= int(v) <= 365 else choice(v, [])) if backup == 'yes' else 14
    pg_major = ask('PostgreSQL server major version (backup client must match)', '16',
                   lambda v: v if re.fullmatch(r'1[6-9]|[2-9][0-9]', v) else choice(v, [])) if environment == 'production' else '16'
    result = {'domains': domains, 'user': user, 'home': f'/home/{user}',
                                           'runtime': runtime, 'backupEnabled': backup == 'yes', 'pgMajor': pg_major,
                                           'backupRetentionDays': retention}
    if environment == 'production':
        result['postgresMode'] = ask('Production PostgreSQL (external / local)', 'external',
                                     lambda value: choice(value, ('external', 'local')))
        if result['postgresMode'] == 'local':
            result['pgMajor'] = '16'
    return result


def local_database(config, environment):
    return environment != 'production' or config['environments'][environment].get('postgresMode') == 'local'


def write_environment(config, environment, *, base=None, owner=None, first_run=None, db_url=None, policy=None):
    spec = config['environments'][environment]
    root = Path(base) if base else Path(spec['home']) / 'mushroom' / environment
    require(not root.is_symlink(), 'Environment root must not be a symlink.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_dir = root / 'secrets'
    require(not secret_dir.is_symlink() and not (root / 'bootstrap').is_symlink(), 'Environment directories cannot be symlinks.')
    secret_dir.mkdir(exist_ok=True, mode=0o700)
    (root / 'bootstrap').mkdir(exist_ok=True, mode=0o700)
    managed = local_database(config, environment)
    required_names = SECRET_NAMES + (('postgres_password',) if managed else ())
    established = any(path.exists() for path in (secret_dir / '.initialized', root / 'state/current', root / 'bootstrap/complete'))
    if established:
        require(all((secret_dir / name).is_file() and not (secret_dir / name).is_symlink() for name in required_names),
                'An established environment has lost a secret. Restore the original file; automatic regeneration is refused.')
    defaults = dict(RUNTIME_DEFAULTS)
    template = 'dev-env.example' if environment == 'dev' else ('production-env.example' if environment == 'production' else 'env.example')
    values = load_env(BUNDLE / 'deploy/vps' / template)
    values.update(defaults)
    values.update(spec.get('runtime', {}))
    subnet = ipaddress.IPv4Network(spec.get('subnet', f'172.30.{SUBNETS[environment]}.0/24'))
    gateway = str(subnet.network_address + 1)
    values.update({'MUSHROOM_ENVIRONMENT': 'development' if environment == 'dev' else environment,
                   'COMPOSE_PROJECT_NAME': f'mushroom-{environment}', 'MUSHROOM_ENV_ROOT': str(root),
                   'TRUSTED_PROXIES': f'127.0.0.1/32,{gateway}/32'})
    for service, domain in spec['domains'].items():
        values[f'PUBLIC_{service.upper()}_URL'] = f'https://{domain}'
        values[f'{service.upper()}_PORT'] = f'127.0.0.1:{spec.get("basePort", PORTS[environment]) + {"web": 0, "app": 1, "dashboard": 2, "api": 3}[service]}'
    values['APP_ORIGINS'] = ','.join(values[f'PUBLIC_{name}_URL'] for name in ('WEB', 'APP', 'DASHBOARD'))
    values['SADAD_RETURN_URL'] = values['PUBLIC_API_URL'] + '/v1/payments/providers/sadad/callback'
    values['SADAD_RESULT_URL'] = values['PUBLIC_APP_URL'] + '/store/order-success'
    if managed:
        values.update({'POSTGRES_DB': 'mushroom_' + environment.replace('-', '_'),
                       'POSTGRES_USER': 'mushroom_' + environment.replace('-', '_'), 'POSTGRES_IMAGE': f"postgres:{spec.get('pgMajor', '16')}-alpine"})
    if environment == 'dev':
        for unsupported in ('DEV_WEB_MEMORY', 'DEV_APP_MEMORY', 'DEV_DASHBOARD_MEMORY'):
            values.pop(unsupported, None)
        values.update({'DEV_WORKSPACE_PATH': str(root / 'workspace'), 'DEV_CACHE_PATH': str(root / 'cache'),
                       'DEV_UID': str(owner[0]) if owner else '1000', 'DEV_GID': str(owner[1]) if owner else '1000'})
        for directory in ('workspace', 'cache', 'state'):
            (root / directory).mkdir(exist_ok=True, mode=0o700)
    if not (root / 'runtime.env').exists():
        atomic_write(root / 'runtime.env', '# Generated public runtime configuration; secrets are separate.\n' +
                     ''.join(f'{key}={value}\n' for key, value in values.items()))
    else:
        values = load_env(root / 'runtime.env')
        require(values.get('MUSHROOM_ENV_ROOT') == str(root) and values.get('COMPOSE_PROJECT_NAME') == f'mushroom-{environment}', 'Existing environment belongs to a different installation.')
    generated = {name: secrets.token_urlsafe(48) for name in ('jwt_access_secret', 'refresh_token_pepper', 'otp_pepper')}
    for kind in ('sms', 'payment'):
        generated[f'{kind}_credential_encryption_key'] = base64.b64encode(secrets.token_bytes(32)).decode()
        generated[f'{kind}_credential_decryption_keys'] = ''
    if managed:
        password_path = secret_dir / 'postgres_password'
        generated['postgres_password'] = password_path.read_text().strip() if password_path.exists() else secrets.token_hex(32)
        generated['database_url'] = f'postgresql://{values["POSTGRES_USER"]}:{generated["postgres_password"]}@postgres:5432/{values["POSTGRES_DB"]}?schema=public'
    elif not (secret_dir / 'database_url').exists():
        require(db_url is not None, 'Production database URL is required.')
        generated['database_url'] = database_url(db_url, production=True)
    for name, value in generated.items():
        if not (secret_dir / name).exists():
            atomic_write(secret_dir / name, value)
    if not (secret_dir / '.initialized').exists():
        atomic_write(secret_dir / '.initialized', 'version=1\n')
    if first_run and not (root / 'bootstrap/complete').exists() and not (root / 'bootstrap/first-run.json').exists():
        atomic_write(root / 'bootstrap/first-run.json', json.dumps(first_run, ensure_ascii=False))
    if environment == 'production' and policy and not (root / 'consent-policy.json').exists():
        atomic_write(root / 'consent-policy.json', json.dumps(validate_policy(policy, environment), ensure_ascii=False))
    if not (root / 'network.yaml').exists():
        atomic_write(root / 'network.yaml', f'networks:\n  default:\n    ipam:\n      config:\n        - subnet: {subnet}\n          gateway: {gateway}\n')
    if owner:
        # Only installer-owned files; never recurse into application data or an existing workspace.
        for path in [root, secret_dir, root / 'runtime.env', root / 'network.yaml', *secret_dir.iterdir(),
                     *[root / name for name in ('workspace', 'cache', 'state', 'bootstrap', 'bootstrap/first-run.json', 'consent-policy.json') if (root / name).exists()]]:
            require(not path.is_symlink(), f'Refusing symlink: {path}')
            os.chown(path, *owner)
    if environment == 'production' and managed:
        overlay = root / 'postgres.compose.yaml'
        atomic_write(overlay, PRODUCTION_POSTGRES)
        if owner:
            os.chown(overlay, *owner)
    return root


PRODUCTION_POSTGRES = """services:
  postgres:
    image: "${POSTGRES_IMAGE:?set PostgreSQL image}"
    environment:
      POSTGRES_DB: "${POSTGRES_DB:?set database name}"
      POSTGRES_USER: "${POSTGRES_USER:?set database user}"
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
    secrets: [postgres_password]
    volumes: [postgres_data:/var/lib/postgresql/data]
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 3s
      retries: 12
  api:
    depends_on:
      postgres:
        condition: service_healthy
volumes:
  postgres_data:
secrets:
  postgres_password:
    file: "${MUSHROOM_ENV_ROOT:?set environment root}/secrets/postgres_password"
"""



def install_packages():
    engine_absent = shutil.which('docker') is None
    run(['apt-get', 'update'])
    run(['apt-get', 'install', '-y', '--no-install-recommends', 'ca-certificates', 'curl', 'gnupg',
         'jq', 'rsync', 'openssl', 'openssh-server', 'ufw', 'caddy', 'python3'])
    if engine_absent:
        require(not any(shutil.which(name) for name in ('dockerd', 'podman')), 'Existing container runtime needs operator review.')
        Path('/etc/apt/keyrings').mkdir(exist_ok=True, mode=0o755)
        run(['curl', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
             'https://download.docker.com/linux/ubuntu/gpg', '-o', '/etc/apt/keyrings/docker.asc'])
        os.chmod('/etc/apt/keyrings/docker.asc', 0o644)
        architecture = run(['dpkg', '--print-architecture'], capture=True).strip()
        require(architecture == 'amd64', 'Current GitHub release images require amd64.')
        atomic_write('/etc/apt/sources.list.d/docker.sources', 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\nComponents: stable\n' +
                     f'Architectures: {architecture}\nSigned-By: /etc/apt/keyrings/docker.asc\n', 0o644)
        run(['apt-get', 'update'])
        run(['apt-get', 'install', '-y', 'docker-ce', 'docker-ce-cli', 'containerd.io', 'docker-buildx-plugin', 'docker-compose-plugin'])
    if subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode != 0:
        installed = False
        for package in ('docker-compose-plugin', 'docker-compose-v2'):
            policy = run(['apt-cache', 'policy', package], capture=True, env={**os.environ, 'LC_ALL': 'C'})
            if re.search(r'Candidate:\s+(?!\(none\))\S+', policy):
                preview = run(['apt-get', '-s', 'install', package], capture=True, env={**os.environ, 'LC_ALL': 'C'})
                require(not re.search(r'^Remv ', preview, re.M), 'Compose installation would remove existing packages; current Docker engine preserved.')
                run(['apt-get', 'install', '-y', '--no-install-recommends', package])
                installed = True
                break
        require(installed, 'No compatible Compose v2 package available from configured apt repositories; existing engine preserved.')
    run(['docker', 'compose', 'version'], capture=True)
    run(['systemctl', 'enable', '--now', 'docker', 'ssh'])
    if engine_absent:
        configure_docker_logging()
    else:
        print('Existing Docker engine preserved; container log rotation stays with its operator.')


def configure_docker_logging(path=None, *, restart=True):
    """Bound container logs on the engine this installer owns; never rewrite operator ownership."""
    path = Path(path) if path else Path('/etc/docker/daemon.json')
    require(not path.is_symlink(), 'Docker daemon configuration cannot be a symlink.')
    if path.exists():
        print(f'{path} already exists; preserved. Confirm max-size/max-file rotation there.')
        return False
    atomic_write(path, json.dumps({'log-driver': 'json-file',
                                   'log-opts': {'max-size': '10m', 'max-file': '5'}}, indent=2) + '\n', 0o644)
    if restart:
        run(['systemctl', 'restart', 'docker'])
    else:
        print(f'{path} written; restart Docker when it is safe so new containers use the log rotation.')
    return True


def provision_user(name):
    import pwd
    try:
        account = pwd.getpwnam(name)
        require(account.pw_uid > 0 and account.pw_dir == f'/home/{name}', 'Existing deployment user has an unexpected home/UID.')
    except KeyError:
        run(['useradd', '--create-home', '--shell', '/bin/bash', name])
        account = pwd.getpwnam(name)
    run(['usermod', '-aG', 'docker', name])
    return account.pw_uid, account.pw_gid


def ssh_identity(user, owner):
    directory = STATE / 'github' / user
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = directory / 'id_ed25519'
    if not key.exists() and not key.with_suffix('.pub').exists():
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', f'mushroom-actions-{user}', '-f', key])
    require(key.with_suffix('.pub').exists(), 'Incomplete deployment key; restore its public half.')
    public = key.with_suffix('.pub').read_text().strip()
    ssh_dir = Path('/home') / user / '.ssh'
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    require(not ssh_dir.is_symlink(), 'SSH directory cannot be a symlink.')
    authorized = ssh_dir / 'authorized_keys'
    existing = authorized.read_text() if authorized.exists() else ''
    if public not in existing:
        atomic_write(authorized, existing.rstrip() + '\nrestrict ' + public + '\n')
    os.chmod(ssh_dir, 0o700)
    os.chown(ssh_dir, *owner)
    os.chown(authorized, *owner)


def save(config):
    atomic_write(STATE / 'installation.json', json.dumps(config, ensure_ascii=False, indent=2) + '\n')


def allocate_networks(config):
    identifiers = run(['docker', 'network', 'ls', '--quiet'], capture=True).split()
    existing = json.loads(run(['docker', 'network', 'inspect', *identifiers], capture=True)) if identifiers else []
    used = [(entry['Name'], ipaddress.ip_network(item['Subnet'])) for entry in existing
            for item in ((entry.get('IPAM') or {}).get('Config') or []) if item.get('Subnet') and ':' not in item['Subnet']]
    routes = json.loads(run(['ip', '-json', '-4', 'route', 'show'], capture=True))
    used += [('host-route', ipaddress.ip_network(route['dst'], strict=False)) for route in routes
             if route.get('dst') and route['dst'] != 'default' and not route.get('dev', '').startswith(('docker', 'br-'))]
    for environment, spec in config['environments'].items():
        preferred = spec.get('subnet', f'172.30.{SUBNETS[environment]}.0/24')
        candidates = [preferred] + [f'172.30.{number}.0/24' for number in range(40, 251)]
        for candidate in candidates:
            network = ipaddress.ip_network(candidate)
            if not any(network.overlaps(other) for name, other in used if name != f'mushroom-{environment}_default'):
                require('subnet' not in spec or spec['subnet'] == candidate, 'An installed network now overlaps another route. Existing settings preserved; review networking.')
                spec['subnet'] = candidate
                used.append((f'mushroom-{environment}_default', network))
                break
        else:
            raise InstallationError('No unused installation subnet available; review Docker/host routes.')
    save(config)


def allocate_ports(config):
    listeners = run(['ss', '-H', '-ltn'], capture=True)
    used = {int(port) for port in re.findall(r':([0-9]+)\s', listeners)}
    for environment, spec in config['environments'].items():
        root = Path(spec['home']) / 'mushroom' / environment
        if (root / 'runtime.env').exists():
            values = load_env(root / 'runtime.env')
            base = int(values['WEB_PORT'].rsplit(':', 1)[1])
            require(all(values[f'{name}_PORT'] == f'127.0.0.1:{base + offset}' for name, offset in
                        [('WEB', 0), ('APP', 1), ('DASHBOARD', 2), ('API', 3)]), 'Installed port layout is inconsistent; review runtime.env.')
        else:
            base = spec.get('basePort', PORTS[environment])
            while any(port in used for port in range(base, base + 4)):
                base += 100
                require(base + 3 <= 65535, 'No free application port block found.')
        spec['basePort'] = base
        used.update(range(base, base + 4))
    save(config)


def check_dns(config, selected=None):
    missing = []
    for environment, spec in config['environments'].items():
        if selected and environment != selected:
            continue
        for domain in spec['domains'].values():
            try:
                records = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
                addresses = {item[4][0] for item in records}
                # This profile uses direct DNS; unrelated AAAA records can break ACME intermittently.
                if addresses != {config['publicIp']}:
                    missing.append(domain)
            except socket.gaierror:
                missing.append(domain)
    require(not missing, 'DNS pending: set DNS-only A records to ' + config['publicIp'] +
            '; remove unrelated AAAA/proxying for: ' + ', '.join(missing) + '. Then rerun install.')


def wait_for_chat_schema(command, probe, attempts=15, delay=2):
    """Retry the TCP auth/schema probe; a failed exec is retried instead of aborting the step."""
    for attempt in range(attempts):
        try:
            tables = run(command + ['exec', '-T', 'chat-postgres', 'sh', '-c', probe],
                         capture=True, sensitive=True).strip()
        except InstallationError:
            tables = ''
        if tables.isdigit() and int(tables) > 0:
            return int(tables)
        if attempt == attempts - 1:
            raise InstallationError('Chat SQL schema not ready; inspect the engine and rerun setup-chat. Credentials retained.')
        time.sleep(delay)


def prepare_chat(config, environment, *, root=None, owner=None):
    """Prepare the companion engine without touching application databases or rotating keys."""
    spec = config['environments'][environment]
    root = Path(root) if root else Path(spec['home']) / 'mushroom' / environment
    if owner is None:
        import pwd
        account = pwd.getpwnam(spec['user'])
        owner = (account.pw_uid, account.pw_gid)
    values = load_env(root / 'runtime.env')
    require(values.get('MUSHROOM_ENV_ROOT') == str(root) and values.get('COMPOSE_PROJECT_NAME') == f'mushroom-{environment}',
            'Chat environment boundary mismatch.')
    marker = root / 'state/chat.initialized'
    required = ('EJABBERD_SQL_PASSWORD', 'EJABBERD_POLICY_SECRET', 'EJABBERD_PRIVATE_KEY_PATH', 'EJABBERD_PUBLIC_KEY_PATH')
    if marker.exists():
        require(all(values.get(key) for key in required), 'Established chat credentials missing; restore the original settings.')
        require(Path(values['EJABBERD_PRIVATE_KEY_PATH']).is_file(), 'Established chat private key missing; restore its backup.')
    if values.get('EJABBERD_SQL_HOST') and not values.get('EJABBERD_SQL_PASSWORD'):
        raise InstallationError('Existing chat database settings have lost their password; restore the original value.')
    if not values.get('EJABBERD_SQL_PASSWORD'):
        retained = run(['docker', 'volume', 'ls', '--filter', f'name=^mushroom-{environment}_chat_postgres_data$',
                        '--format', '{{.Name}}'], capture=True, sensitive=True)
        require(not retained.strip(), 'Existing chat database volume has no saved credentials; restore the original runtime.env.')
    source = root / 'chat-source'
    assets = ('Dockerfile', 'mod_sabz_chat.erl', 'ejabberd.yml')
    asset_hash = hashlib.sha256(b''.join((BUNDLE / 'deploy/ejabberd' / name).read_bytes() for name in assets)).hexdigest()[:16]
    port = spec.get('basePort', PORTS[environment]) + 4
    if not values.get('EJABBERD_PROXY_PORT'):
        for port in range(port, min(port + 100, 65536)):
            try:
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', port))
                break
            except OSError:
                continue
        else:
            raise InstallationError('No free loopback chat port found.')
    defaults = {
        'EJABBERD_SOURCE_ROOT': str(source), 'EJABBERD_IMAGE': f'mushroom-ejabberd:installer-{asset_hash}',
        'EJABBERD_DOMAIN': spec['domains']['api'],
        'EJABBERD_WEBSOCKET_URL': f'https://{spec["domains"]["api"]}/xmpp-websocket'.replace('https:', 'wss:'),
        'EJABBERD_SQL_HOST': 'chat-postgres', 'EJABBERD_SQL_DB': 'mushroom_chat_' + environment.replace('-', '_'),
        'EJABBERD_SQL_USER': 'mushroom_chat_' + environment.replace('-', '_'),
        'EJABBERD_SQL_PASSWORD': secrets.token_hex(32), 'EJABBERD_POLICY_SECRET': secrets.token_hex(32),
        'EJABBERD_PRIVATE_KEY_PATH': str(root / 'secrets/ejabberd-private.pem'),
        'EJABBERD_PUBLIC_KEY_PATH': str(root / 'secrets/ejabberd-public.jwk'),
        'EJABBERD_API_INTERNAL_PORT': '3003', 'EJABBERD_PROXY_PORT': str(port),
    }
    for key, value in defaults.items():
        if key not in values:
            values[key] = value
        require(values[key] and not any(c in values[key] for c in '\r\n\x00$'), f'Invalid chat setting: {key}')
    require(values['EJABBERD_PROXY_PORT'].isdigit() and 1024 <= int(values['EJABBERD_PROXY_PORT']) <= 65535,
            'Invalid chat proxy port.')
    private = Path(values['EJABBERD_PRIVATE_KEY_PATH'])
    public = Path(values['EJABBERD_PUBLIC_KEY_PATH'])
    require(private.is_absolute() and public.is_absolute() and private != public,
            'Chat keys require distinct absolute paths.')
    require(not private.is_symlink() and not public.is_symlink(), 'Chat keys cannot be symlinks.')
    require(not public.exists() or private.is_file(), 'Chat public key exists without its private key; restore the original private key.')
    private.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.chat-key-', dir=private.parent) as temporary:
        if not private.exists():
            generated = Path(temporary) / 'private.pem'
            run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', generated], sensitive=True)
            atomic_write(private, generated.read_text())
        pem = Path(temporary) / 'public.pem'
        run(['openssl', 'pkey', '-in', private, '-pubout', '-out', pem], sensitive=True)
        modulus = run(['openssl', 'rsa', '-pubin', '-in', pem, '-modulus', '-noout'], capture=True, sensitive=True)
        details = run(['openssl', 'pkey', '-pubin', '-in', pem, '-text', '-noout'], capture=True, sensitive=True)
        match, exponent = re.search(r'Modulus=([0-9A-Fa-f]+)', modulus), re.search(r'Exponent:\s+(\d+)', details)
        require(match and exponent, 'Cannot derive the RSA public key.')
        number = int(match.group(1), 16)
        require(number.bit_length() >= 2048, 'Chat RSA key must be at least 2048 bits.')
        encode = lambda n: base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, 'big')).decode().rstrip('=')
        jwk = {'kty': 'RSA', 'n': encode(number), 'e': encode(int(exponent.group(1))), 'alg': 'RS256', 'use': 'sig'}
        if public.exists():
            existing = json.loads(public.read_text())
            require(all(existing.get(key) == jwk[key] for key in ('kty', 'n', 'e')), 'Existing chat public/private keys do not match; restore the correct pair.')
        else:
            atomic_write(public, json.dumps(jwk) + '\n', 0o644)
    os.chmod(private, 0o600 if environment == 'dev' else 0o640)
    os.chown(private, owner[0], owner[1] if environment == 'dev' else 65532)
    os.chmod(public, 0o644)
    os.chown(public, *owner)
    for name in assets:
        target = source / 'deploy/ejabberd' / name
        if not target.exists():
            atomic_write(target, (BUNDLE / 'deploy/ejabberd' / name).read_text(), 0o644)
    if not (root / 'chat.compose.yaml').exists():
        atomic_write(root / 'chat.compose.yaml', (BUNDLE / 'deploy/ejabberd/compose.yaml').read_text())
    managed_database = values['EJABBERD_SQL_HOST'] == 'chat-postgres'
    if managed_database and not (root / 'chat.database.yaml').exists():
        atomic_write(root / 'chat.database.yaml', (BUNDLE / 'deploy/ejabberd/compose.database.yaml').read_text())
    # Bootstrap only the engine. The placeholder API is never pulled or started.
    bootstrap = root / 'state/chat.bootstrap.yaml'
    atomic_write(bootstrap, 'services:\n  api:\n    image: scratch\n')
    atomic_write(root / 'runtime.env', ''.join(f'{key}={value}\n' for key, value in values.items()))
    for path in [root / 'runtime.env', root / 'chat.compose.yaml', root / 'state', bootstrap, source,
                 *source.rglob('*'), *([root / 'chat.database.yaml'] if managed_database else [])]:
        require(not path.is_symlink(), f'Refusing symlink: {path}')
        os.chown(path, *owner)
    command = ['runuser', '-u', spec['user'], '--', 'docker', 'compose', '--project-name', f'mushroom-{environment}',
               '--env-file', root / 'runtime.env', '--file', bootstrap, '--file', root / 'chat.compose.yaml']
    if managed_database:
        command.extend(['--file', root / 'chat.database.yaml'])
    if (root / 'network.yaml').exists():
        command.extend(['--file', root / 'network.yaml'])
    # This marks retained credentials, not successful engine startup.
    atomic_write(marker, 'version=1\n')
    os.chown(marker, *owner)
    run(command + ['config', '--quiet'], sensitive=True)
    image = values['EJABBERD_IMAGE']
    try:
        run(['docker', 'image', 'inspect', image], capture=True, sensitive=True)
    except InstallationError:
        print(f'{environment}: preparing chat image (first build may take several minutes)...')
        if '@sha256:' in image:
            run(['docker', 'pull', image], sensitive=True)
        else:
            run(command + ['build', 'ejabberd'], sensitive=True)
    services = ['chat-postgres', 'ejabberd'] if managed_database else ['ejabberd']
    run(command + ['up', '-d', '--wait', '--wait-timeout', '180', *services], sensitive=True)
    if managed_database:
        # Check TCP authentication and real engine schema, not only pg_isready/process liveness.
        probe = 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_tables WHERE schemaname=\'public\'"'
        wait_for_chat_schema(command, probe)
    print(f'{environment}: chat engine ready; existing keys and data retained.')


COMMUNICATIONS_INPUTS = {
    'CHAT_VIDEO_IMAGE', 'CHAT_VIDEO_ORIGIN', 'CHAT_TURN_SECRET', 'CHAT_STUN_URL',
    'CHAT_TURN_URLS', 'CHAT_S3_ENDPOINT', 'CHAT_S3_BUCKET', 'CHAT_S3_REGION',
    'CHAT_S3_ACCESS_KEY', 'CHAT_S3_SECRET_KEY',
    'CHAT_VIDEO_IMAGE_MODE', 'CHAT_COMMUNICATIONS_SCHEMA', 'CHAT_TURN_MANAGED',
    'CHAT_TURN_PUBLIC_IP', 'CHAT_TURN_LISTEN_PORT', 'CHAT_TURN_MIN_PORT', 'CHAT_TURN_MAX_PORT',
}

COMMUNICATIONS_OPTIONAL = {
    'CHAT_VIDEO_IMAGE_MODE', 'CHAT_COMMUNICATIONS_SCHEMA', 'CHAT_TURN_MANAGED',
    'CHAT_TURN_PUBLIC_IP', 'CHAT_TURN_LISTEN_PORT', 'CHAT_TURN_MIN_PORT', 'CHAT_TURN_MAX_PORT',
}


def prepare_communications(config, environment, *, root, owner, profile=None, allow_changes=False):
    spec = config['environments'][environment]
    root = Path(root)
    runtime = root / 'runtime.env'
    overlay = root / 'communications.compose.yaml'
    values = load_env(runtime)
    require(values.get('MUSHROOM_ENV_ROOT') == str(root)
            and values.get('COMPOSE_PROJECT_NAME') == f'mushroom-{environment}',
            'Communications environment boundary mismatch.')
    require(not overlay.is_symlink(), 'Refusing symlinked communications overlay.')
    if profile:
        profile = Path(profile)
        require(not profile.is_symlink() and profile.is_file() and profile.stat().st_mode & 0o077 == 0,
                'Communications profile must be a private regular file (mode 0600).')
        supplied = load_env(profile)
        require(supplied.keys() <= COMMUNICATIONS_INPUTS, 'Unknown communications profile setting.')
        for key, value in supplied.items():
            require(allow_changes or values.get(key, value) == value, f'Existing setting preserved: {key}.')
            values[key] = value
    values.setdefault('CHAT_S3_REGION', 'us-east-1')
    for key in sorted(COMMUNICATIONS_INPUTS - COMMUNICATIONS_OPTIONAL):
        require(re.fullmatch(r"""[^\s"'$#\\]+""", values.get(key, '')), f'Missing or invalid communications setting: {key}.')
    image_mode = values.get('CHAT_VIDEO_IMAGE_MODE', 'registry')
    require(image_mode in ('registry', 'build'), 'Invalid communications image mode.')
    require(values.get('CHAT_TURN_MANAGED', 'false') in ('true', 'false'), 'Invalid managed TURN setting.')
    require(values.get('CHAT_COMMUNICATIONS_SCHEMA', '2') == '2', 'Unsupported communications profile schema.')
    if image_mode == 'build':
        require(environment == 'dev' and re.fullmatch(r'mushroom-dev-mirotalk:[a-zA-Z0-9_.-]+', values['CHAT_VIDEO_IMAGE']),
                'Build mode needs a dev MiroTalk source image.')
    else:
        require(re.fullmatch(r'[^@]+@sha256:[a-f0-9]{64}', values['CHAT_VIDEO_IMAGE']),
                'Use an immutable MiroTalk image digest.')
    origin = values['CHAT_VIDEO_ORIGIN']
    require(origin.startswith('https://'), 'MiroTalk requires a dedicated HTTPS origin.')
    host = hostname(origin.removeprefix('https://'))
    require(origin == f'https://{host}' and all(host not in item['domains'].values()
            for item in config['environments'].values()), 'MiroTalk requires a separate HTTPS hostname.')
    require(values['CHAT_S3_ENDPOINT'].startswith('https://'), 'Communications object storage requires HTTPS.')
    require(re.fullmatch(r'[A-Za-z0-9_-]{43,256}', values['CHAT_TURN_SECRET']),
            'Invalid TURN shared secret.')
    if not values.get('CHAT_DB_PASSWORD'):
        require(not overlay.exists(), 'Restore the original communications database password.')
        retained = run(['docker', 'volume', 'ls', '--filter',
                        f'name=^mushroom-{environment}_communications_sql$', '--format', '{{.Name}}'],
                       capture=True, sensitive=True)
        require(not retained.strip(), 'Restore the original communications database password for the retained volume.')
        values['CHAT_DB_PASSWORD'] = secrets.token_urlsafe(32)
    values.setdefault('CHAT_VIDEO_SECRET', secrets.token_urlsafe(32))
    for key in ('CHAT_DB_PASSWORD', 'CHAT_VIDEO_SECRET'):
        require(re.fullmatch(r'[A-Za-z0-9_-]{43,256}', values[key]), f'Invalid communications credential: {key}.')
    values.setdefault('CHAT_VIDEO_ENABLED', 'false')
    if allow_changes:
        values['CHAT_VIDEO_ENABLED'] = 'false'
        values.pop('CHAT_VIDEO_TEST_UNTIL', None)
    if not values.get('CHAT_VIDEO_PROXY_PORT'):
        reserved = set()
        for name, other in config['environments'].items():
            other_runtime = Path(other.get('home', '')) / 'mushroom' / name / 'runtime.env'
            if name != environment and other_runtime.is_file():
                other_values = load_env(other_runtime)
                if other_values.get('CHAT_VIDEO_PROXY_PORT'):
                    reserved.add(int(other_values['CHAT_VIDEO_PROXY_PORT']))
        values['CHAT_VIDEO_PROXY_PORT'] = str(communications.free_range(
            sys.modules[__name__], spec.get('basePort', PORTS[environment]) + 5, 1, occupied=reserved))
    require(values['CHAT_VIDEO_ENABLED'] in ('true', 'false'), 'Invalid video activation setting.')
    port = values['CHAT_VIDEO_PROXY_PORT']
    require(port.isdigit() and 1024 <= int(port) <= 65535, 'Invalid MiroTalk proxy port.')
    values['CHAT_VIDEO_EMBED_ORIGINS'] = ','.join(
        'https://' + spec['domains'][name] for name in ('app', 'dashboard'))
    template = (BUNDLE / 'deploy/mirotalk/compose.yaml').read_text(encoding='utf-8')
    if overlay.exists():
        old_content = overlay.read_text(encoding='utf-8')
        old_hash = hashlib.sha256(old_content.replace('\r\n', '\n').encode()).hexdigest()
        known_legacy_hash = 'b88e9b06787812ee13ca5fb27d994235bad6fa50e6263eff90a2d47326762f04'
        require(old_content.replace('\r\n', '\n') == template.replace('\r\n', '\n')
                or old_hash == known_legacy_hash,
                'Custom communications overlay preserved; compare with the bundled template before setup.')
        if old_content != template:
            atomic_write(overlay.with_suffix('.yaml.before-upgrade'), old_content)
    if values.get('CHAT_TURN_MANAGED') == 'true':
        values['CHAT_TURN_UID'], values['CHAT_TURN_GID'] = map(str, owner)
        values['COMPOSE_PROFILES'] = 'managed-turn'
    elif values.get('COMPOSE_PROFILES') == 'managed-turn':
        values.pop('COMPOSE_PROFILES')
    communications.write_turn(sys.modules[__name__], root, values, owner)
    atomic_write(runtime, ''.join(f'{key}={value}\n' for key, value in values.items()))
    os.chown(runtime, *owner)
    atomic_write(overlay, template)
    os.chown(overlay, *owner)


def configure_proxy(config, directory=None):
    directory = Path(directory) if directory is not None else Path('/etc/caddy')
    target = directory / 'Caddyfile'
    site_file = directory / 'mushroom.caddy'
    import_line = f'import {site_file}'
    existing = target.read_text() if target.exists() else ''
    marker = '# Managed by mushroom installation'
    # Preserve foreign sites: require an explicit import rather than replacing their Caddyfile.
    uncommented = re.sub(r'#[^\n]*', '', existing)
    default = re.sub(r'\s+', '', uncommented) == ':80{root*/usr/share/caddyfile_server}'
    imported = import_line in uncommented
    require(not existing.strip() or default or imported,
            'Existing Caddy configuration preserved. Add `import /etc/caddy/mushroom.caddy` at top level, validate it, then rerun install.')
    sites = []
    snippets = {}
    for environment, spec in config['environments'].items():
        root = Path(spec['home']) / 'mushroom' / environment
        settings = load_env(root / 'runtime.env') if (root / 'runtime.env').exists() else {}
        port = settings.get('EJABBERD_PROXY_PORT', str(spec.get('basePort', PORTS[environment]) + 4))
        require(port.isdigit() and 1024 <= int(port) <= 65535, 'Invalid chat proxy port.')
        snippets[environment] = (BUNDLE / 'deploy/ejabberd/chat.caddy.example').read_text().replace(
            'https://dev-app.example.invalid', 'https://' + spec['domains']['app']).replace(
            'https://dev-dashboard.example.invalid', 'https://' + spec['domains']['dashboard']).replace(
            '127.0.0.1:5444', '127.0.0.1:' + port)
        chat_file = directory / 'mushroom-chat' / environment / 'chat.caddy'
        if chat_file.exists() and not chat_file.read_text().startswith('# Managed by mushroom installation'):
            normalize = lambda text: '\n'.join(line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#'))
            require(normalize(chat_file.read_text()) == normalize(snippets[environment]),
                    f'Custom chat proxy preserved at {chat_file}; review it before automated setup.')
        snippets[environment] = '# Managed by mushroom installation: chat\n' + snippets[environment]
        if (root / 'communications.compose.yaml').is_file():
            snippets[environment] += '\nrespond /v1/chat/internal/* 404\n'
            origin = settings.get('CHAT_VIDEO_ORIGIN', '')
            host = hostname(origin.removeprefix('https://'))
            video_port = settings.get('CHAT_VIDEO_PROXY_PORT', '')
            require(origin == f'https://{host}' and video_port.isdigit()
                    and 1024 <= int(video_port) <= 65535, 'Invalid MiroTalk reverse proxy configuration.')
            sites.append((BUNDLE / 'deploy/mirotalk/call.caddy.example').read_text()
                         .replace('calls.example.com', host).replace('127.0.0.1:5444', f'127.0.0.1:{video_port}'))
        for service, domain in spec['domains'].items():
            offset = {'web': 0, 'app': 1, 'dashboard': 2, 'api': 3}[service]
            chat_import = f'  import {directory}/mushroom-chat/{environment}/*.caddy\n' if service == 'api' else ''
            if service == 'api' and (root / 'state/database-maintenance').is_file():
                sites.append(f'{domain} {{\n  tls {config["email"]}\n  respond "Database recovery in progress" 503\n}}\n')
            else:
                sites.append(f'{domain} {{\n  tls {config["email"]}\n  encode zstd gzip\n{chat_import}  reverse_proxy 127.0.0.1:{spec.get("basePort", PORTS[environment]) + offset}\n}}\n')
    site_content = '\n'.join(sites)
    new_main = existing if imported else marker + '\n' + import_line + '\n'
    # Validate the proposed complete config before changing either live file.
    with tempfile.TemporaryDirectory(prefix='mushroom-validate-', dir=target.parent) as temporary:
        proposed_sites = Path(temporary) / 'sites.caddy'
        proposed_main = Path(temporary) / 'Caddyfile'
        for environment, content in snippets.items():
            atomic_write(Path(temporary) / 'chat' / environment / 'chat.caddy', content, 0o644)
        atomic_write(proposed_sites, site_content.replace(f'{directory}/mushroom-chat/', f'{temporary}/chat/'), 0o644)
        atomic_write(proposed_main, new_main.replace(import_line, f'import {proposed_sites}'), 0o644)
        run(['caddy', 'validate', '--config', proposed_main, '--adapter', 'caddyfile'], capture=True)
    if not imported:
        atomic_write(target.with_suffix('.before-mushroom'), existing, 0o600)
    for environment, content in snippets.items():
        atomic_write(directory / 'mushroom-chat' / environment / 'chat.caddy', content, 0o644)
        os.chmod(directory / 'mushroom-chat', 0o755)
        os.chmod(directory / 'mushroom-chat' / environment, 0o755)
    atomic_write(site_file, site_content, 0o644)
    if not imported:
        atomic_write(target, new_main, 0o644)
    run(['systemctl', 'enable', '--now', 'caddy'])
    run(['systemctl', 'reload', 'caddy'])


def github_handoff(config):
    directory = STATE / 'github'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    host_public_key = Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().split()
    host = config['sshHost'] if config['sshPort'] == '22' else f'[{config["sshHost"]}]:{config["sshPort"]}'
    known_hosts = f'{host} {host_public_key[0]} {host_public_key[1]}\n'
    atomic_write(directory / 'known_hosts', known_hosts)
    lines = [f'Repository: https://github.com/{config["repository"]}/settings/secrets/actions',
             'Names below are exact. Secret VALUES stay in protected files; do not paste them in chat/logs.', '', 'Repository Secrets:']
    scopes = [('VPS_', 'mushroom-deploy')] if any(e != 'production' for e in config['environments']) else []
    if 'production' in config['environments']:
        scopes.append(('PRODUCTION_VPS_', 'mushroom-prod'))
    for prefix, user in scopes:
        lines.extend([f'{prefix}HOST = {config["sshHost"]}', f'{prefix}USER = {user}',
                      f'{prefix}SSH_PRIVATE_KEY = contents of {directory / user / "id_ed25519"}',
                      f'{prefix}KNOWN_HOSTS = contents of {directory / "known_hosts"}'])
    lines.extend(['', 'Repository Variables:', f'VPS_PORT = {config["sshPort"]}', f'PRODUCTION_VPS_PORT = {config["sshPort"]}',
                  f'VPS_DEV_CD_ENABLED = {str("dev" in config["environments"]).lower()}',
                  f'VPS_CD_ENABLED = {str("staging" in config["environments"]).lower()}',
                  f'PRODUCTION_CD_ENABLED = {str("production" in config["environments"]).lower()}',
                  f'PRODUCTION_AUTO_DEPLOY = {str(config.get("productionAutoDeploy", False)).lower()}',
                  f'RELEASE_APPROVER = {config["approver"]}', 'GHCR_ATTESTATIONS_ENABLED = false', '',
                  'Only enable CD flags after DNS/TLS and doctor pass. No permanent GHCR token is needed.',
                  'First dev deployment: Actions > Sync shared dev VPS > Run workflow > branch dev.',
                  'First staging deployment: push/merge the installer-enabled release to main; CI must pass.',
                  'First production deployment: Actions > Deploy production > Run workflow > accepted release_run_id.',
                  'GitHub Secrets changes do not start a workflow by themselves.',
                  'Keep packages private and grant this repository Actions access to its GHCR packages.',
                  'Transfer the private key securely, then remove its handoff copy after testing Actions SSH access.',
                  'SMS/payment remain disabled until separately configured and enabled in the Dashboard.',
                  'Production media remains disabled by the existing application release policy.',
                  'Provider backup/PITR and off-site encrypted backup/key custody still require operational acceptance.'])
    report = '\n'.join(lines) + '\n'
    atomic_write(directory / 'GITHUB-SETUP.txt', report)
    print(report)


def firewall(config):
    ports = run(['ss', '-H', '-ltnp'], capture=True)
    daemon = run(['sshd', '-T'], capture=True)
    socket_ports = run(['systemctl', 'show', 'ssh.socket', '--property', 'Listen', '--value'], capture=True)
    configured = set(re.findall(r'^port ([0-9]+)$', daemon, re.M))
    configured.update(re.findall(r':([0-9]+)\b', socket_ports))
    for line in ports.splitlines():
        if '"sshd"' in line:
            match = re.search(r':([0-9]+)\s', line)
            if match:
                configured.add(match.group(1))
    require(config['sshPort'] in configured and re.search(rf':{re.escape(config["sshPort"])}\s', ports),
            'Selected SSH port is not a listening SSH service; firewall unchanged. Correct sshPort in installation.json or rerun configure.')
    for port in sorted(configured | {'80', '443'}):
        # Preserve every known sshd/socket port before enabling UFW, including the current management path.
        run(['ufw', 'allow', f'{port}/tcp'], capture=True)
    run(['ufw', '--force', 'enable'], capture=True)


def install(config, selected=None, *, communications_profile=None):
    steps = config['steps']
    if not steps.get('packages'):
        for line in run(['ss', '-H', '-ltnp'], capture=True).splitlines():
            if re.search(r':(?:80|443)\s', line):
                require('"caddy"' in line, 'Ports 80/443 are used by another service. Existing service preserved; review the proxy before installing.')
        needed_gib = 4 if 'dev' in config['environments'] else 2
        memory_kib = int(re.search(r'MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text()).group(1))
        # Nominal VPS RAM can exceed MemTotal due to unit conversion and reserved memory.
        minimum_kib = needed_gib * 1024 * 1024 * 9 // 10
        require(memory_kib >= minimum_kib,
                f'This profile requires a {needed_gib} GiB-class server '
                f'(minimum visible RAM: {minimum_kib / 1024**2:.2f} GiB; detected: {memory_kib / 1024**2:.2f} GiB).')
        require(shutil.disk_usage('/').free >= 10 * 1024**3, 'At least 10 GiB free disk is required.')
        print('Installing host prerequisites...')
        install_packages()
        steps['packages'] = True
        save(config)
    persist_bundle()
    allocate_networks(config)
    allocate_ports(config)
    if selected and not (Path(config['environments'][selected]['home']) / 'mushroom' / selected / 'runtime.env').exists():
        needed = 4 if 'dev' in config['environments'] else 2
        memory = int(re.search(r'MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text()).group(1))
        require(memory >= needed * 1024**2 * 9 // 10, f'Server needs at least a {needed} GiB resource class.')
        require(shutil.disk_usage('/').free >= 10 * 1024**3, 'At least 10 GiB free disk is required.')
    for environment, spec in config['environments'].items():
        if selected and environment != selected:
            continue
        owner = provision_user(spec['user'])
        ssh_identity(spec['user'], owner)
        root = Path(spec['home']) / 'mushroom' / environment
        first_run = None
        if not (root / 'bootstrap/first-run.json').exists() and not (root / 'bootstrap/complete').exists():
            print(f'{environment}: configure the first administrator (no demonstration data).')
            first_run = admin_details()
        db = None
        policy = None
        if environment == 'production':
            if not local_database(config, environment) and not (root / 'secrets/database_url').exists():
                db = ask('External production PostgreSQL URL (hidden)', secret=True,
                         validator=lambda v: database_url(v, production=True))
            if not (root / 'consent-policy.json').exists():
                policy = ask('Absolute path to approved production consent-policy JSON',
                             validator=lambda v: str(Path(v).resolve()) if validate_policy(v, 'production') else '')
        root = write_environment(config, environment, owner=owner, first_run=first_run, db_url=db, policy=policy)
        if environment == 'dev' and not spec.get('mediaConfigured'):
            configure_storage(config, environment)
        profile = communications_profile if selected == environment else None
        if profile is None and sys.stdin.isatty() and not load_env(root / 'runtime.env').get('CHAT_DB_PASSWORD'):
            profile = communications.guided_profile(sys.modules[__name__], config, environment)
        if profile or load_env(root / 'runtime.env').get('CHAT_DB_PASSWORD'):
            with communications.environment_lock(sys.modules[__name__], root):
                prepare_communications(config, environment, root=root, owner=owner, profile=profile)
                communications.receipt(sys.modules[__name__], environment, phase='configured', configured=True, verified=False)
        else:
            prepare_chat(config, environment, root=root, owner=owner)
        configure_backups(config, environment)
    # The current SSH port must already be listening; never edit sshd or close the current SSH session.
    firewall(config)
    github_handoff(config)
    check_dns(config, selected)
    configure_proxy(config)
    steps['serverReady'] = True
    save(config)
    print('Server preparation complete. Run doctor, add the handoff values in GitHub, then dispatch the first deployment.')


def configure(config, environment=None):
    candidate = json.loads(json.dumps(config))
    candidate['sshHost'] = ask('SSH host/IP reachable from GitHub', config['sshHost'],
                               lambda v: v if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', v) else choice(v, []))
    candidate['sshPort'] = ask('Existing listening SSH port', config['sshPort'],
                               lambda v: str(int(v)) if 1 <= int(v) <= 65535 else choice(v, []))
    if environment:
        require(environment in candidate['environments'], 'Select an installed environment.')
        spec = candidate['environments'][environment]
        root = Path(spec['home']) / 'mushroom' / environment
        require((root / 'runtime.env').exists(), 'Finish initial environment preparation first.')
        values = load_env(root / 'runtime.env')
        for service, domain in spec['domains'].items():
            spec['domains'][service] = ask(f'{environment} {service} hostname', domain, hostname)
        values['LOG_LEVEL'] = ask('Log level', values.get('LOG_LEVEL', 'info'), lambda v: choice(v, ['fatal', 'error', 'warn', 'info', 'debug', 'trace']))
        for key in ('SMS_TRANSPORT_MODE', 'SADAD_TRANSPORT_MODE'):
            values[key] = ask(key + ' (provider credentials/enable remain in Dashboard)', values.get(key, 'disabled'), lambda v: choice(v, ['disabled', 'live']))
        if values['SADAD_TRANSPORT_MODE'] == 'live':
            values['SADAD_REDIRECT_PROFILE'] = ask('Merchant-approved Sadad redirect profile', values.get('SADAD_REDIRECT_PROFILE', ''),
                                                  lambda v: choice(v, ['PDF_PURCHASE', 'PHP_VPG_PURCHASE', 'DOTNET_PURCHASE_INDEX']))
        spec['runtime'] = {name: values[name] for name in ('LOG_LEVEL', 'SMS_TRANSPORT_MODE', 'SADAD_TRANSPORT_MODE', 'SADAD_REDIRECT_PROFILE')}
        media = {}
        if environment == 'dev':
            values, media = media_configuration(values, root)
            spec['runtime'].update({key: value for key, value in values.items()
                                    if key == 'PRODUCT_MEDIA_STORAGE_MODE' or key.startswith(('R2_', 'ARVAN_'))
                                    if not any(word in key for word in ('ACCESS_KEY', 'SECRET'))})
            spec['mediaConfigured'] = True
        domains = [domain for item in candidate['environments'].values() for domain in item['domains'].values()]
        require(len(domains) == len(set(domains)), 'Environment hostnames must all be distinct.')
        check_dns(candidate)
        previous_api = values.get('PUBLIC_API_URL', '')
        for service, domain in spec['domains'].items():
            values[f'PUBLIC_{service.upper()}_URL'] = 'https://' + domain
        if values.get('EJABBERD_WEBSOCKET_URL') == previous_api.replace('https:', 'wss:') + '/xmpp-websocket':
            values['EJABBERD_WEBSOCKET_URL'] = values['PUBLIC_API_URL'].replace('https:', 'wss:') + '/xmpp-websocket'
        values['APP_ORIGINS'] = ','.join(values[f'PUBLIC_{key}_URL'] for key in ('WEB', 'APP', 'DASHBOARD'))
        if not values.get('SADAD_RETURN_URL') or values['SADAD_RETURN_URL'] == previous_api.rstrip('/') + '/v1/payments/providers/sadad/callback':
            values['SADAD_RETURN_URL'] = values['PUBLIC_API_URL'] + '/v1/payments/providers/sadad/callback'
        values['SADAD_RESULT_URL'] = values['PUBLIC_APP_URL'] + '/store/order-success'
        owner = (root.stat().st_uid, root.stat().st_gid)
        write_media_configuration(root, values, media, owner)
    save(candidate)
    if candidate['steps'].get('serverReady'):
        firewall(candidate)
        configure_proxy(candidate)
    github_handoff(candidate)
    print('Configuration saved. Rerun install if preparation is pending, then run the corresponding GitHub workflow to apply runtime changes.')


PanelAction = namedtuple('PanelAction', 'key label handler needs_environment destructive audit_key',
                         defaults=(False, False, None))


def setup_chat(config, environment=None, communications_profile=None, *, edit=False):
    """Prepare communications privately; CD deploys the application."""
    persist_bundle()
    for name in ([environment] if environment else list(config['environments'])):
        require(name in config['environments'], 'Select an installed --environment.')
        spec = config['environments'][name]
        root = Path(spec['home']) / 'mushroom' / name
        import pwd
        account = pwd.getpwnam(spec['user'])
        with communications.environment_lock(sys.modules[__name__], root):
            profile = communications_profile
            if profile is None:
                profile = communications.guided_profile(sys.modules[__name__], config, name, edit=edit)
            prepare_communications(config, name, root=root,
                                   owner=(account.pw_uid, account.pw_gid), profile=profile, allow_changes=edit)
            communications.receipt(sys.modules[__name__], name, phase='proxy-pending',
                                   configured=False, verified=False)
            configure_proxy(config)
            communications.receipt(sys.modules[__name__], name, phase='configured', configured=True, verified=False)
    print('Communications configured. Run the environment deployment, then Communications > Diagnose.')


def setup_storage(config):
    """Install the current bundle copy, then save the dev object-storage profile."""
    require('dev' in config['environments'], 'Object storage configuration needs an installed dev environment.')
    persist_bundle()
    configure_storage(config, 'dev')


def setup_bale(config):
    require('dev' in config['environments'], 'Bale setup requires an installed Dev environment.')
    persist_bundle()
    configure_bale(config, 'dev')


def setup_bale_webhook(config):
    require('dev' in config['environments'], 'Bale webhook registration needs Dev.')
    register_bale_webhook(config, 'dev')


def setup_analytics(config):
    require('dev' in config['environments'], 'Analytics setup needs an installed dev environment.')
    persist_bundle()
    configure_analytics(config, 'dev')


def panel_audit(action, environment=None):
    """Record only the requested action; values, paths and secrets are never written."""
    path = STATE / 'panel.log'
    require(not path.is_symlink(), 'Panel log cannot be a symlink.')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    line = f'{stamp} action={action} environment={environment or "-"}\n'
    existed = path.exists()
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, line.encode('utf-8'))
    finally:
        os.close(descriptor)
    if not existed:
        os.chmod(path, 0o600)


def environment_state(config, environment):
    """Read prepared/deployed/chat markers for display; secrets are never loaded."""
    spec = config['environments'][environment]
    root = Path(spec['home']) / 'mushroom' / environment
    prepared = False
    values = {}
    runtime = root / 'runtime.env'
    if runtime.is_file() and not runtime.is_symlink():
        try:
            values = load_env(runtime)
            prepared = (values.get('MUSHROOM_ENV_ROOT') == str(root)
                        and values.get('COMPOSE_PROJECT_NAME') == f'mushroom-{environment}')
        except (InstallationError, OSError, UnicodeDecodeError):
            prepared = False
    return {'prepared': prepared, 'deployed': (root / 'state/current').is_file(),
            'chat': (root / 'state/chat.initialized').is_file(),
            'firstAdmin': (root / 'bootstrap/first-run.json').is_file(),
            'media': values.get('PRODUCT_MEDIA_STORAGE_MODE', 'disabled') if prepared else 'unknown',
            'bale': 'enabled' if prepared and values.get('BALE_AUTH_ENABLED') == 'true' else 'disabled'}


def render_panel(config, actions, note=None, *, section=None, back_label='[q] quit'):
    """Pure rendering: state comes from files, never from secret values."""
    steps = config.get('steps') or {}
    title = f'Mushroom Panel - {config.get("domain", "unconfigured host")}'
    status = ['Server ready: ' + ('Yes' if steps.get('serverReady') else 'No'),
              'Host packages: ' + ('Yes' if steps.get('packages') else 'No')]
    for environment in config['environments']:
        state = environment_state(config, environment)
        flags = [name for name, active in (
            ('prepared', state['prepared']), ('deployed', state['deployed']), ('chat', state['chat']),
            ('first-admin-pending', state['firstAdmin']),
            ('backup', service_backup_enabled(config, environment, 'app')),
            ('chat-backup', service_backup_enabled(config, environment, 'chat'))) if active]
        flags.append('media=' + str(state['media']))
        flags.append('bale=' + str(state['bale']))
        status.append(f'{environment}: ' + ' | '.join(flags))

    sections = [('STATUS', status)]
    if section:
        grouped = ((section, sorted(actions,
                                    key=lambda action: int(action.key) if action.key.isdecimal() else float('inf'))),)
        sections = []
    else:
        groups = {'ENVIRONMENT': {'1', '2', '3'},
                  'OPERATIONS': {'4', '5', '8', '9', '10', '11'},
                  'CONFIGURATION & DEPLOYMENT': {'6', '7', '12', '13', '14', '15'}}
        grouped = tuple((name, sorted(
            (action for action in actions if str(action.key) in keys),
            key=lambda action: int(action.key) if action.key.isdecimal() else float('inf')))
                        for name, keys in groups.items())
        known = set().union(*groups.values())
        grouped += (('OTHER', sorted(
            (action for action in actions if str(action.key) not in known),
            key=lambda action: int(action.key) if action.key.isdecimal() else float('inf'))),)
    sections.extend((name, ['[' + action.key + '] ' + action.label
                            + ('  *' if action.destructive else '') for action in items])
                    for name, items in grouped if items)
    sections.append(('EXIT', [back_label]))
    content_width = max(56, len(title), *(len(row) for _, items in sections for row in items),
                        *(len(name) for name, _ in sections))
    border_top = '╔' + '═' * (content_width + 4) + '╗'
    divider = '╟' + '─' * (content_width + 4) + '╢'
    lines = [border_top, '║  ' + title.ljust(content_width) + '  ║', divider]
    for index, (heading, items) in enumerate(sections):
        lines.append('║  ' + heading.ljust(content_width) + '  ║')
        lines.extend('║  ' + row.ljust(content_width) + '  ║' for row in items)
        if index < len(sections) - 1:
            lines.append(divider)
    lines.append('╚' + '═' * (content_width + 4) + '╝')
    if any(action.destructive for action in actions):
        lines.extend(['', '* Requires typed confirmation'])
    if note:
        lines.extend(['', '! ' + str(note)])
    return '\n'.join(lines) + '\n'


def choose_environment(config, reader):
    names = list(config['environments'])
    if len(names) == 1:
        return names[0]
    print('\nEnvironment:')
    for index, name in enumerate(names, 1):
        print(f'  [{index}] {name}')
    answer = reader('Choose an environment [0 = back]: ').strip().lower()
    if answer in ('', '0', 'q'):
        return None
    if answer.isdecimal() and 1 <= int(answer) <= len(names):
        return names[int(answer) - 1]
    require(answer in names, 'Unknown environment: ' + answer)
    return answer


def typed_confirmation(prompt, expected, reader):
    return reader(f'{prompt} Type "{expected}" to continue: ').strip() == expected


def panel_status(config, environment=None):
    run(compose_command(config, environment) + ['ps'])


def panel_logs(config, environment=None, *, reader=None, emit=print):
    reader = reader or terminal_input
    command = compose_command(config, environment)
    services = run(command + ['config', '--services'], capture=True).splitlines()
    require(services, f'{environment}: no services configured.')
    while True:
        emit('\nLogs for ' + environment + ':\n  [0] Back\n  [1] All services')
        for index, service in enumerate(services, 2):
            emit(f'  [{index}] {service}')
        choice = reader('Choose a service: ').strip()
        if choice in ('', '0', 'q'):
            return
        if not choice.isdecimal() or not 1 <= int(choice) <= len(services) + 1:
            emit('Unknown selection. Please try again.')
            continue
        selected = [] if choice == '1' else [services[int(choice) - 2]]
        mode = reader('  [1] Live logs  [2] Recent logs  [0] Back: ').strip()
        if mode in ('', '0', 'q'):
            continue
        if mode not in ('1', '2'):
            emit('Unknown selection. Please try again.')
            continue
        emit('\nShowing logs' + (' (Ctrl+C to return)' if mode == '1' else '') + ':\n')
        try:
            run(command + ['logs', '--tail', '100'] + (['--follow'] if mode == '1' else []) + selected)
        except KeyboardInterrupt:
            emit('\nLive logs stopped.')
        except InstallationError as error:
            emit(f'Unable to show logs: {error}')
        if mode == '2':
            reader('\nPress Enter to return to logs menu: ')


def panel_doctor(config, environment=None):
    doctor(config)


def panel_restart(config, environment=None):
    run(compose_command(config, environment) + ['restart'])


def panel_chat(config, environment=None):
    communications.menu(sys.modules[__name__], config, environment)


def panel_configure(config, environment=None):
    configure(config, environment)


def panel_storage(config, environment=None):
    setup_storage(config)


def panel_analytics(config, environment=None):
    setup_analytics(config)


def panel_bale(config, environment=None):
    require(environment == 'dev', 'Bale setup is available only in Dev.')
    setup_bale(config)


def panel_bale_webhook(config, environment=None):
    require(environment == 'dev', 'Bale webhook registration is available only in Dev.')
    setup_bale_webhook(config)


def panel_backup(config, environment=None):
    service = ask('Database (app / chat)', 'app', lambda value: choice(value, ('app', 'chat')))
    run_backup(config, environment, service)


def panel_github(config, environment=None):
    github_handoff(config)


def panel_proxy(config, environment=None):
    configure_proxy(config)


def panel_install(config, environment=None):
    install(config)


def add_environment(config, environment):
    require(environment in ('dev', 'staging', 'production'), 'Select dev, staging, or production.')
    if environment not in config['environments']:
        spec = environment_configuration(config['domain'], environment)
        candidate = {**config, 'environments': {**config['environments'], environment: spec}}
        check_unique_domains(candidate)
        config['environments'][environment] = spec
        save(config)
    install(config, selected=environment)


def panel_add_environment(config, environment=None):
    environment = ask('Add or resume environment (dev / staging / production)',
                      validator=lambda value: choice(value, ('dev', 'staging', 'production')))
    add_environment(config, environment)


def panel_backup_settings(config, environment):
    service = ask('Database (app / chat)', 'app', lambda value: choice(value, ('app', 'chat')))
    active = service_backup_enabled(config, environment, service)
    list_backups(environment, service)
    enabled = ask('Daily encrypted backups (yes / no)', 'yes' if active else 'no',
                  lambda value: choice(value, ('yes', 'no'))) == 'yes'
    set_backup_schedule(config, environment, service, enabled)


def panel_restore(config, environment):
    service = ask('Database (app / chat)', 'app', lambda value: choice(value, ('app', 'chat')))
    archives = list_backups(environment, service)
    if config['environments'][environment].get('backupRemotePrefix'):
        command, remote = remote_arguments(config, environment, service)
        try:
            names = run(command + ['lsf', '--files-only', '--max-depth', '1', remote], capture=True, sensitive=True)
            for name in sorted(names.splitlines(), reverse=True):
                if BACKUP_NAME.fullmatch(name):
                    print('remote:' + name)
        except InstallationError:
            print('Off-site archive list unavailable. Local archives are still available.')
    archive = ask('Archive number, absolute path, or remote:filename')
    if archive.isdecimal():
        index = int(archive)
        require(1 <= index <= len(archives), 'Archive number does not exist.')
        archive = archives[index - 1]
    identity = ask('Path to original age identity (Enter to use the saved key)')
    if isinstance(archive, str) and archive.startswith('remote:'):
        restore_offsite(config, environment, service, archive.removeprefix('remote:'), identity or None)
    else:
        restore_isolated(config, environment, archive, service, identity or None)


def environment_root(config, environment):
    return Path(config['environments'][environment]['home']) / 'mushroom' / environment


@contextmanager
def deployment_lock(root, environment):
    import fcntl
    path = root / 'state/deploy.lock'
    require((root / 'state/recovery-lock-compatible').is_file() and path.is_file() and not path.is_symlink(),
            'Deploy this environment with the updated VPS workflow before database switching.')
    require(not (root / '.deploy.lock').exists(), 'A deployment using the older lock is active.')
    with path.open('r') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallationError('Deployment or database recovery is active.') from None
        yield


def recovery_record(environment, kind):
    directory = STATE / 'database-recovery'
    directory.mkdir(exist_ok=True, mode=0o700)
    return directory / f'{environment}-{kind}'


def database_location(parsed):
    return parsed.hostname, parsed.port or 5432, unquote(parsed.path[1:])


def stage_application_recovery(config, environment, archive, target_file=None, identity=None):
    root = environment_root(config, environment)
    current = urlsplit((root / 'secrets/database_url').read_text().strip())
    key = private_file(identity) if identity else backup_key(environment)
    require(Path(archive).is_file() and key.is_file(), 'Backup archive or age identity is missing.')
    with deployment_lock(root, environment):
        if local_database(config, environment):
            require(not target_file and current.hostname == 'postgres',
                    'Managed databases use a fresh database on the same private PostgreSQL instance.')
            target_name = f'mushroom_{environment.replace("-", "_")}_{secrets.token_hex(5)}_restore_test'
            command = compose_command(config, environment) + ['exec', '-T', 'postgres']
            user = unquote(current.username)
            run(command + ['createdb', '-U', user, '-T', 'template0', target_name], sensitive=True)
            target = current._replace(path='/' + target_name).geturl()
            count = restore_archive(command, ['--username', user, '--dbname', target_name], archive, key)
        else:
            require(target_file, 'Create an empty recovery database and provide its protected TLS URL file.')
            target = database_url(private_file(target_file).read_text().strip(), production=True)
            destination = urlsplit(target)
            require(re.fullmatch(r'[a-z][a-z0-9_]*_restore_test', database_location(destination)[2]),
                    'Recovery database name must end in _restore_test.')
            for existing in config['environments']:
                path = environment_root(config, existing) / 'secrets/database_url'
                if path.is_file():
                    require(database_location(destination) != database_location(urlsplit(path.read_text().strip())),
                            'Recovery cannot target an active environment database.')
            with pg_client(config, environment, target) as (command, connection):
                count = restore_archive(command, connection, archive, key)
        require(count > 0, 'Recovery did not produce application tables.')
        atomic_write(recovery_record(environment, 'candidate'), target + '\n')
        print(f'Application recovery candidate prepared with {count} database objects.')


def check_recovery_schema(command, environment):
    if environment == 'dev':
        args = ['--entrypoint', 'bash', 'api', '-ec',
                'export DATABASE_URL="$(cat /run/secrets/database_url)"; '
                'exec pnpm --filter @sabz-zendegi/api exec prisma migrate status']
    else:
        args = ['api', 'node_modules/prisma/build/index.js', 'migrate', 'status']
    run(command + ['run', '--rm', '--no-deps', *args], sensitive=True)


def activate_application_recovery(config, environment, kind):
    root = environment_root(config, environment)
    source = root / 'secrets/database_url'
    record = recovery_record(environment, kind)
    require(record.is_file() and not record.is_symlink(), 'No saved database connection for this operation.')
    target = database_url(record.read_text().strip(), production=not local_database(config, environment))
    maintenance = root / 'state/database-maintenance'
    journal = recovery_record(environment, 'in-progress')
    with deployment_lock(root, environment):
        require(not journal.exists() or kind == 'in-progress', 'Resume the interrupted database switch first.')
        command = compose_command(config, environment)
        previous = source.read_text()
        require(kind == 'in-progress' or previous.strip() != target, 'This database connection is already active.')
        permissions = source.stat()
        if kind != 'in-progress':
            atomic_write(journal, previous)

        def write_connection(value):
            atomic_write(source, value, stat.S_IMODE(permissions.st_mode))
            os.chown(source, permissions.st_uid, permissions.st_gid)

        def start_api():
            run(command + ['up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '180', 'api'],
                sensitive=True)

        atomic_write(maintenance, '503\n')
        try:
            configure_proxy(config)
            run(command + ['stop', 'api'], sensitive=True)
            write_connection(target + '\n')
            check_recovery_schema(command, environment)
            start_api()
        except BaseException:
            print('Switch interrupted or failed. Restoring the saved connection while maintenance remains active.')
            if kind != 'in-progress':
                write_connection(previous)
                start_api()
                maintenance.unlink()
                try:
                    configure_proxy(config)
                except BaseException:
                    atomic_write(maintenance, '503\n')
                    raise
                journal.unlink()
            raise
        if kind != 'in-progress':
            atomic_write(recovery_record(environment, 'previous'), previous)
        maintenance.unlink()
        try:
            configure_proxy(config)
        except BaseException:
            atomic_write(maintenance, '503\n')
            raise
        journal.unlink()
        print('Application connection is active and API health checks passed.')


def panel_application_recovery(config, environment):
    action = ask('Application database (prepare / switch / rollback / resume)', 'prepare',
                 lambda value: choice(value, ('prepare', 'switch', 'rollback', 'resume')))
    if action == 'prepare':
        archives = list_backups(environment, 'app')
        value = ask('Archive number or absolute file path')
        if value.isdecimal():
            require(1 <= int(value) <= len(archives), 'Archive number does not exist.')
            value = archives[int(value) - 1]
        identity = ask('Path to original age identity (Enter to use the saved key)')
        target = None if local_database(config, environment) else ask('Path to private empty recovery database URL')
        stage_application_recovery(config, environment, value, target, identity or None)
        return
    if action == 'rollback':
        print('Writes made after the previous switch are stored in the newer database.')
    require(typed_confirmation(f'{action} application database for {environment}. This interrupts API traffic.',
                               environment, terminal_input), 'Cancelled.')
    activate_application_recovery(config, environment,
                                  {'switch': 'candidate', 'rollback': 'previous', 'resume': 'in-progress'}[action])



def panel_recovery_bundle(config, environment):
    """Export only the files needed to reconstruct an environment on a replacement host."""
    spec = config['environments'][environment]
    root = Path(spec['home']) / 'mushroom' / environment
    recipient = ask('Public age recipient for recovery bundle',
                    validator=lambda value: value if re.fullmatch(r'age1[a-z0-9]{58}', value) else
                    choice(value, []))
    if shutil.which('age') is None:
        run(['apt-get', 'install', '-y', '--no-install-recommends', 'age'])
    directory = backup_directory(environment, 'recovery')
    require(not directory.is_symlink() and not directory.parent.is_symlink(),
            'Recovery destination cannot be a symlink.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = directory / f'{stamp}-{secrets.token_hex(4)}.tar.age'
    partial = destination.with_suffix('.partial')
    files = ['runtime.env', 'network.yaml', 'consent-policy.json', 'chat.compose.yaml', 'chat.database.yaml',
             'postgres.compose.yaml', 'state/chat.initialized', 'bootstrap/complete']
    files.extend('secrets/' + name for name in (*SECRET_NAMES, 'postgres_password',
                                               'ejabberd-private.pem', 'ejabberd-public.jwk'))
    for marker in ('current', 'previous'):
        path = root / 'state' / marker
        if path.is_file():
            release = Path(path.read_text().strip()).resolve()
            require(release.is_relative_to((root / 'releases').resolve()), 'Invalid release state.')
            files.append(str((release / 'release-manifest.json').relative_to(root)))
    files = [name for name in dict.fromkeys(files) if (root / name).is_file()]
    require('runtime.env' in files and 'secrets/database_url' in files, 'Prepare the environment first.')
    require(all(not (root / name).is_symlink() for name in files), 'Recovery files cannot be symlinks.')
    try:
        binary_pipeline(['tar', '-C', root, '-cf', '-', '--', *files],
                        ['age', '--recipient', recipient, '--output', partial],
                        stages=('recovery package', 'age encryption'))
        os.chmod(partial, 0o600)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    print(f'Encrypted recovery package: {destination}. Copy it off-site with the database archives.')



def monitoring_path(environment):
    return STATE / 'monitoring' / f'{environment}.json'


def notify_monitor(url, environment, event):
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password,
            'Use an HTTPS monitoring URL.')
    request = Request(url, data=json.dumps({'environment': environment, 'event': event}).encode(),
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=8) as reply:
            require(200 <= reply.status < 300, 'Monitoring endpoint rejected the request.')
    except OSError:
        raise InstallationError('Monitoring endpoint did not accept the request.') from None


def panel_monitoring(config, environment):
    alert = ask('Alert webhook URL', secret=True)
    heartbeat = ask('External heartbeat URL', secret=True)
    notify_monitor(alert, environment, 'test')
    notify_monitor(heartbeat, environment, 'heartbeat')
    path = monitoring_path(environment)
    path.parent.mkdir(exist_ok=True, mode=0o700)
    atomic_write(path, json.dumps({'alert': alert, 'heartbeat': heartbeat}))
    atomic_write('/etc/systemd/system/mushroom-monitor.service',
                 '[Service]\nType=oneshot\nExecStart=/usr/local/sbin/mushroom monitor\n', 0o644)
    atomic_write('/etc/systemd/system/mushroom-monitor.timer',
                 '[Timer]\nOnCalendar=*:0/5\nPersistent=true\n[Install]\nWantedBy=timers.target\n', 0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', 'mushroom-monitor.timer'])
    print('Monitoring enabled. Verify the test alert and set an external heartbeat timeout.')


def monitor_environment(config, environment, spec, profile):
    path = monitoring_path(environment).with_suffix('.state')
    previous = json.loads(path.read_text()) if path.is_file() else {}
    counts, alerts = {}, set()
    checks = {name: 'https://' + domain + ('/v1/health/ready' if name == 'api' else '/')
              for name, domain in spec['domains'].items()}
    checks['chat'] = 'https://' + spec['domains']['api'] + '/v1/health/chat'
    for name, url in checks.items():
        try:
            with urlopen(url, timeout=5) as response:
                healthy = 200 <= response.status < 300
        except OSError:
            healthy = False
        counts[name] = 0 if healthy else previous.get('counts', {}).get(name, 0) + 1
        if counts[name] >= 3:
            alerts.add(f'service-unhealthy:{name}')
    for directory in ('/', '/var/lib/docker', '/var/backups/mushroom', spec['home']):
        if Path(directory).is_dir():
            usage = shutil.disk_usage(directory)
            if usage.used / usage.total >= 0.85:
                alerts.add(f'disk-capacity:{directory}')
    for service in ('app', 'chat'):
        if not service_backup_enabled(config, environment, service):
            continue
        directory = backup_directory(environment, service)
        stamps = ['last-success']
        if spec.get('backupRemotePrefix'):
            stamps.append('last-offsite-success')
        for name in stamps:
            marker = directory / name
            if not marker.is_file() or time.time() - marker.stat().st_mtime >= 26 * 3600:
                alerts.add(f'{name}-stale:{service}')
        failure = directory / 'last-error'
        if failure.is_file():
            alerts.add(f'{failure.read_text().strip()}:{service}')
    reported = set(previous.get('reported', [])) & alerts
    try:
        notify_monitor(profile['heartbeat'], environment, 'heartbeat')
    except InstallationError:
        # Keep delivering alerts if only the heartbeat service is down.
        print(f'{environment}: heartbeat endpoint unavailable.')
    try:
        for event in sorted(alerts - reported):
            notify_monitor(profile['alert'], environment, event)
            reported.add(event)
    finally:
        atomic_write(path, json.dumps({'counts': counts, 'reported': sorted(reported)}))


def monitor(config):
    failed = []
    for environment, spec in config['environments'].items():
        path = monitoring_path(environment)
        if path.is_file():
            try:
                monitor_environment(config, environment, spec, json.loads(private_file(path).read_text()))
            except InstallationError:
                failed.append(environment)
    require(not failed, 'Alert delivery failed for ' + ', '.join(failed))



def backup_menu_actions():
    """Backup and recovery operations, kept together in their own panel."""
    return [
        PanelAction('1', 'Run encrypted backup now', panel_backup, True, False, '8'),
        PanelAction('2', 'Configure backup schedule', panel_backup_settings, True, False, '16'),
        PanelAction('3', 'Configure S3 / SFTP backup destination', panel_offsite, True, False, '18'),
        PanelAction('4', 'Retry off-site transfer', panel_retry_transfer, True, False, '19'),
        PanelAction('5', 'Import encrypted backup into an isolated database', panel_restore, True, False, '17'),
        PanelAction('6', 'Export encrypted environment recovery package', panel_recovery_bundle, True, False, '21'),
        PanelAction('7', 'Prepare or switch application database recovery', panel_application_recovery,
                    True, False, '22'),
        PanelAction('8', 'Configure monitoring and send a test alert', panel_monitoring, True, False, '20'),
    ]


def panel_backup_menu(config, environment=None, *, reader=None, emit=print, operation_lock=nullcontext):
    """Open the backup/recovery submenu; each operation takes its own installer lock."""
    run_panel(config, actions=backup_menu_actions(), reader=reader, emit=emit,
              operation_lock=operation_lock, section='BACKUP & RECOVERY',
              back_label='[q] back to main menu', return_prompt='\nPress Enter to return to backup menu: ')


def panel_menu(config):
    """Every entry calls an existing operation; the panel adds no new behaviour."""
    actions = [
        PanelAction('1', 'Environment status (docker compose ps)', panel_status, True),
        PanelAction('2', 'Logs management', panel_logs, True),
        PanelAction('3', 'Doctor checks (DNS/TLS/secrets/deployment)', panel_doctor),
        PanelAction('4', 'Restart environment', panel_restart, True, True),
        PanelAction('5', 'Communications management', panel_chat, True),
        PanelAction('6', 'Configure domains/runtime', panel_configure, True),
        PanelAction('7', 'Configure dev object storage', panel_storage),
        PanelAction('8', 'Backup and recovery', panel_backup_menu),
        PanelAction('15', 'Add or resume an environment', panel_add_environment),
        PanelAction('9', 'Print GitHub handoff summary', panel_github),
        PanelAction('10', 'Re-apply proxy/TLS configuration', panel_proxy, False, True),
        PanelAction('11', 'Resume full installation', panel_install, False, True),
        PanelAction('12', 'Configure dev analytics (hidden API secret)', panel_analytics),
        PanelAction('13', 'Configure Dev Bale Mini App (private bot token)', panel_bale, True),
        PanelAction('14', 'Register Dev Bale webhook (after deployment)', panel_bale_webhook, True, True),
    ]
    if 'dev' not in config['environments']:
        actions = [action for action in actions if action.handler not in (panel_storage, panel_analytics, panel_bale, panel_bale_webhook)]
    return actions


def run_panel(config, *, actions=None, reader=None, confirm=None, emit=print, operation_lock=nullcontext,
              section=None, back_label='[q] quit', return_prompt='\nPress Enter to return to the main menu: '):
    """Menu loop with injectable input/output so it is testable without a terminal."""
    actions = panel_menu(config) if actions is None else actions
    reader = reader or terminal_input
    confirm = confirm or (lambda prompt, expected: typed_confirmation(prompt, expected, reader))
    by_key = {str(action.key): action for action in actions}
    note = None
    while True:
        emit(render_panel(config, actions, note, section=section, back_label=back_label))
        note = None
        try:
            selection = reader('Select: ').strip().lower()
        except EOFError:
            return
        if selection in ('q', 'quit', 'exit'):
            return
        action = by_key.get(selection)
        if action is None:
            note = 'Unknown selection: ' + (selection or '(empty)')
            continue
        environment = None
        if action.needs_environment:
            try:
                environment = choose_environment(config, reader)
            except EOFError:
                return
            except InstallationError as error:
                note = str(error)
                continue
            if environment is None:
                continue
        if action.destructive and not confirm(f'{action.label} ({environment or "all environments"}).',
                                              environment or 'install'):
            note = 'Cancelled; nothing changed.'
            continue
        panel_audit(action.audit_key or action.key, environment)
        try:
            emit('\n--- ' + action.label + ' ---')
            if action.handler is panel_backup_menu:
                action.handler(config, environment, reader=reader, emit=emit, operation_lock=operation_lock)
                if operation_lock is lock:
                    current = json.loads((STATE / 'installation.json').read_text())
                    config.clear()
                    config.update(current)
                    actions = panel_menu(config)
                    by_key = {str(entry.key): entry for entry in actions}
            else:
                with operation_lock():
                    if operation_lock is lock:
                        current = json.loads((STATE / 'installation.json').read_text())
                        config.clear()
                        config.update(current)
                    if action.handler is panel_logs:
                        panel_logs(config, environment, reader=reader, emit=emit)
                    else:
                        action.handler(config, environment)
                    if operation_lock is lock and section is None:
                        actions = panel_menu(config)
                        by_key = {str(entry.key): entry for entry in actions}
        except (InstallationError, OSError) as error:
            note = f'{action.label} failed: {error}'
        except EOFError:
            return
        except KeyboardInterrupt:
            note = 'Cancelled.'
        else:
            note = None
        if action.handler not in (panel_logs, panel_backup_menu) and reader is terminal_input:
            try:
                reader(return_prompt)
            except EOFError:
                return


def panel(config):
    """Interactive terminal panel; non-interactive callers must use a subcommand."""
    require(sys.stdin.isatty() and sys.stdout.isatty(),
            'The panel needs an interactive terminal. Use "sudo mushroom status|doctor|logs" for non-interactive checks.')
    try:
        if updater.panel_offer(BUNDLE):
            os.execv(sys.executable, [sys.executable, '-X', 'utf8', str(updater.ROOT / 'current/installation/manager.py'), 'panel'])
        run_panel(config, operation_lock=lock)
    except KeyboardInterrupt:
        print('\nPanel closed.')
    print()


def compose_command(config, environment):
    spec = config['environments'][environment]
    root = Path(spec['home']) / 'mushroom' / environment
    if environment == 'dev':
        env = root / 'runtime.env'
        workspace = Path(load_env(env).get('DEV_WORKSPACE_PATH', str(root / 'workspace'))) if env.is_file() else root / 'workspace'
        require(workspace.resolve().is_relative_to(root.resolve()), 'Dev workspace points outside the environment.')
        compose = workspace / 'deploy/vps/compose.dev.yaml'
    else:
        state = root / 'state/current'
        require(state.exists(), f'{environment}: awaiting first successful GitHub deployment.')
        release = Path(state.read_text().strip()).resolve()
        require(release.is_relative_to((root / 'releases').resolve()), 'Release state points outside this environment.')
        compose, env = release / 'compose.yaml', release / 'release.env'
    require(compose.is_file(), f'{environment}: awaiting first GitHub deployment.')
    args = ['runuser', '-u', spec['user'], '--', 'sh', '-c',
            'cd "$1" || exit 1; shift; exec "$@"', 'mushroom-compose', spec['home'], 'env',
            f"HOME={spec['home']}", f"USER={spec['user']}", f"LOGNAME={spec['user']}",
            'docker', 'compose', '--project-name', f'mushroom-{environment}',
            '--env-file', env, '--file', compose]
    if (root / 'network.yaml').exists():
        args.extend(['--file', root / 'network.yaml'])
    if env.is_file() and load_env(env).get('CHAT_DB_PASSWORD'):
        overlay = root / 'communications.compose.yaml'
        require(overlay.is_file(), f'{environment}: communications overlay missing; run setup-chat.')
        args.extend(['--file', overlay])
    else:
        if (root / 'chat.compose.yaml').exists():
            args.extend(['--file', root / 'chat.compose.yaml'])
        if (root / 'chat.database.yaml').exists():
            args.extend(['--file', root / 'chat.database.yaml'])
    if environment == 'production' and local_database(config, environment):
        args.extend(['--file', root / 'postgres.compose.yaml'])
    return args


def doctor(config, deployed=False):
    run(['docker', 'version'], capture=True)
    run(['docker', 'compose', 'version'], capture=True)
    check_dns(config)
    require(config['steps'].get('serverReady'), 'Server preparation incomplete; rerun install.')
    for environment, spec in config['environments'].items():
        root = Path(spec['home']) / 'mushroom' / environment
        values = load_env(root / 'runtime.env')
        validate_bale_runtime(values, root, environment)
        mode = values.get('PRODUCT_MEDIA_STORAGE_MODE', 'disabled')
        require(mode in ('disabled', 'r2', 'arvancloud'), 'Unknown object storage mode; run setup-storage --environment dev.')
        require(environment == 'dev' or mode == 'disabled', 'Object storage must remain disabled outside the supported dev runtime.')
        if mode != 'disabled':
            prefix = 'R2' if mode == 'r2' else 'ARVAN'
            require(all(values.get(f'{prefix}_{name}') for name in ('ENDPOINT', 'BUCKET')),
                    'Selected object storage profile is incomplete; run setup-storage --environment dev.')
            for suffix in ('access_key_id', 'secret_access_key'):
                path = root / 'secrets' / f'{prefix.lower()}_{suffix}'
                require(not path.is_symlink() and path.is_file() and path.stat().st_size > 0,
                        'Selected storage credential file is missing/empty; run setup-storage --environment dev.')
        if values.get('CHAT_DB_PASSWORD'):
            require((root / 'communications.compose.yaml').is_file()
                    and all(values.get(key) for key in (COMMUNICATIONS_INPUTS - COMMUNICATIONS_OPTIONAL) | {
                        'CHAT_VIDEO_SECRET', 'CHAT_VIDEO_EMBED_ORIGINS', 'CHAT_VIDEO_PROXY_PORT'}),
                    f'{environment}: communications preparation incomplete; run mushroom setup-chat.')
        else:
            require((root / 'chat.compose.yaml').is_file() and all(values.get(key) for key in (
                'EJABBERD_SOURCE_ROOT', 'EJABBERD_IMAGE', 'EJABBERD_DOMAIN', 'EJABBERD_WEBSOCKET_URL',
                'EJABBERD_SQL_HOST', 'EJABBERD_SQL_DB', 'EJABBERD_SQL_USER', 'EJABBERD_SQL_PASSWORD',
                'EJABBERD_POLICY_SECRET', 'EJABBERD_PRIVATE_KEY_PATH', 'EJABBERD_PUBLIC_KEY_PATH', 'EJABBERD_PROXY_PORT')),
                f'{environment}: chat preparation incomplete; run mushroom setup-chat --environment {environment}.')
            require(all(Path(values[key]).is_file() for key in ('EJABBERD_PRIVATE_KEY_PATH', 'EJABBERD_PUBLIC_KEY_PATH')),
                    f'{environment}: chat key missing; restore its backup or complete setup-chat.')
            require(values['EJABBERD_SQL_HOST'] != 'chat-postgres' or (root / 'chat.database.yaml').is_file(),
                    f'{environment}: managed chat database overlay missing; run setup-chat.')
        require(all((root / 'secrets' / name).is_file() for name in SECRET_NAMES), f'{environment}: missing secret file.')
        for name in ('jwt_access_secret', 'refresh_token_pepper', 'otp_pepper'):
            require(re.fullmatch(r'[A-Za-z0-9_-]{43,256}', (root / 'secrets' / name).read_text().strip()), f'{environment}: invalid {name}.')
        require(len({(root / 'secrets' / name).read_text().strip() for name in ('jwt_access_secret', 'refresh_token_pepper', 'otp_pepper')}) == 3,
                f'{environment}: authentication secrets must be independent.')
        run(['runuser', '-u', spec['user'], '--', 'docker', 'info'], capture=True)
        for domain in spec['domains'].values():
            try:
                with socket.create_connection((domain, 443), timeout=10) as connection:
                    with ssl.create_default_context().wrap_socket(connection, server_hostname=domain):
                        pass
            except (OSError, ssl.SSLError):
                raise InstallationError(f'{domain}: HTTPS certificate not ready; check DNS/firewall/Caddy and rerun doctor.') from None
        if environment == 'production':
            managed = local_database(config, environment)
            database_url((root / 'secrets/database_url').read_text().strip(), not managed)
            validate_policy(root / 'consent-policy.json', 'production')
            if not managed or (root / 'state/current').is_file():
                with pg_client(config, environment) as (command, connection):
                    version = run(command + ['psql', *connection, '-At', '-v', 'ON_ERROR_STOP=1', '-c', 'SHOW server_version_num'],
                                  capture=True, sensitive=True)
                require(version.strip().isdigit() and int(version.strip()) >= 160000, 'PostgreSQL must be version 16 or newer.')
        print(f'{environment}: configuration ready' + ('; awaiting first CD' if not (root / 'state/current').exists() and environment != 'dev' else ''))
        if deployed:
            for name, domain in spec['domains'].items():
                path = '/v1/health/ready' if name == 'api' else '/'
                with urlopen(f'https://{domain}{path}', timeout=15) as response:
                    require(response.status == 200, f'{environment}: {name} HTTPS probe failed.')
            with urlopen(f'https://{spec["domains"]["api"]}/v1/health/chat', timeout=15) as response:
                require(response.status == 200, f'{environment}: chat health probe failed.')
            print(f'{environment}: external HTTPS probes passed')


def audit_environment(repository, current=False):
    """Print key names only; actual local .env values never leave this process."""
    repository = Path(repository)
    schema = repository / 'apps/api/src/config/environment.validation.ts'
    keys = set(re.findall(r'^  ([A-Z][A-Z0-9_]*):', schema.read_text(encoding='utf-8'), re.M))
    example = load_env(repository / 'apps/api/.env.example')
    missing = keys - example.keys()
    require(not missing, 'API example lacks schema keys: ' + ', '.join(sorted(missing)))
    for relative in ('deploy/vps/compose.dev.yaml', 'deploy/vps/compose.yaml', 'compose.production.example.yaml'):
        overlay = 'deploy/mirotalk/compose.yaml' if 'CHAT_DATABASE_URL' in keys else 'deploy/ejabberd/compose.yaml'
        source = (repository / relative).read_text(encoding='utf-8') + '\n' + (repository / overlay).read_text(encoding='utf-8')
        secret_keys = {name.upper() for name in SECRET_NAMES}
        absent = [key for key in keys - {'NODE_ENV', 'PORT'} if not re.search(rf'\b{key}(?:_FILE)?\b', source)
                  and not (relative.endswith('compose.dev.yaml') and key in secret_keys
                           and re.search(rf'\b{key.lower()}\b', source))]
        require(not absent, f'{relative} lacks API settings: ' + ', '.join(sorted(absent)))
    for app in ('web', 'app', 'dashboard'):
        template = load_env(repository / f'apps/{app}/.env.example')
        require(all(key.startswith('PUBLIC_') for key in template), f'{app}: frontend template must contain public settings only.')
    print(f'Environment contract: all {len(keys)} API keys and three frontend examples covered.')
    if current:
        for app in ('api', 'web', 'app', 'dashboard'):
            path = repository / f'apps/{app}/.env'
            if not path.exists():
                print(f'{app}: .env absent (not modified).')
                continue
            values = load_env(path)
            template = load_env(repository / f'apps/{app}/.env.example')
            print(f'{app}: absent keys: ' + (', '.join(sorted(template.keys() - values.keys())) or 'none'))
            if app == 'api':
                invalid = [name for name in ('JWT_ACCESS_SECRET', 'REFRESH_TOKEN_PEPPER', 'OTP_PEPPER')
                           if not re.fullmatch(r'[A-Za-z0-9_-]{43,256}', values.get(name, ''))]
                print('api: missing/invalid required auth keys: ' + (', '.join(invalid) or 'none'))


def persist_bundle():
    if (BUNDLE / 'release.json').is_file():
        updater.install_local(BUNDLE)
        return
    destination = Path('/opt/mushroom-installer')
    if BUNDLE != destination:
        for name in BUNDLE_FILES:
            atomic_write(destination / name, (BUNDLE / name).read_text(encoding='utf-8'), 0o755 if name.endswith('.sh') else 0o644)
    atomic_write('/usr/local/sbin/mushroom', '#!/usr/bin/env bash\nset -Eeuo pipefail\nexec python3 -X utf8 /opt/mushroom-installer/installation/manager.py "$@"\n', 0o755)


BACKUP_NAME = re.compile(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}\.dump\.age')


def backup_directory(environment, service):
    root = Path('/var/backups/mushroom') / environment
    return root if service == 'app' else root / service


def backup_key(environment):
    return STATE / 'backup-keys' / f'{environment}.key'


def initialize_backup(environment, service):
    directory = backup_directory(environment, service)
    key = backup_key(environment)
    recipient = key.with_suffix('.recipient')
    require(not key.is_symlink() and not recipient.is_symlink(), 'Backup key files cannot be symlinks.')
    if not key.exists():
        require(not recipient.exists() and not any(backup_directory(environment, 'app').rglob('*.dump.age')),
                'Original backup key is missing. Restore the original identity before continuing.')
    if shutil.which('age') is None:
        run(['apt-get', 'install', '-y', '--no-install-recommends', 'age'])
    key.parent.mkdir(exist_ok=True, mode=0o700)
    if not key.exists():
        run(['age-keygen', '-o', key], sensitive=True)
    os.chmod(key, 0o600)
    public_key = run(['age-keygen', '-y', key], capture=True, sensitive=True).strip()
    if recipient.exists():
        require(recipient.read_text().strip() == public_key, 'Backup identity does not match its saved recipient.')
    else:
        atomic_write(recipient, public_key + '\n')
    require(not directory.is_symlink() and not backup_directory(environment, 'app').is_symlink(),
            'Backup destination cannot be a symlink.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_directory(environment, 'app'), 0o700)
    os.chmod(directory, 0o700)


def service_backup_enabled(config, environment, service):
    spec = config['environments'][environment]
    return bool(spec.get('backupServices', {}).get(service, spec.get('backupEnabled', False)))


def configure_backups(config, environment):
    for service in ('app', 'chat'):
        if service_backup_enabled(config, environment, service):
            install_backup_timer(environment, service)


def install_backup_timer(environment, service):
    initialize_backup(environment, service)
    suffix = '' if service == 'app' else f'-{service}'
    unit = f'mushroom-backup-{environment}{suffix}'
    atomic_write(f'/etc/systemd/system/{unit}.service', '[Unit]\nDescription=Mushroom encrypted database backup\nAfter=docker.service network-online.target\n'
                 f'[Service]\nType=oneshot\nUMask=0077\nExecStart=/usr/local/sbin/mushroom backup --environment {environment} --service {service} --scheduled\n', 0o644)
    atomic_write(f'/etc/systemd/system/{unit}.timer', '[Unit]\nDescription=Daily Mushroom database backup\n[Timer]\nOnCalendar=hourly\nOnActiveSec=1min\nRandomizedDelaySec=5m\nPersistent=true\n[Install]\nWantedBy=timers.target\n', 0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', unit + '.timer'])



def set_backup_schedule(config, environment, service, enabled):
    unit = f'mushroom-backup-{environment}' + ('' if service == 'app' else f'-{service}')
    if enabled:
        install_backup_timer(environment, service)
    elif Path(f'/etc/systemd/system/{unit}.timer').is_file():
        run(['systemctl', 'disable', '--now', unit + '.timer'])
    config['environments'][environment].setdefault('backupServices', {})[service] = enabled
    save(config)


def offsite_config(environment):
    return STATE / 'backup-remotes' / f'{environment}.conf'


def private_file(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Select a regular private file.')
    metadata = path.stat()
    require(metadata.st_uid == 0 and stat.S_IMODE(metadata.st_mode) == 0o600,
            'File must be owned by root with mode 0600.')
    return path


def guided_offsite_profile():
    kind = ask('Off-site backup destination (s3 / sftp)', 's3',
               lambda value: choice(value, ('s3', 'sftp')))
    if kind == 's3':
        endpoint = ask('S3 HTTPS endpoint')
        require(urlsplit(endpoint).scheme == 'https' and urlsplit(endpoint).hostname,
                'Enter the bucket provider HTTPS endpoint.')
        options = {'type': 's3', 'provider': 'Other', 'endpoint': endpoint,
                   'region': ask('S3 region', 'us-east-1'),
                   'access_key_id': ask('S3 access key ID', secret=True),
                   'secret_access_key': ask('S3 secret access key', secret=True),
                   'no_check_bucket': 'true'}
        require(options['access_key_id'] and options['secret_access_key'], 'Both S3 credentials are required.')
    else:
        options = {'type': 'sftp', 'host': ask('SFTP hostname', validator=hostname),
                   'user': ask('SFTP user'), 'port': ask('SFTP port', '22'),
                   'key_file': str(private_file(ask('Absolute path to SFTP private key'))),
                   'known_hosts_file': ask('Absolute path to verified SSH known_hosts'),
                   'shell_type': 'none'}
        require(options['user'] and options['port'].isdecimal() and 1 <= int(options['port']) <= 65535,
                'Enter an SFTP user and valid port.')
    profile = configparser.ConfigParser(interpolation=None)
    profile['backup'] = options
    output = io.StringIO()
    profile.write(output)
    return output.getvalue()



def configure_offsite(config, environment):
    supplied = ask('Private rclone config path (Enter for guided setup)')
    content = private_file(supplied).read_text() if supplied else guided_offsite_profile()
    profile = configparser.ConfigParser(interpolation=None)
    try:
        profile.read_string(content)
    except configparser.Error:
        raise InstallationError('Invalid rclone config.') from None
    require(profile.sections() == ['backup'] and not profile.defaults(), 'Use a single [backup] rclone remote.')
    remote = profile['backup']
    kind = remote.get('type')
    require(kind in ('s3', 'sftp'), 'Backup remote must be S3 or SFTP.')
    if kind == 'sftp':
        require(remote.get('known_hosts_file') and remote.get('key_file'),
                'SFTP needs a pinned known_hosts file and an SSH private key.')
        require(remote.get('shell_type') == 'none', 'Set shell_type = none for backup SFTP.')
        identity = private_file(remote['key_file'])
        host_keys = Path(remote['known_hosts_file'])
        require(identity.is_absolute() and host_keys.is_absolute() and host_keys.is_file() and not host_keys.is_symlink()
                and host_keys.stat().st_uid == 0 and not host_keys.stat().st_mode & 0o022,
                'Use absolute paths and a root-owned known_hosts file that other users cannot modify.')
    else:
        require(urlsplit(remote.get('endpoint', '')).scheme == 'https', 'S3 needs an explicit HTTPS endpoint.')
    prefix = ask('Remote folder (S3: bucket/prefix, SFTP: relative folder)')
    require(all(part not in ('.', '..') and re.fullmatch(r'[a-zA-Z0-9._-]+', part)
                for part in prefix.split('/')), 'Use a relative remote path without dot segments.')
    if shutil.which('rclone') is None:
        run(['apt-get', 'install', '-y', '--no-install-recommends', 'rclone'])
    directory = offsite_config(environment).parent
    directory.mkdir(exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='verify-', dir=directory) as temporary:
        path = Path(temporary) / 'remote.conf'
        atomic_write(path, content)
        run(['rclone', '--config', path, '--contimeout', '15s', '--timeout', '1m',
             'lsf', '--max-depth', '1', 'backup:' + prefix], capture=True, sensitive=True)
        os.replace(path, offsite_config(environment))
    config['environments'][environment]['backupRemotePrefix'] = prefix
    save(config)
    print('Backup destination configured. Run a backup to verify upload and read-back.')


def remote_arguments(config, environment, service, filename=''):
    prefix = config['environments'][environment].get('backupRemotePrefix')
    require(prefix, 'No off-site backup destination configured for this environment.')
    command = ['rclone', '--config', private_file(offsite_config(environment)),
               '--contimeout', '15s', '--timeout', '2m', '--retries', '2']
    return command, f'backup:{prefix}/{environment}/{service}/{filename}'


def encrypted_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def transfer_backup(config, environment, service, path):
    try:
        upload_backup(config, environment, service, path)
    except (InstallationError, OSError):
        directory = backup_directory(environment, service)
        if directory.is_dir() and not directory.is_symlink():
            atomic_write(directory / 'last-error', 'offsite-failed\n')
        raise


def upload_backup(config, environment, service, path):
    path = Path(path)
    require(path.parent == backup_directory(environment, service) and not path.is_symlink() and path.is_file()
            and BACKUP_NAME.fullmatch(path.name), 'Select a saved encrypted backup for this environment and service.')
    command, destination = remote_arguments(config, environment, service, path.name)
    expected = encrypted_hash(path)
    run(command + ['copyto', '--immutable', path, destination], sensitive=True)
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen([str(arg) for arg in command + ['cat', destination]], stdout=subprocess.PIPE,
                              stderr=errors) as download:
            actual = hashlib.file_digest(download.stdout, 'sha256').hexdigest()
            require(download.wait() == 0 and actual == expected,
                    'Off-site read-back did not match the local encrypted archive.')
    atomic_write(path.parent / 'last-offsite-success', json.dumps({
        'archive': path.name, 'sha256': expected, 'time': datetime.now(timezone.utc).isoformat()}) + '\\n')
    error_path = path.parent / 'last-error'
    if error_path.is_file() and error_path.read_text().strip() == 'offsite-failed':
        error_path.unlink()
    print(f'Off-site backup verified: {path.name}')


def restore_offsite(config, environment, service, name, identity=None):
    require(BACKUP_NAME.fullmatch(name), 'Select the exact encrypted archive filename.')
    command, source = remote_arguments(config, environment, service, name)
    with tempfile.TemporaryDirectory(prefix='restore-', dir=STATE) as temporary:
        path = Path(temporary) / name
        run(command + ['copyto', source, path], sensitive=True)
        restore_isolated(config, environment, path, service, identity)


def panel_offsite(config, environment):
    configure_offsite(config, environment)


def panel_retry_transfer(config, environment):
    service = ask('Database (app / chat)', 'app', lambda value: choice(value, ('app', 'chat')))
    archives = list_backups(environment, service)
    require(archives, 'No encrypted archive is available.')
    index = ask('Archive number', '1',
                lambda value: int(value) if value.isdecimal() else choice(value, []))
    require(1 <= index <= len(archives), 'Archive number does not exist.')
    transfer_backup(config, environment, service, archives[index - 1])



def list_backups(environment, service):
    directory = backup_directory(environment, service)
    archives = sorted((p for p in directory.glob('*.dump.age')
                       if BACKUP_NAME.fullmatch(p.name) and not p.is_symlink() and p.is_file()), reverse=True)
    stamp = directory / 'last-success'
    offsite = directory / 'last-offsite-success'
    print('Last verified off-site archive: ' + (json.loads(offsite.read_text())['archive'] if offsite.is_file() else 'none'))
    print('Last local success: ' + (stamp.read_text().strip() if stamp.is_file() else 'none'))
    for index, path in enumerate(archives, start=1):
        print(f'{index}. {path.name} ({path.stat().st_size:,} bytes)')
    return archives


@contextmanager
def pg_client(config, environment, target=None, *, service='app', network='host'):
    spec = config['environments'][environment]
    if target is None and service == 'chat':
        values = load_env(Path(spec['home']) / 'mushroom' / environment / 'runtime.env')
        communications = bool(values.get('CHAT_DB_PASSWORD'))
        host = 'communications-postgres' if communications else values.get('EJABBERD_SQL_HOST')
        if host in ('postgres', 'chat-postgres', 'communications-postgres'):
            labels = ['--filter', f'label=com.docker.compose.project=mushroom-{environment}',
                      '--filter', f'label=com.docker.compose.service={host}']
            containers = run(['docker', 'ps', '-q', *labels], capture=True).split()
            require(len(containers) == 1, 'Expected one running PostgreSQL container for chat backup.')
            user, database = ('mushroom_communications', 'mushroom_communications') if communications else (
                values['EJABBERD_SQL_USER'], values['EJABBERD_SQL_DB'])
            yield ['docker', 'exec', '-i', containers[0]], ['--username', user, '--dbname', database]
            return
        host = hostname(host or '')
        user, password, db = (quote(values[key], safe='') for key in (
            'EJABBERD_SQL_USER', 'EJABBERD_SQL_PASSWORD', 'EJABBERD_SQL_DB'))
        tls = 'require' if environment == 'production' else 'prefer'
        target = f'postgresql://{user}:{password}@{host}/{db}?sslmode={tls}'
    if local_database(config, environment) and target is None:
        source = Path(spec['home']) / 'mushroom' / environment / 'secrets/database_url'
        active = urlsplit(database_url(source.read_text().strip()))
        yield compose_command(config, environment) + ['exec', '-T', 'postgres'], [
            '--username', unquote(active.username), '--dbname', unquote(active.path[1:])]
        return
    source = Path(spec['home']) / 'mushroom' / environment / 'secrets/database_url'
    parsed = urlsplit(database_url(target if target is not None else source.read_text().strip(), production=not local_database(config, environment)))
    query = parse_qs(parsed.query)
    require(not set(query) - {'sslmode', 'schema', 'connection_limit', 'pool_timeout', 'connect_timeout', 'pgbouncer'},
            'Backup client URL contains unsupported options; configure provider backup access explicitly.')
    settings = {'PGHOST': parsed.hostname, 'PGPORT': str(parsed.port or 5432), 'PGDATABASE': unquote(parsed.path[1:]),
                'PGUSER': unquote(parsed.username), 'PGPASSWORD': unquote(parsed.password),
                'PGSSLMODE': query.get('sslmode', ['prefer'])[0], 'PGCONNECT_TIMEOUT': '15'}
    fd, temporary = tempfile.mkstemp(prefix='pg-client-', dir=STATE)
    os.close(fd)
    try:
        atomic_write(temporary, ''.join(f'{key}={value}\n' for key, value in settings.items()))
        yield ['docker', 'run', '--rm', '-i', '--network', network, '--env-file', temporary,
               f'postgres:{spec.get("pgMajor", "16")}-alpine'], ['--dbname', settings['PGDATABASE']]
    finally:
        Path(temporary).unlink(missing_ok=True)


def binary_pipeline(left, right, *, stages=('source', 'destination')):
    # Both error streams are private temporary files, so a noisy failing process cannot deadlock the pipe.
    with tempfile.TemporaryFile() as left_errors, tempfile.TemporaryFile() as right_errors:
        producer = subprocess.Popen([str(arg) for arg in left], stdout=subprocess.PIPE, stderr=left_errors)
        try:
            consumer = subprocess.Popen([str(arg) for arg in right], stdin=producer.stdout,
                                        stdout=subprocess.DEVNULL, stderr=right_errors)
            producer.stdout.close()
            consumer_status = consumer.wait()
            producer_status = producer.wait()
            require(producer_status == 0 and consumer_status == 0,
                    f'{stages[0]} exited {producer_status}; {stages[1]} exited {consumer_status}. No success recorded.')
        finally:
            if producer.poll() is None:
                producer.kill()
                producer.wait()


def run_backup(config, environment, service):
    directory = backup_directory(environment, service)
    error_path = directory / 'last-error'
    try:
        backup(config, environment, service, initialize=True)
    except (InstallationError, OSError):
        if directory.is_dir() and not directory.is_symlink() and not error_path.exists():
            atomic_write(error_path, 'backup-failed\n')
        raise



def backup(config, environment, service='app', *, initialize=False):
    if initialize:
        initialize_backup(environment, service)
    key = backup_key(environment)
    require(key.is_file(), 'Backup identity is missing. Configure backups or recover the original age identity.')
    recipient = run(['age-keygen', '-y', key], capture=True).strip()
    directory = backup_directory(environment, service)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    filename = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4) + '.dump.age'
    destination = directory / filename
    temporary = directory / (filename + '.partial')
    try:
        with (pg_client(config, environment) if service == 'app' else pg_client(config, environment, service=service)) as (command, connection):
            binary_pipeline(command + ['pg_dump', '--format=custom', '--no-owner', '--no-acl', *connection],
                            ['age', '--recipient', recipient, '--output', temporary], stages=('pg_dump', 'age encryption'))
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        atomic_write(directory / 'last-success', datetime.now(timezone.utc).isoformat() + '\n')
        cutoff = datetime.now(timezone.utc).timestamp() - int(config['environments'][environment].get('backupRetentionDays', 14)) * 86400
        for previous in directory.glob('*.dump.age'):
            if previous != destination and not previous.is_symlink() and previous.is_file() \
                    and re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}\.dump\.age', previous.name) and previous.stat().st_mtime < cutoff:
                previous.unlink()
        print(f'Encrypted logical backup: {destination}. Keep a separate copy of the age key.')
        error_path = directory / 'last-error'
        error_path.unlink(missing_ok=True)
        if config['environments'][environment].get('backupRemotePrefix'):
            transfer_backup(config, environment, service, destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore(config, environment, archive, target_file):
    require(archive and target_file, 'Restore needs --archive and --target-url-file for an empty *_restore_test database.')
    target = private_file(target_file).read_text().strip()
    parsed = urlsplit(database_url(target, production=not local_database(config, environment)))
    require(re.fullmatch(r'[a-z][a-z0-9_]*_restore_test', unquote(parsed.path[1:])), 'Restore target database name must end in _restore_test.')
    original = Path(config['environments'][environment]['home']) / 'mushroom' / environment / 'secrets/database_url'
    current = urlsplit(original.read_text().strip())
    require((parsed.hostname, parsed.port or 5432, unquote(parsed.path)) != (current.hostname, current.port or 5432, unquote(current.path)), 'Restore cannot target the source database.')
    with pg_client(config, environment, target) as (command, connection):
        restore_archive(command, connection, archive, backup_key(environment))
    print('Restore drill completed in the separate empty test database.')


RESTORED_OBJECTS = (
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
    "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' "
    "AND c.relkind IN ('r','p','S','v','m','f')"
)


def object_count(command, connection):
    return int(run(command + ['psql', *connection, '-At', '-v', 'ON_ERROR_STOP=1',
                              '-c', RESTORED_OBJECTS], capture=True, sensitive=True).strip())


def restore_archive(command, connection, archive, key):
    require(Path(key).is_file() and Path(archive).is_file(), 'Backup archive or decryption key is missing.')
    require(object_count(command, connection) == 0, 'Restore target is not empty; no data was changed.')
    # Verify the authenticated tail before pg_restore can commit a valid-looking prefix.
    with open(os.devnull, 'wb') as sink:
        verified = subprocess.run(['age', '--decrypt', '--identity', str(key), str(archive)],
                                  stdout=sink, stderr=subprocess.DEVNULL)
    require(verified.returncode == 0, 'Archive authentication failed. Check the identity and encrypted file.')
    binary_pipeline(['age', '--decrypt', '--identity', key, archive],
                    command + ['pg_restore', *connection, '--single-transaction', '--exit-on-error', '--no-owner', '--no-acl'],
                    stages=('age decryption', 'pg_restore'))
    return object_count(command, connection)


def restore_isolated(config, environment, archive, service='app', identity=None):
    key = private_file(identity) if identity else backup_key(environment)
    archive = Path(archive)
    require(key.is_file() and archive.is_file(), 'Backup archive or decryption key is missing.')
    name = f'mushroom-{environment}-{service}-restore-{secrets.token_hex(5)}'
    database = 'mushroom_restore_test'
    image = f"postgres:{config['environments'][environment].get('pgMajor', '16') if service == 'app' else '16'}-alpine"
    run(['docker', 'run', '-d', '--name', name, '--network', 'none', '--label',
         'mushroom.restore-test=true', '-e', f'POSTGRES_DB={database}',
         '-e', 'POSTGRES_PASSWORD=' + secrets.token_hex(32), image], capture=True, sensitive=True)
    command = ['docker', 'exec', '-i', name]
    connection = ['--username', 'postgres', '--dbname', database]
    try:
        for _ in range(60):
            try:
                run(command + ['pg_isready', '-h', '127.0.0.1', *connection], capture=True, sensitive=True)
                break
            except InstallationError:
                time.sleep(1)
        else:
            raise InstallationError('Isolated PostgreSQL did not become ready within 60 seconds.')
        count = restore_archive(command, connection, archive, key)
        print(f'Restored {count} database objects in {name}. Remove when finished: sudo docker rm -fv {name}')
    except BaseException:
        run(['docker', 'rm', '-fv', name], capture=True, sensitive=True)
        raise


@contextmanager
def lock():
    import fcntl
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'installation.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallationError('Another manager operation is running.') from None
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=['install', 'panel', 'update', 'setup-chat', 'setup-storage', 'setup-analytics', 'setup-bale', 'register-bale-webhook', 'configure', 'doctor', 'verify', 'github', 'status', 'logs', 'restart', 'backup', 'restore', 'backup-list', 'backup-schedule', 'add-environment', 'monitor', 'audit-env'])
    parser.add_argument('--version', nargs='?', const='__current__', help='Print tool version, or select a release for update')
    parser.add_argument('--check', action='store_true', help='Only check for an installer update')
    parser.add_argument('--yes', action='store_true', help='Confirm an explicitly requested unattended downgrade')
    parser.add_argument('--environment', choices=ENVIRONMENTS)
    parser.add_argument('--communications-env', help='Private file with MiroTalk image, HTTPS origin, TURN and S3 settings')
    parser.add_argument('--repository', default=str(BUNDLE))
    parser.add_argument('--current', action='store_true', help='Audit existing .env key names without displaying values')
    parser.add_argument('--archive', help='Encrypted .dump.age backup to restore into a separate test database')
    parser.add_argument('--target-url-file', help='Protected file containing the empty restore-test database URL')
    parser.add_argument('--service', choices=('app', 'chat'), default='app')
    parser.add_argument('--schedule', choices=('on', 'off'))
    parser.add_argument('--scheduled', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--identity-file')
    parser.add_argument('--offsite', action='store_true')
    args = parser.parse_args()
    if args.version == '__current__':
        print(updater.current_version(BUNDLE))
        return
    if (args.version or args.check or args.yes) and args.command != 'update':
        parser.error('--version TAG, --check and --yes require update')
    if args.communications_env and (args.command not in ('install', 'setup-chat') or not args.environment):
        parser.error('--communications-env requires install or setup-chat and --environment')
    if args.command == 'audit-env':
        audit_environment(args.repository, args.current)
        return
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Run this manager as root on Ubuntu 24.04 (sudo).')
    require(os.uname().machine == 'x86_64', 'Current GitHub release images require an x86_64/amd64 host.')
    require('VERSION_ID="24.04"' in Path('/etc/os-release').read_text() and 'ID=ubuntu' in Path('/etc/os-release').read_text(),
            'Only Ubuntu 24.04 is supported.')
    state_file = STATE / 'installation.json'
    if args.command == 'panel' or (args.command is None and state_file.is_file()):
        require(state_file.is_file(), 'Run install first.')
        config = json.loads(state_file.read_text())
        require(config.get('version') == 1, 'Unsupported installation state version.')
        panel(config)
        return
    if args.command == 'monitor':
        require(state_file.is_file(), 'Run install first.')
        monitor(json.loads(state_file.read_text()))
        return
    with lock():
        if args.command == 'update':
            try:
                updater.update(args.version, args.check, args.yes)
            except updater.UpdateError as error:
                raise InstallationError(str(error)) from None
            return
        path = STATE / 'installation.json'
        if args.command is None:
            args.command = 'panel' if path.exists() else 'install'
        if path.exists():
            config = json.loads(path.read_text())
            require(config.get('version') == 1, 'Unsupported installation state version.')
        else:
            require(args.command == 'install', 'Run install first.')
            config = new_configuration()
            save(config)
        if args.command == 'install':
            require(not args.communications_env or args.environment in config['environments'],
                    'Select a configured environment.')
            install(config, selected=args.environment if args.communications_env else None,
                    communications_profile=args.communications_env)
        elif args.command == 'add-environment':
            add_environment(config, args.environment)
        elif args.command == 'setup-chat':
            setup_chat(config, args.environment, args.communications_env)
        elif args.command == 'setup-storage':
            require(args.environment == 'dev', 'Use setup-storage --environment dev; production storage remains disabled.')
            setup_storage(config)
        elif args.command == 'setup-analytics':
            require(args.environment == 'dev', 'Use setup-analytics --environment dev; Production stays unchanged.')
            setup_analytics(config)
        elif args.command == 'setup-bale':
            require(args.environment == 'dev', 'Use setup-bale --environment dev.')
            setup_bale(config)
        elif args.command == 'register-bale-webhook':
            require(args.environment == 'dev', 'Use register-bale-webhook --environment dev.')
            setup_bale_webhook(config)
        elif args.command == 'configure':
            configure(config, args.environment)
        elif args.command in ('doctor', 'verify'):
            doctor(config, args.command == 'verify')
        elif args.command == 'github':
            github_handoff(config)
        else:
            require(args.environment in config['environments'], 'Select an installed --environment.')
            if args.command == 'backup':
                if args.scheduled:
                    if not service_backup_enabled(config, args.environment, args.service):
                        return
                    stamp = backup_directory(args.environment, args.service) / 'last-success'
                    if stamp.is_file() and time.time() - stamp.stat().st_mtime < 23.5 * 3600:
                        spec = config['environments'][args.environment]
                        marker = stamp.parent / 'last-offsite-success'
                        if spec.get('backupRemotePrefix') and (
                                not marker.is_file() or marker.stat().st_mtime < max(
                                    stamp.stat().st_mtime, offsite_config(args.environment).stat().st_mtime)):
                            candidates = sorted(path for path in stamp.parent.glob('*.dump.age')
                                                if BACKUP_NAME.fullmatch(path.name) and not path.is_symlink())
                            if candidates:
                                transfer_backup(config, args.environment, args.service, candidates[-1])
                        return
                    if args.service == 'app' and local_database(config, args.environment):
                        spec = config['environments'][args.environment]
                        root = Path(spec['home']) / 'mushroom' / args.environment
                        deployed = root / ('workspace/deploy/vps/compose.dev.yaml' if args.environment == 'dev'
                                           else 'state/current')
                        if not deployed.is_file():
                            print('Backup pending: waiting for the first deployment.')
                            return
                run_backup(config, args.environment, args.service)
                return
            if args.command == 'restore':
                require(args.archive, 'Select --archive or use the backup panel.')
                if args.target_url_file:
                    require(args.service == 'app' and not args.identity_file,
                            'Explicit target URL uses the application database and saved identity.')
                    restore(config, args.environment, args.archive, args.target_url_file)
                else:
                    if args.offsite:
                        restore_offsite(config, args.environment, args.service, args.archive, args.identity_file)
                    else:
                        restore_isolated(config, args.environment, args.archive, args.service, args.identity_file)
                return
            if args.command == 'backup-list':
                list_backups(args.environment, args.service)
                return
            if args.command == 'backup-schedule':
                require(args.schedule, 'Specify --schedule on or off.')
                set_backup_schedule(config, args.environment, args.service, args.schedule == 'on')
                return
            command = compose_command(config, args.environment)
            command.extend({'status': ['ps'], 'logs': ['logs', '--tail', '100'], 'restart': ['restart']}[args.command])
            run(command)


def failure_message(error):
    if isinstance(error, InstallationError):
        return str(error)
    if isinstance(error, KeyboardInterrupt):
        return 'Installation interrupted. Rerun install to resume saved steps.'
    if isinstance(error, EOFError):
        return 'Terminal input ended. Reconnect your SSH terminal and rerun install interactively.'
    trace = error.__traceback__
    while trace and trace.tb_next:
        trace = trace.tb_next
    location = f'{Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno}' if trace else 'unknown location'
    number = f', errno={error.errno}' if isinstance(error, OSError) and error.errno is not None else ''
    # Exception messages and locals may contain passwords, URLs or raw undecodable secret bytes.
    return f'Operation failed ({type(error).__name__}{number}, {location}). Rerun install to resume saved steps; report this error code/location if it repeats.'


def cli():
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(failure_message(error), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    cli()
