# Sadad live activation runbook

The deployment switch and the administrator switch are separate by design:

- `SADAD_TRANSPORT_MODE=live` permits this API deployment to contact Sadad. It does not enable a clinic provider.
- Dashboard **Settings → Payment gateways → Sadad → Enable adapter** creates/promotes the active immutable revision to `RIAL/goLiveReady` and enables new attempts.
- Disabling the adapter stops new attempts. Callback and Verify remain available for attempts already sent to Sadad.

## Shared dev values

Use these non-secret values in `/home/deploy/mushroom/dev/runtime.env` after Sadad confirms the merchant redirect profile and the VPS outbound IP is registered:

```dotenv
PUBLIC_API_URL=https://dev-api.drshirmardi.com
SADAD_TRANSPORT_MODE=live
SADAD_REQUEST_TIMEOUT_MS=5000
SADAD_RETURN_URL=https://drshirmardi.com/v1/payments/providers/sadad/callback
SADAD_RESULT_URL=https://dev-app.drshirmardi.com/store/order-success
SADAD_REDIRECT_PROFILE=PHP_VPG_PURCHASE
```

`PHP_VPG_PURCHASE` produces `https://sadad.shaparak.ir/VPG/Purchase?Token=...`. Replace it with `PDF_PURCHASE` or `DOTNET_PURCHASE_INDEX` only when the current merchant acceptance document explicitly names that profile. The API refuses to start in live mode when any required value is empty or malformed, and validates `SADAD_RETURN_URL` as an explicit HTTPS URL ending exactly in `/v1/payments/providers/sadad/callback`, without embedded credentials, query or fragment. Its domain may differ from `PUBLIC_API_URL`; it must be the callback address accepted for the merchant.

Merchant ID, Terminal ID and Terminal Key are entered only in Dashboard. They are encrypted as payment-provider credential revisions and must not be placed in `runtime.env`, Compose, GitHub variables or logs. The AES payment-credential key remains in `secrets/payment_credential_encryption_key`.

After editing `runtime.env`, rerun the normal `Sync shared dev VPS` workflow or run the repository's `deploy/vps/deploy-dev.sh` with the same deployment environment. Confirm API readiness, then open Dashboard and enable Sadad. Select it as the default adapter if checkout should use it.

## Main-domain callback on shared dev

The browser posts to `https://drshirmardi.com/v1/payments/providers/sadad/callback`.
Only the callback and browser-start paths are proxied to the dev API; all other main-domain routes retain their existing handlers.
This reaches the same handler exposed at `https://dev-api.drshirmardi.com/v1/payments/providers/sadad/callback`.

1. On the server serving **drshirmardi.com**, import [sadad-callback.caddy.example](vps/sadad-callback.caddy.example) **inside its existing Caddy site block**. Keep the existing site and its other routes. Confirm `API_PORT` in the selected dev runtime is `127.0.0.1:3303`; otherwise use its actual loopback port. Do not add a duplicate main-domain site or change DNS.
2. Validate the complete Caddy configuration before reloading it: `sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`, then `sudo systemctl reload caddy`.
3. Set the exact `SADAD_RETURN_URL` above in the **dev** runtime. Keep `PUBLIC_API_URL=https://dev-api.drshirmardi.com`. Sync/redeploy dev so both API and dashboard receive the setting. The displayed dashboard address comes from this public runtime setting.
4. Confirm a synthetic POST with form content type reaches the callback handler (an invalid synthetic order must never become a paid order), then perform the authorized acceptance transaction below. Visiting the URL with GET is not a payment acceptance test.

The snippet preserves method, path, query and form body and passes the backend's final 303/Location back to the browser. Do not put a 301/302, www redirect, login gate, CDN challenge or cache in front of this callback path. Verify TLS and reachability on the registered main domain. If the main domain lives on another host, configure its proxy to the HTTPS API origin, including the upstream Host/TLS name, and explicitly configure trusted proxy IPs to preserve rate limiting. Do not trust arbitrary client-supplied forwarded headers.

This repository commit prepares the source and proxy configuration; it does not edit an existing VPS runtime or Caddyfile. Installer reconfiguration preserves an existing explicit main-domain return URL. If switching the callback between dev and production, finish/reconcile outstanding dev attempts first; never route two environments to the same callback simultaneously. Concurrent environments require separate registered callback addresses/paths and an explicit routing contract.

Reference: [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

## Browser departure and interrupted-attempt recovery (PAY02)

Callback routing alone does not change the browser's departure origin. The app now opens a real merchant-origin HTML page before the bank. The token is kept in its URL fragment (never sent to the merchant HTTP server), cleared from history, and encoded into the fixed bank URL. The page is public/rate-limited with nonce CSP, no-store and Referrer-Policy origin; it does not create or confirm a payment.

On an existing VPS using `/etc/caddy/sadad.caddy`, replace the old callback-only handle inside the existing `drshirmardi.com` block with this exact matcher/handle, retaining its TLS and fallback handlers:

```caddy
@sadad_routes path /v1/payments/providers/sadad/callback /v1/payments/providers/sadad/start
handle @sadad_routes {
    reverse_proxy 127.0.0.1:3303 {
        header_down Cache-Control "no-store"
    }
}
```

Validate the full Caddyfile and reload; deploy PAY02 to dev. The existing SADAD_RETURN_URL stays at the main-domain callback. A GET to the main-domain start path must now return HTML200 when live mode is configured; without a token fragment it shows a safe invalid-link message and never contacts Sadad. A404 means the new proxy route or API version is absent. A502 means the API upstream is unavailable. Do not broaden this route to proxy all API paths.

Clicking gateway payment again resumes the same active attempt with its original token, amount and revision, even with a fresh idempotency key. CREATED/VERIFYING/REQUIRES_RECONCILIATION remain pending and never spawn another bank attempt. If an old token is no longer usable, an authorized administrator can find REDIRECT_READY in Dashboard billing reconciliation and run the existing Verify action. A definitive bank failure permits another attempt. Uncertain responses stay in reconciliation; only existing reviewed failure-resolution controls may release them. Never delete records, cancel a potentially paid attempt based only on a browser error, or manually mark a bank payment successful. Existing wallet/cancel guards and late callback verification remain intact.

Local Chromium tests establish the outgoing Referer behavior; live PSP acceptance and exact merchant whitelist matching still require an authorized bank test. Registering the actual frontend domain remains an alternative if Sadad requires it.

## Live callback token casing (PAY03)

The observed VPG browser callback posts lowercase token with OrderId/ResCode. PAY03 accepts token and the compatible Token spelling, rejects conflicting aliases and duplicated parameters, and ignores unrelated bank/card/CSRF fields. Bank success in the form is only a hint: matching server Verify is still required for a wallet credit or paid order. No Caddy, environment or database migration change is required for this correction; deploy the API commit normally.

For a transaction whose callback was rejected before this fix, do not create/pay a new attempt. Use the existing administrator billing reconciliation Verify action on the original attempt. The bank result determines whether it settles or remains unresolved; do not force success, delete the record or release an uncertain attempt just because money was debited. For fresh acceptance, confirm callback303 to the app result page and exactly one persisted credit/payment on replay. Share only field names/status/requestId when troubleshooting; never tokens, cookies, CSRF values or card hashes.

## Acceptance transaction

1. Create a low-value real order using an authorized test customer.
2. Confirm the create-attempt response opens the configured merchant origin at `/v1/payments/providers/sadad/start#...`. Its HTML document then navigates only to the configured allowlisted Sadad purchase endpoint. In browser Network with Preserve log enabled, confirm the Purchase request has `Referer: https://drshirmardi.com/`. Do not copy tokens/cookies into logs or messages.
3. Complete or cancel the payment at Sadad.
4. Confirm the browser POST reaches the exact Return URL with form fields `OrderId`, `Token` and `ResCode`.
5. Confirm the API performs server-to-server Verify before creating a Payment, marking the order `PAID`, consuming inventory reservations or issuing a receipt.
6. Confirm a repeated callback is idempotent and does not create a second Payment.
7. Confirm a timeout remains `REQUIRES_RECONCILIATION`; retry uses the same attempt, token and provider order ID.
8. Disable Sadad in Dashboard and confirm new attempts return `SADAD_GO_LIVE_BLOCKED` while an already-created callback can still finish Verify.

Do not treat the redirect or callback alone as proof of payment. The authoritative success condition is a matching successful Verify response persisted as a single Payment.
