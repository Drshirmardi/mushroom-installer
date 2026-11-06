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

## F12-T02-I10 — versioned self-update (2026-11-06)

Ubuntu24.04 full regression:88 cases (86 PASS/2 opt-in drills). New updater checks cover real relocated CLI version, legacy/repeated install/rollback, private byte/mode preservation, rejected paths/links/duplicates/unknown files and hashes, startup/activation failure, common manager lock and offline/panel cache. Disposable bootstrap rehearsal verifies fresh/legacy install, repeated activation and corrupt download rejection. Windows updater tests, shell syntax and private application contracts PASS. Hosted I09 run42166707841 PASS including real chat SQL retention and encrypted restore. No live VPS updated.
