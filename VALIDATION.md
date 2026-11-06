# Verification record

## F12-T02-I09 — standalone source extraction (2026-11-06)

Coordinator review: PASS. Explicit original bundle inputs only; no application source, private Git history or server credentials copied.

- Windows: 60 installer cases, 52 PASS / 8 platform or opt-in skips.
- Disposable Ubuntu 24.04: 68 cases, 66 PASS / 2 opt-in drills; real RSA, Caddy transport and Compose model included.
- Real isolated PostgreSQL encrypted backup/restore: PASS.
- Shared assets: all 18 own/upstream hashes PASS.
- Private application environment/production Compose assertions: both PASS against the external Mushroom checkout.
- Bundle build, relocated help and actionlint: PASS.
- Hosted real chat-engine rehearsal is retained in checks.yml and remains a publication gate for I11.

No live server operation performed.
