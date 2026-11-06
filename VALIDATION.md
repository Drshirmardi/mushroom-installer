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

## F12-T02-I11 — automatic publication

Local actionlint and Bash syntax PASS for reusable verification, serialized publication and draft-first assets. Public release publication waits for the full standalone Linux suite, fresh/legacy bootstrap containers, real chat engine SQL retention and encrypted PostgreSQL restore. Relevant main source changes trigger releases; documentation-only changes do not. Versions use v1.0.<publish-workflow-run-number>. Published assets are preserved on rerun and older reruns cannot replace Latest.

Hosted I10 exposed a test-fixture root assumption; ced049e models privileged status without requiring the entire unit suite to run as root. All22 updater cases also PASS as uid1000 in a disposable container. No production permission check was changed.

### Public acceptance — 2026-11-06

Release v1.0.1 points to source26c638764a9cf075bce6196f383ef3d7f7b930a5. Hosted run42168105175 PASS:90 unit/regression cases (88 PASS/2 dedicated drills), fresh and legacy bootstrap containers, real chat retention and PostgreSQL encrypted restore. Four public assets downloaded anonymously; SHA-256 matched. Real anonymous bootstrap into a disposable Ubuntu24.04 container, CLI version, update --check and repeated update PASS. Rerunning publish job127731224980 (retry127731682342) PASS without replacing published assets; archive digest remains d3eac5e21fde353acb1ac2a309167dd567a8870c4ea125a939db26938e388914. No live server or application deployment.
