"""Guided communications configuration and checks. Secrets stay in private environment files."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import secrets
import socket
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


def root_for(config, environment):
    return Path(config['environments'][environment]['home']) / 'mushroom' / environment


def receipt(m, environment, **updates):
    path = m.STATE / 'communications' / (environment + '.json')
    value = json.loads(path.read_text()) if path.is_file() else {}
    value.update(updates)
    value['updatedAt'] = datetime.now(timezone.utc).isoformat()
    m.atomic_write(path, json.dumps(value, indent=2) + '\n')
    return value


def fingerprint(m, values):
    selected = {key: values.get(key, '') for key in sorted(m.COMMUNICATIONS_INPUTS)}
    if selected.get('CHAT_VIDEO_IMAGE_MODE') == 'build':
        selected['CHAT_VIDEO_IMAGE'] = 'source-build'
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def save_call_mode(m, root, environment, values, mode):
    values['CHAT_VIDEO_ENABLED'] = 'true' if mode == 'enabled' else 'false'
    values.pop('CHAT_VIDEO_TEST_UNTIL', None)
    updates = {'callMode': mode + '-pending-deployment', 'browserTestConfirmed': mode == 'enabled',
               'verified': mode == 'enabled', 'fingerprint': fingerprint(m, values)}
    if mode == 'trial':
        until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat().replace('+00:00', 'Z')
        values['CHAT_VIDEO_TEST_UNTIL'] = until
        updates.update(trialUntil=until, trialFingerprint=fingerprint(m, values))
    else:
        updates.update(trialUntil=None, trialFingerprint=None)
    m.atomic_write(root / 'runtime.env', ''.join(f'{key}={value}\n' for key, value in values.items()))
    m.os.chown(root / 'runtime.env', root.stat().st_uid, root.stat().st_gid)
    receipt(m, environment, **updates)
    print(f'{mode} saved; the running API changes after the next environment deployment.')
    if mode == 'trial':
        print('The trial expires automatically at ' + updates['trialUntil'] + '. Deploy promptly, then test two browsers on separate networks using relay-only ICE.')


@contextmanager
def environment_lock(m, root):
    """Use the same lock as CD even before its first successful deployment."""
    import fcntl
    path = root / 'state/deploy.lock'
    m.require(not path.is_symlink() and not path.parent.is_symlink(), 'Deployment lock cannot be a symlink.')
    path.parent.mkdir(parents=True, exist_ok=True)
    m.os.chown(path.parent, root.stat().st_uid, root.stat().st_gid)
    m.require(not (root / '.deploy.lock').exists(), 'An older deployment is active.')
    with path.open('a') as handle:
        m.os.chown(path, root.stat().st_uid, root.stat().st_gid)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise m.InstallationError('Deployment or recovery is active; retry communications afterwards.') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def free_range(m, start, count, *, occupied=None):
    occupied = set(occupied or ())
    for candidate in range(start, 65536 - count + 1):
        if any(port in occupied for port in range(candidate, candidate + count)):
            continue
        sockets = []
        try:
            for port in range(candidate, candidate + count):
                for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM):
                    connection = socket.socket(socket.AF_INET, kind)
                    sockets.append(connection)
                    connection.bind(('0.0.0.0', port))
            return candidate
        except OSError:
            continue
        finally:
            for connection in sockets:
                connection.close()
    raise m.InstallationError('No available communications port range. Review listening services.')


def guided_profile(m, config, environment, *, edit=False):
    root = root_for(config, environment)
    values = m.load_env(root / 'runtime.env')
    draft = root / 'communications/profile.env'
    m.require(not draft.parent.is_symlink(), 'Communications directory cannot be a symlink.')
    supplied = m.load_env(draft) if draft.is_file() else {}
    supplied = {key: value for key, value in supplied.items() if key in m.COMMUNICATIONS_INPUTS}
    existing = {key: value for key, value in values.items() if key in m.COMMUNICATIONS_INPUTS}
    supplied.update(existing)

    def save(key, value):
        supplied[key] = value
        m.atomic_write(draft, ''.join(f'{name}={item}\n' for name, item in supplied.items()))
        receipt(m, environment, phase='configuring', configured=False, verified=False)

    def field(key, label, default='', validator=None, secret=False):
        if supplied.get(key) and not edit:
            print(f'{label}: retained' if secret else f'{label}: {supplied[key]}')
            return supplied[key]
        if supplied.get(key) and secret and m.ask('Replace ' + label, 'no', lambda value: m.choice(value, ['yes', 'no'])) != 'yes':
            return supplied[key]
        if not secret:
            default = supplied.get(key, default)
        value = m.ask(label, default, validator, secret=secret)
        save(key, value)
        return value

    print(f'Communications for {environment}; environment root: {root}')
    print('Existing credentials are retained. Incomplete setup resumes from its saved private profile.')
    field('CHAT_VIDEO_IMAGE_MODE', 'MiroTalk image mode (build / registry)',
          'registry' if supplied.get('CHAT_VIDEO_IMAGE') else ('build' if environment == 'dev' else 'registry'),
          lambda value: m.choice(value, ['build', 'registry']))
    if supplied['CHAT_VIDEO_IMAGE_MODE'] == 'build':
        m.require(environment == 'dev', 'Server image builds currently require the dev deployment workflow.')
        image = supplied.get('CHAT_VIDEO_IMAGE', '')
        save('CHAT_VIDEO_IMAGE', image if image.startswith(f'mushroom-{environment}-mirotalk:') else f'mushroom-{environment}-mirotalk:pending')
    else:
        field('CHAT_VIDEO_IMAGE', 'Published MiroTalk image with @sha256 digest')
    domain = config['environments'][environment]['domains']['app']
    base = domain.split('.', 1)[1]
    field('CHAT_VIDEO_ORIGIN', 'Dedicated HTTPS call origin (create its DNS record on this server)',
          f'https://calls-{environment}.{base}')
    field('CHAT_TURN_MANAGED', 'Run Coturn on this VPS (true / false)', 'true',
          lambda value: m.choice(value, ['true', 'false']))
    if supplied['CHAT_TURN_MANAGED'] == 'true':
        reserved = set()
        for name in config['environments']:
            other = root_for(config, name) / 'runtime.env'
            if name != environment and other.is_file():
                settings = m.load_env(other)
                if settings.get('CHAT_TURN_LISTEN_PORT'):
                    reserved.add(int(settings['CHAT_TURN_LISTEN_PORT']))
                if settings.get('CHAT_TURN_MIN_PORT') and settings.get('CHAT_TURN_MAX_PORT'):
                    reserved.update(range(int(settings['CHAT_TURN_MIN_PORT']), int(settings['CHAT_TURN_MAX_PORT']) + 1))
        suggestion = config.get('sshHost', '')
        try:
            suggestion = str(ipaddress.ip_address(suggestion))
        except ValueError:
            suggestion = ''
        def public_ip(value):
            address = ipaddress.ip_address(value)
            m.require(address.version == 4 and address.is_global, 'Enter this VPS public IPv4 address. For NAT, use its public mapping.')
            return str(address)
        field('CHAT_TURN_PUBLIC_IP', 'Public IPv4 for TURN; confirm the public mapping if this VPS uses NAT', suggestion, public_ip)
        if not supplied.get('CHAT_TURN_LISTEN_PORT'):
            save('CHAT_TURN_LISTEN_PORT', str(free_range(m, 3478, 1, occupied=reserved)))
        reserved.add(int(supplied['CHAT_TURN_LISTEN_PORT']))
        if not supplied.get('CHAT_TURN_MIN_PORT'):
            start = free_range(m, 49160, 41, occupied=reserved)
            save('CHAT_TURN_MIN_PORT', str(start))
            save('CHAT_TURN_MAX_PORT', str(start + 40))
        if not supplied.get('CHAT_TURN_MAX_PORT'):
            save('CHAT_TURN_MAX_PORT', str(int(supplied['CHAT_TURN_MIN_PORT']) + 40))
        host = supplied['CHAT_TURN_PUBLIC_IP']
        port = supplied['CHAT_TURN_LISTEN_PORT']
        save('CHAT_STUN_URL', f'stun:{host}:{port}')
        save('CHAT_TURN_URLS', f'turn:{host}:{port}?transport=udp,turn:{host}:{port}?transport=tcp')
        if not supplied.get('CHAT_TURN_SECRET'):
            save('CHAT_TURN_SECRET', secrets.token_urlsafe(32))
        print(f'TURN requires inbound TCP/UDP {port} and UDP {supplied["CHAT_TURN_MIN_PORT"]}-{supplied["CHAT_TURN_MAX_PORT"]}.')
        print('Configure the VPS provider firewall and NAT forwarding for these ports when applicable.')
    else:
        field('CHAT_STUN_URL', 'Existing STUN URL')
        field('CHAT_TURN_URLS', 'Existing TURN URLs, comma separated')
        field('CHAT_TURN_SECRET', 'Existing TURN shared secret (hidden)', secret=True)
    prefix = 'ARVAN' if values.get('PRODUCT_MEDIA_STORAGE_MODE') == 'arvancloud' else 'R2'
    field('CHAT_S3_ENDPOINT', 'Communications S3 HTTPS endpoint', values.get(prefix + '_ENDPOINT', ''))
    field('CHAT_S3_REGION', 'Communications S3 signing region', values.get(prefix + '_REGION') or 'us-east-1')
    field('CHAT_S3_BUCKET', 'Dedicated private communications bucket (create it at your provider)', f'mushroom-{environment}-communications')
    for suffix in ('ACCESS_KEY', 'SECRET_KEY'):
        field('CHAT_S3_' + suffix, 'Communications S3 ' + suffix.lower() + ' (hidden)', secret=True)
    save('CHAT_COMMUNICATIONS_SCHEMA', '2')
    return draft


def write_turn(m, root, values, owner):
    if values.get('CHAT_TURN_MANAGED') != 'true':
        return
    m.require(all(values.get(key) for key in (
        'CHAT_TURN_PUBLIC_IP', 'CHAT_TURN_LISTEN_PORT', 'CHAT_TURN_MIN_PORT', 'CHAT_TURN_MAX_PORT',
    )), 'Managed TURN profile needs public IP, listener and relay range settings.')
    address = ipaddress.ip_address(values['CHAT_TURN_PUBLIC_IP'])
    m.require(address.version == 4 and address.is_global, 'TURN needs the VPS public IPv4 address.')
    listen, low, high = (int(values[key]) for key in ('CHAT_TURN_LISTEN_PORT', 'CHAT_TURN_MIN_PORT', 'CHAT_TURN_MAX_PORT'))
    m.require(1024 <= listen <= 65535 and 1024 <= low <= high <= 65535 and high - low <= 1000
              and listen not in range(low, high + 1), 'Invalid TURN listener or relay range.')
    text = '\n'.join([
        f'listening-port={listen}', f'min-port={low}', f'max-port={high}',
        'listening-ip=0.0.0.0', f'external-ip={address}',
        f'realm={urlsplit(values["CHAT_VIDEO_ORIGIN"]).hostname}',
        'fingerprint', 'use-auth-secret', f'static-auth-secret={values["CHAT_TURN_SECRET"]}',
        'no-tls', 'no-dtls', 'no-cli', 'no-loopback-peers', 'no-multicast-peers',
        'denied-peer-ip=10.0.0.0-10.255.255.255', 'denied-peer-ip=172.16.0.0-172.31.255.255',
        'denied-peer-ip=192.168.0.0-192.168.255.255', 'user-quota=8', 'total-quota=128',
        'log-file=stdout',
    ]) + '\n'
    target = root / 'communications/turnserver.conf'
    m.require(not target.parent.is_symlink(), 'Communications directory cannot be a symlink.')
    m.atomic_write(target, text)
    m.os.chown(target, *owner)
    for binding in (f'{listen}/tcp', f'{listen}/udp', f'{low}:{high}/udp'):
        m.run(['ufw', 'allow', binding], capture=True)


def s3_request(values, method, key, payload=b''):
    """AWS Signature V4 path-style request with credentials kept out of URL/logs."""
    endpoint = urlsplit(values['CHAT_S3_ENDPOINT'])
    if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in ('', '/'):
        raise ValueError('S3 endpoint must be a plain HTTPS origin.')
    path = '/' + quote(values['CHAT_S3_BUCKET'], safe='') + '/' + quote(key, safe='/')
    now = datetime.now(timezone.utc)
    stamp, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
    digest = hashlib.sha256(payload).hexdigest()
    headers = {'host': endpoint.netloc, 'x-amz-content-sha256': digest, 'x-amz-date': stamp}
    signed = ';'.join(headers)
    canonical = '\n'.join([method, path, '', ''.join(f'{name}:{value}\n' for name, value in headers.items()), signed, digest])
    scope = f'{day}/{values["CHAT_S3_REGION"]}/s3/aws4_request'
    message = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signing = ('AWS4' + values['CHAT_S3_SECRET_KEY']).encode()
    for item in (day, values['CHAT_S3_REGION'], 's3', 'aws4_request'):
        signing = hmac.new(signing, item.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, message.encode(), hashlib.sha256).hexdigest()
    headers['Authorization'] = f'AWS4-HMAC-SHA256 Credential={values["CHAT_S3_ACCESS_KEY"]}/{scope}, SignedHeaders={signed}, Signature={signature}'
    request = Request(f'https://{endpoint.netloc}{path}', data=payload if method == 'PUT' else None, headers=headers, method=method)
    with urlopen(request, timeout=15) as response:
        return response.read(1024)


def check_storage(values):
    key = '.mushroom-check/' + secrets.token_hex(16)
    payload = secrets.token_bytes(32)
    try:
        s3_request(values, 'PUT', key, payload)
        if s3_request(values, 'GET', key) != payload:
            raise ValueError('Storage roundtrip mismatch')
        endpoint = urlsplit(values['CHAT_S3_ENDPOINT'])
        anonymous = f'https://{endpoint.netloc}/' + quote(values['CHAT_S3_BUCKET'], safe='') + '/' + quote(key, safe='/')
        try:
            with urlopen(anonymous, timeout=15):
                pass
        except HTTPError as error:
            if error.code not in (401, 403, 404):
                raise ValueError('Unable to verify private object access') from None
        else:
            raise ValueError('Communications bucket permits anonymous object reads')
    finally:
        s3_request(values, 'DELETE', key)


def diagnose(m, config, environment):
    root = root_for(config, environment)
    values = m.load_env(root / 'runtime.env')
    results = {}
    host = urlsplit(values.get('CHAT_VIDEO_ORIGIN', '')).hostname
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        expected = values.get('CHAT_TURN_PUBLIC_IP')
        results['dns'] = bool(addresses) and (not expected or expected in addresses)
    except (OSError, TypeError):
        results['dns'] = False
    try:
        with urlopen(values['CHAT_VIDEO_ORIGIN'] + '/mushroom-call.html', timeout=10) as response:
            results['https'] = response.status == 200
    except Exception:
        results['https'] = False
    try:
        with urlopen(values['CHAT_VIDEO_ORIGIN'] + '/socket.io/?EIO=4&transport=polling', timeout=10) as response:
            results['signaling'] = response.status == 200 and response.read(1024).startswith(b'0{')
    except Exception:
        results['signaling'] = False
    try:
        check_storage(values)
        results['storage'] = True
    except Exception:
        results['storage'] = False
    try:
        if values.get('CHAT_TURN_MANAGED') == 'true':
            turn_host, turn_port = '127.0.0.1', int(values['CHAT_TURN_LISTEN_PORT'])
        else:
            first = values['CHAT_TURN_URLS'].split(',')[0]
            parsed = urlsplit(first.replace('turns:', 'turns://', 1) if first.startswith('turns:') else first.replace('turn:', 'turn://', 1))
            turn_host, turn_port = parsed.hostname, parsed.port or (5349 if parsed.scheme == 'turns' else 3478)
        with socket.create_connection((turn_host, turn_port), timeout=5):
            results['turn_listener'] = True
    except (OSError, ValueError, KeyError):
        results['turn_listener'] = False
    api = config['environments'][environment]['domains'].get('api', '')
    try:
        with urlopen('https://' + api + '/v1/health/chat', timeout=10) as response:
            results['messaging'] = response.status == 200
    except Exception:
        results['messaging'] = False
    current = fingerprint(m, values)
    previous = receipt(m, environment)
    accepted = previous.get('browserTestConfirmed', False) and previous.get('fingerprint') == current
    receipt(m, environment, checks=results, automaticallyVerified=all(results.values()),
            verified=all(results.values()) and accepted, browserTestConfirmed=accepted, fingerprint=current)
    for name, passed in results.items():
        print(f'{name}: ' + ('passed' if passed else 'pending / failed'))
    if not results['dns']:
        print('Point the dedicated call DNS record at this VPS public address; use DNS-only routing for TURN.')
    if not results['https']:
        print('Check public TCP 80/443, Caddy, call DNS, and successful deployment of MiroTalk.')
    if not results['storage']:
        print('Check private bucket (anonymous reads must fail), signing region, endpoint, and object put/get/delete permissions.')
    if not results['turn_listener']:
        print('Check Coturn deployment, listener port, and host/provider firewall rules.')
    print('TURN requires a two-network browser test with relay-only ICE; listener checks do not prove media delivery.')
    return results


def menu(m, config, environment):
    root = root_for(config, environment)
    services = ['mirotalk', 'communications-postgres', 'chat-scanner']
    while True:
        print(f'\nCommunications ({environment})\n[1] Setup / resume\n[2] Status\n[3] Recent redacted logs\n[4] Diagnose\n[5] Restart service\n[6] Calls: disabled / trial / enabled\n[7] Change configuration\n[q] Back')
        action = m.terminal_input('Select: ').strip()
        if action in ('q', ''):
            return
        try:
            values = m.load_env(root / 'runtime.env')
            available = services + (['coturn'] if values.get('CHAT_TURN_MANAGED') == 'true' else [])
            if action == '1':
                m.setup_chat(config, environment)
            elif action == '7':
                if m.typed_confirmation('Change communications configuration', environment, m.terminal_input):
                    m.setup_chat(config, environment, edit=True)
            elif action == '2':
                path = m.STATE / 'communications' / (environment + '.json')
                print(path.read_text() if path.is_file() else 'Communications setup has not completed.')
                try:
                    m.run(m.compose_command(config, environment) + ['ps', *available])
                except m.InstallationError:
                    print('Service status pending. Complete the environment deployment, then run Diagnose.')
            elif action == '3':
                values = m.load_env(root / 'runtime.env')
                output = m.run(m.compose_command(config, environment) + ['logs', '--tail', '80', *available], capture=True, sensitive=True)
                for key, value in values.items():
                    if value and any(part in key for part in ('SECRET', 'PASSWORD', 'ACCESS_KEY', 'TOKEN')):
                        output = output.replace(value, '[redacted]')
                        output = output.replace(quote(value, safe=''), '[redacted]')
                print(output)
            elif action == '4':
                with environment_lock(m, root):
                    diagnose(m, config, environment)
            elif action == '5':
                service = m.ask('Service', 'mirotalk', lambda value: m.choice(value, available))
                if m.typed_confirmation('Restart ' + service, environment, m.terminal_input):
                    with environment_lock(m, root):
                        m.run(m.compose_command(config, environment) + ['restart', service])
            elif action == '6':
                mode = m.ask('Call mode (disabled / trial / enabled)', 'disabled',
                             lambda value: m.choice(value, ['disabled', 'trial', 'enabled']))
                with environment_lock(m, root):
                    values = m.load_env(root / 'runtime.env')
                    if mode in ('trial', 'enabled'):
                        results = diagnose(m, config, environment)
                        if not all(results.values()):
                            save_call_mode(m, root, environment, values, 'disabled')
                            raise m.InstallationError('Resolve pending communications checks; call disable is saved for next deployment.')
                    if mode == 'enabled':
                        try:
                            record = receipt(m, environment)
                            m.require(record.get('trialFingerprint') == fingerprint(m, values),
                                      'Start a new trial and deploy this configuration before permanent activation.')
                            deadline = datetime.fromisoformat(values.get('CHAT_VIDEO_TEST_UNTIL', '').replace('Z', '+00:00'))
                            m.require(deadline > datetime.now(timezone.utc), 'The call trial expired. Start and deploy a new trial.')
                            api = config['environments'][environment]['domains']['api']
                            with urlopen('https://' + api + '/v1/health/video', timeout=10) as response:
                                m.require(json.load(response).get('status') == 'ok', 'Deploy the active call trial before confirming it.')
                        except (ValueError, OSError, KeyError, TypeError, m.InstallationError):
                            save_call_mode(m, root, environment, values, 'disabled')
                            raise m.InstallationError('Call trial is not active or reachable. Check deployment and trial expiry.') from None
                        if not m.typed_confirmation('Confirm two browsers on different networks passed relay-only audio/video; cancel records disable for next deployment', environment, m.terminal_input):
                            mode = 'disabled'
                    save_call_mode(m, root, environment, values, mode)
            else:
                print('Unknown selection.')
        except m.InstallationError as error:
            print(str(error))
