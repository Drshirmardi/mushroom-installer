# Consultation chat — ejabberd 26.09

Status: **PR28 UNDER REVIEW (2026-10-23)**; full clinical/browser consultation acceptance remains pending. [PR28](https://github.com/Drshirmardi/mushroom/pull/28) targets `dev`. This branch uses ejabberd only. Socket.IO gateway/clients/dependencies and the old public REST message-write operation are removed. Recorded verification includes local startup, engine/API/route health, disposable private-room provisioning/membership, WebSocket negotiation and clean shutdown. No VPS/production deployment or merge was performed.

## Local development: one command

With the application's normal local environment/database already prepared, start Docker Desktop/Engine and run `pnpm dev` from repository root. `pnpm dev:api` also prepares chat before starting only the API. No manual engine key/database provisioning or local Caddy is needed for these commands.

`scripts/dev.mjs` checks Docker/Compose, creates development-only credentials/RSA keys under ignored `.local/chat/`, starts `compose.local.yaml` and waits for service health, builds the shared client, then starts Turbo. The first invocation downloads/builds the engine; later invocations reuse Docker's build cache. Ctrl+C stops that checkout's chat containers while preserving keys and SQL volumes. It never migrates, seeds or changes application PostgreSQL. Direct package API start still requires explicit engine settings.

The local profile uses one isolated, unpublished PostgreSQL container for the engine archive, with separate per-checkout Docker volumes/project identity. This avoids provisioning/altering the application's existing database. Production can continue using a dedicated logical engine database on the same PostgreSQL service. Do not delete `.local/chat/` independently of its database; restore credentials if they are lost. Concurrent startup of the same checkout is fenced by a PID lock.

WebSocket is loopback-only `ws://127.0.0.1:5444/xmpp-websocket`; native API is loopback-only port5445. `DEV_CHAT_WS_PORT`/`DEV_CHAT_ADMIN_PORT` can resolve local port collisions. The backend accepts plain WS only for loopback in development; other environments require WSS. Docker must reach the host API via `host.docker.internal` and the API listens on0.0.0.0 for this local path. This helper is for a browser on the same host, not LAN/remote/Codespaces browser forwarding. Those use a proper public WSS deployment profile. Startup refuses non-development environment configuration.

The custom image strips upstream BEAM's privileged-port file capability at build time, since these high-port listeners run with `cap_drop: ALL` and `no-new-privileges`; runtime remains the unprivileged ejabberd user. If an older local image reports `erlexec: Error 1 executing ... beam.smp`, rerun `pnpm dev` to rebuild it without resetting volumes.

Verified on the owner's local Windows/Docker setup: `pnpm dev` starts both engine containers, shared-client build, API and all three Next servers; API live/ready/chat and patient/dashboard chat routes returned200. Native disposable private-room creation/reopen/member changes and cleanup, WebSocket feature negotiation, unauthorized policy401, a second cold start and Ctrl+C cleanup passed. Three targeted room-client regression tests and strict API/client compilation passed. Keys/volumes were retained; no application migration/seed or manual medical/financial mutation was performed. Full patient-to-doctor messaging/moderation/permission/browser acceptance remains a separate gate.

The setup steps below apply to deployed environments.

### Installer-managed deployments

The standalone installer now automates new engine provisioning. On an existing installation, transfer the matching updated installer bundle and run `sudo bash installation/install.sh setup-chat --environment dev`, then rerun the normal deployment. It bundles the engine Dockerfile/module/config, generates and retains RSA/JWK keys and credentials, starts a private engine PostgreSQL container, builds a source-hashed local engine image and installs/validates Caddy's exact-origin WebSocket route. No host Node or application checkout is needed for preparation.

Installer-managed databases use the optional `chat.database.yaml` companion; all deployment and management operations retain it. It marks the local engine image `pull_policy: never`, so release pulls still fetch the four application images while retaining the prepared engine. Existing operator-provided engine/database settings are preserved. The manual steps below remain applicable to externally managed configurations. The dedicated engine archive volume needs its own backup; application-database backups do not contain it. Production's application PostgreSQL remains external.

## How this fits Mushroom

- StanzaJS 12.22.1 has a pinned pnpm metadata patch in `patches/stanza@12.22.1.patch` so Turbopack resolves its browser platform instead of Node-only DNS code. Both application Dockerfiles copy patches before frozen installation. The SDK is loaded dynamically when a writable consultation is opened. Both frontends use `packages/api-client/src/chat-engine.ts`; existing authentication refresh is reused. No extra UI framework is installed. Browser bundle size and server capacity are not yet measured.
- ejabberd owns authenticated WebSocket streams and private-room delivery. NestJS still enforces current user/session/clinic/appointment/window/block policy and commits each text message before routing.
- Original PostgreSQL conversation/message/moderation/receipt/audit data remains authoritative. Engine SQL MAM is a delivery projection. Use a separate engine database and role on the same PostgreSQL service; Prisma does not own its tables. No historical messages are copied or deleted.
- Existing appointment links and assigned-appointment conversation IDs remain valid. Closed appointments retain authorized REST history; they cannot send or join an active room.
- Current deployment uses **Caddy**, not nginx. Reuse the public API domain with `/xmpp-websocket`; a separate chat DNS record is unnecessary. The XMPP identity domain is independent of the public socket URL.
- Private policy and engine administration use the container network. Caddy blocks the policy prefix publicly and allows browser socket requests only from the exact patient/dashboard origins.
- This release remains **single API instance**. Leases, revocation and invalidation fan-out are in memory. No multi-instance claim is made.
- `/v1/health/chat` checks engine availability separately. Ordinary API readiness remains database-based so a chat outage does not remove appointment/commerce APIs from service. Missing engine configuration/signing key fails startup; a runtime engine outage cannot select a second send path.

## Prepare an isolated environment before deploying this branch

Use synthetic accounts and disposable data. The agent has not executed these steps.

1. Create a dedicated `mushroom_chat` PostgreSQL database/role with no grants on the application database. For remote PostgreSQL, configure SQL TLS/CA in `ejabberd.yml`; the supplied defaults assume a private container network.
2. Generate external RSA keys and a distinct policy secret:

   ```sh
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out /private/secrets/ejabberd-private.pem
   openssl pkey -in /private/secrets/ejabberd-private.pem -pubout -out /private/secrets/ejabberd-public.pem
   node scripts/ejabberd-public-key.mjs /private/secrets/ejabberd-public.pem /private/secrets/ejabberd-public.jwk
   openssl rand -hex 32
   ```

   Set `EJABBERD_PUBLIC_KEY_PATH` to the **public.jwk JSON** file, not the PEM file: ejabberd's `jwt_key` uses `jose_jwk:from_binary`. The API keeps its private PEM unchanged. Local `pnpm dev` derives the public JWK automatically from the existing key.

   Keep all values outside Git/logs. The API runtime must be able to read its private-key mount (production image UID 65532; dev UID comes from deployment configuration). The public JWK must be readable by engine UID 9000. Do not make the private key world-readable.
3. Merge `env.example` into the environment's existing `runtime.env`, outside Git. Set source/key absolute paths, dedicated SQL password and policy secret. Use `EJABBERD_WEBSOCKET_URL=wss://<existing-api-domain>/xmpp-websocket`; `EJABBERD_DOMAIN` identifies XMPP and need not be that API hostname. Assign a distinct loopback proxy port, SQL database/role and keys to every environment. The API's private policy port defaults to 3003.
4. Copy `compose.yaml` to `<environment-root>/chat.compose.yaml`. The dev/release scripts now require this overlay before deploying; the installer operations also include it. It must remain present during all `up`, `pull`, `stop` and rollback operations, including `--remove-orphans`. The overlay uses absolute source/config/key paths so it works beside either VPS Compose profile.
5. For local/source dev, build the supplied engine image using the exact overlay, then start it together with the API. Example from repository root:

   ```sh
   docker compose --env-file /private/chat-check.env -f deploy/vps/compose.dev.yaml -f /private/environment/chat.compose.yaml build ejabberd
   docker compose --env-file /private/chat-check.env -f deploy/vps/compose.dev.yaml -f /private/environment/chat.compose.yaml up -d ejabberd api
   ```

   For staging/production release **build and publish the reviewed engine image first**, then set `EJABBERD_IMAGE` to its registry digest in `runtime.env`. The existing release manifest still promotes the four application images; engine publication/promotion is an explicit prerequisite, not an automatically supplied fifth image. Do not use the local development image tag for a release that calls `compose pull`. Keep its reviewed source/config path available, and retain a compatible engine/config for rollback.
6. Adapt `chat.caddy.example` with the environment's two browser origins and loopback socket port. Install it as `/etc/caddy/mushroom-chat/<environment>/chat.caddy` with normal root-owned readable Caddy configuration permissions. The updated installer generates an optional import inside each API site, so rerunning it preserves this external snippet. On existing installations add `import /etc/caddy/mushroom-chat/<environment>/*.caddy` **inside the API site** before its normal reverse proxy. Validate/reload Caddy using the existing deployment procedure. No nginx installation is needed. Do not expose ports 5280, native XMPP/federation or PostgreSQL publicly.
7. Rebuild the API/client/frontends and compile the engine module as part of owner acceptance before starting this branch. Required API variables are documented in `apps/api/.env.example`; deployments without the engine prerequisites will not start successfully. The 512 MiB/0.5 CPU defaults are configurable budgets, not measured minimums.

The standalone installer packages configuration examples, not the private application checkout or a compiled engine image. It does not create the engine SQL database, keys, runtime settings or public proxy snippet automatically. Its environment audit includes the required companion overlay. Provision those prerequisites before invoking deployment on this branch.

## Frontend behavior and public contracts

- Existing authorized conversation read now returns `transport: "ejabberd"`. `POST /v1/chat/appointments/{appointmentId}/session` returns an in-memory short-lived XMPP password, JID, fixed `sabz` resource, private room, send eligibility and expiry, with `Cache-Control: no-store`. Do not persist or log these credentials.
- Each browser connection gets a separate nonce in its user/session/expiry-bound JID, preventing simultaneous tabs from claiming the same resource. Credentials are renewed before expiry; reconnect gets a fresh session and rechecks persisted authorization. Existing session revocation and the three-connection cap still apply. Expired leases remove their memberships when the engine is reachable; failed cleanup/API crashes can leave expired, policy-denied affiliation rows requiring housekeeping.
- Both frontends load the latest 50 canonical messages, fetch older pages on demand, preserve draft text after send failure, prevent duplicate in-flight sends and reuse the same client UUID when retrying the same payload. Only a canonical engine echo confirms send success. A transport failure does not force a permanently read-only composer.
- `mod_muc_admin` supplies the native room commands. The pinned26.09/v2 configuration uses `allowpm: none`; `get_room_options` returns an options object which the client validates before admitting a room.
- Native events carry canonical message ID, sequence, sender kind, timestamp, visibility and original client ID. Authoritative REST history recovers gaps; live/native archive text is checked against current moderation. Visibility transitions also reconcile after reconnect, foregrounding and every 30 seconds while the page is visible. This process can replay older moderation records; volume/latency needs owner measurement.
- `GET .../messages` accepts `direction=latest|before|after` (default `after`), bounded `limit`, and opaque cursors. Responses remain ascending, with `nextCursor` for that direction and `checkpointCursor` for incremental recovery. Old records are unchanged.
- Clinical reports, moderation, retained history and native receipt policy remain. The old public `POST .../messages` endpoint no longer exists. Both the native SQL archive and retained application ledger are storage responsibilities within one messaging system, not two alternate send implementations.
- OpenAPI source was synchronized for these additive reads/session contracts and removal of the old send operation; TypeScript client source was regenerated. The Nest Swagger exporter was executed with temporary export-only signing material; chat contracts were synchronized from its actual output and client source regenerated. Unrelated pre-existing commerce contract drift was preserved, not bundled into this change.

## Media

This implementation is text-only. Native public upload, rich payloads, direct messaging, room management and federation are disabled. Private medical attachments still need a validated/quarantined appointment-authorized storage endpoint and structured message contract. No photo/upload or call-ready claim is made.

## Owner acceptance before merge into dev

- Review the latest CI results for API/client/frontend compilation, builds and contract checks. Local startup/engine image evidence is recorded above; Caddy/VPS operational acceptance remains separate.
- PostgreSQL chat policy/moderation tests now obtain credentials through the session API and send through the private ejabberd policy endpoint. Test fixtures isolate external room administration, so these checks do not claim live XMPP delivery. Real native-engine scenarios remain a separate acceptance gate.
- Two synthetic accounts: patient and assigned physician, plus clinic admin. Exercise delivery, canonical ordering, same-ID retry after uncertain delivery, typing during an incoming message, switching appointments during a request, older pages, multi-tab use and five-minute renewal.
- Validate revoked/expired session, disabled user, wrong physician/clinic/appointment, STAFF, closed/early appointment and blocks; active delivery, native archive and REST must preserve their access boundaries.
- Restrict/restore old and recent messages while online, reconnecting and paging. Validate private-room isolation and rejection of direct messages, invites, arbitrary IQ/room management, rich payloads and federation.
- Stop engine/API/network separately: no alternate send path, no lost committed record, draft/retry recovery, correct chat health and independent ordinary API readiness. Check actual SQL archive durability and expired membership housekeeping.
- Inspect logs for credential/message leakage; measure real browser bundle, engine memory/CPU, policy latency and throughput before declaring stable.
- Owner frontend/browser acceptance and task review remain required before merging PR28 into `dev`.

References: [ejabberd](https://docs.ejabberd.im/admin/configuration/database/), [26.09 source](https://github.com/processone/ejabberd/tree/26.09), [StanzaJS](https://github.com/legastero/stanza), [Caddy WebSocket proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).
