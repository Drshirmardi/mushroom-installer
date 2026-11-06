import { createPublicKey } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const [input, output] = process.argv.slice(2);
if (!input || !output || resolve(input) === resolve(output)) {
  throw new Error('Usage: node scripts/ejabberd-public-key.mjs <public.pem> <public.jwk>; use distinct paths.');
}
const key = createPublicKey(readFileSync(input));
if (key.asymmetricKeyType !== 'rsa' || (key.asymmetricKeyDetails?.modulusLength ?? 0) < 2048) {
  throw new Error('An RSA public key of at least 2048 bits is required.');
}
writeFileSync(output, JSON.stringify({ ...key.export({ format: 'jwk' }), alg: 'RS256', use: 'sig' }) + '\n', { flag: 'wx', mode: 0o644 });
