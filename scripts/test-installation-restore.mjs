import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const suffix = randomUUID();
const server = `mushroom-restore-${suffix}`;
const network = `mushroom-restore-network-${suffix}`;
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', ...options });
  if (result.error || result.status !== 0) throw new Error(`Disposable restore fixture command failed: ${command}`);
  return result.stdout ?? '';
}
try {
  run('docker', ['network', 'create', network]);
  run('docker', ['run', '-d', '--name', server, '--network', network, '--network-alias', 'installer-db',
    '-e', 'POSTGRES_PASSWORD=restore-disposable-fixture', '-e', 'POSTGRES_DB=mushroom_source_installation_test', 'postgres:16-alpine']);
  // The image starts a temporary socket-only server during initdb; wait for final TCP startup.
  let ready = false;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (spawnSync('docker', ['exec', server, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres']).status === 0) { ready = true; break; }
    await new Promise((done) => setTimeout(done, 500));
  }
  if (!ready) throw new Error('Disposable restore server did not become ready.');
  for (const database of ['mushroom_corrupt_restore_test', 'mushroom_docker_restore_test']) {
    run('docker', ['exec', server, 'createdb', '-U', 'postgres', database]);
  }
  run(process.platform === 'win32' ? 'python' : 'python3', ['-B', 'installation/test_postgres_restore.py'], {
    cwd: root,
    env: { ...process.env, INSTALLATION_RESTORE_TEST: '1', INSTALLATION_RESTORE_NETWORK: network },
    stdio: 'inherit',
  });
} finally {
  spawnSync('docker', ['rm', '-fv', server], { encoding: 'utf8' });
  spawnSync('docker', ['network', 'rm', network], { encoding: 'utf8' });
}
