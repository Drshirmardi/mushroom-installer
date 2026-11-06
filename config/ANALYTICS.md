# Analytics — browser and confirmed conversions

AN01 and AN02 deliver the browser foundation, attribution and request-level UX events.
AN03 adds confirmed server conversions/Outbox; AN04 still owns end-to-end acceptance.
This is not yet the completed analytics feature. Do not enable production
analytics until those gates and the collection/consent policy are accepted.

## Runtime configuration

`PUBLIC_GA4_MEASUREMENT_ID=G-...` and `PUBLIC_ANALYTICS_CONSENT=granted`
populate the existing `/runtime-config.js` in Docker and VPS dev startup. Both
default to disabled/denied. No API secret belongs in this public configuration.
Runtime changes take effect on reload/restart without rebuilding the frontend.
`PUBLIC_ANALYTICS_DEBUG=true` explicitly marks events for DebugView through that same
runtime and loader; it defaults to false, and false/absent omits the debug parameter.
The server uses the same flag only at transport time without changing stored events.
This technical gate does not obtain a visitor's consent; the consent integration
must supply the applicable policy before enabling collection.

The visitor preference `localStorage['mushroom.analytics']='denied'` is checked
at route/action dispatch. This slice does not add a consent banner or claim
immediate revocation of data already collected by Google's script.

In the GA4 web stream disable Enhanced Measurement automatic history/pageview
tracking (and other automatically collected interactions pending policy review).
Our route observer owns page views; query-only login changes do not count as pages.

## Event boundary

`createAnalytics(adapter)` owns enabled state, logical paths, consecutive route
deduplication and allowed metadata. `page` and `track` are vendor-independent.
Only explicitly annotated CTA elements are observed; no DOM-text autocapture,
form fields, raw query strings, account IDs or medical results are collected.
Browser events make no application API request and perform no database write.
Google's loader is asynchronous, once per document, and its pre-load buffer is
bounded to 100 commands if the script is blocked. This does not guarantee delivery.
Before initializing a destination, the same loader sets global `send_page_view=false`
and safe title/location/referrer defaults. Actual Google testing found that the per-config
flag alone allowed an extra automatic raw-URL view; the global defaults removed it.

AN02 preserves only validated source/medium/campaign slugs, first explicit landing
CTA and HTTPS referrer origin; raw referrer paths/queries remain excluded. Context
is tab-scoped sessionStorage with a 30-minute handoff validity window. Only the
configured app origin receives ma_* handoff parameters; they are removed upon app
initialization while product query/hash parameters stay unchanged. GA4 client/session
IDs come from the documented gtag get callback and are accepted only for the same
measurement ID. Missing/blocked SDK callbacks never delay navigation; in that case
native GA4 cross-domain identity is not guaranteed, although CTA/campaign context
still survives. Same-origin cookie-domain and cross-domain property settings require
operator validation with real GA4 in AN04. No purchase is
emitted from the success page. Nothing is installed on the staff dashboard.

## Verification

`node --experimental-strip-types --test config/analytics.test.mjs`

`node --test config/analytics-browser.test.mjs` (installed Playwright Chromium)

The browser test uses the actual compiled SDK in Chromium and intercepts remote
resources. It verifies dispatch/metadata and lack of API calls, not ingestion
into a real GA4 property or the complete authenticated app journey.

Sources:
- https://developers.google.com/analytics/devguides/collection/ga4/views
- https://developers.google.com/tag-platform/gtagjs/reference

## AN02 behavior semantics

- page_view: logical route including home/checkout/schedule; query-only changes do not count.
- cta_impression: explicit CTA at least 50% intersecting the viewport, once per observer/route.
- cta_click / navigation_click: explicit action or internal known-origin link, never DOM labels.
- feature_engagement: visible intervals >=1s, flushed on visibility loss, pagehide and route cleanup.
- action_attempt / action_result: registered existing request, duration, HTTP status and accepted,
  rejected, unavailable or network_error outcome. Accepted HTTP is NOT purchase/assessment
  completion. Retries are separate attempts. No response or request body is read for observation.
- cart_quantity_set: after a successful cart mutation, opaque public SKU and absolute quantity;
  zero represents removal. It is not an invented add-to-cart quantity delta.
- Public catalogue/product reads expose registered operation and opaque catalogue product ID;
  no product titles, searches, prices, diagnoses or private patient resource identifiers.

The existing app session-aware transport, auth headers, marriage context, response object and
body ownership are preserved. No additional application API call, database write, server
collector or new infrastructure is introduced. GA4 reports need custom definitions for custom
metadata. This slice does not add generic DOM/form capture, session replay, monetary conversions,
or server-side purchase events. New app endpoint categories require explicit registry entries.

Tests: analytics-context.test.mjs (pure sanitization/outcomes), analytics-journey.test.mjs
(actual Chromium cross-origin handoff and unchanged request transport), existing API/auth
regressions. Remote GA4 script/API are intercepted; live ingestion is not asserted.

## AN03 confirmed conversions

The browser forwards an optional `X-Analytics-Context` on existing API POST requests,
only with collection enabled and fresh, actual GA client/session IDs. The server accepts
at most 1024 characters, validates the same property and a 30-minute handoff window,
and projects only the allowed metadata. This header never authorizes a business action;
existing authentication and ownership checks remain authoritative. It is redacted from logs.
Blocked/missing SDK, stale context or denied consent means the conversion is skipped.
The current browser snapshot is acquired at initialization; long-lived pages may need a
reload to obtain fresh attribution. No identity or event timestamp is fabricated.

- `purchase`: committed commerce payment, actual order transaction ID, up to 100 opaque
  SKU/quantity entries and the full settled amount. Longer item lists are capped.
- `refund`: completed wallet refund or manually evidenced gateway refund, using the
  original transaction and original buyer context. A refund after context expiry stays
  exclusively in the financial system, as explicitly approved by the owner.
- `service_paid`: confirmed non-commerce paid intent; no service name or clinical detail.
  Wallet top-ups and compensated late payments are excluded from sales.
- `booking_confirmed`: confirmed appointment, including confirmation after payment.
- `assessment_completed`: COMPLETE submission only; no answers or results.
- `sign_up`: actual account creation timestamp; existing-account login is not signup.

Commerce/refund amounts use custom `amount_rial`, with exact `amount_rial_text` fallback
when outside JavaScript's safe integer range. Configure a custom metric for numeric rial
amounts; standard GA4 currency/revenue metrics are not populated with a fabricated currency.
Custom non-commerce conversion events currently count outcomes without monetary amounts.

Financial delivery records are created inside the existing settlement/refund transaction.
Signup, assessment completion and immediate appointment context capture run best-effort
after successful handlers; a crash in that gap can lose those events. No external HTTP
request occurs inside a financial transaction. A database enqueue failure rolls back that
transaction, preserving the financial/outbox consistency boundary; Google downtime does not.

## Short-lived delivery and retention

The existing PostgreSQL Outbox holds minimal context and pending conversions. There is
no new table, service or activity archive. The existing operational consumer excludes
analytics; the in-process analytics consumer claims at most five rows every ten seconds,
with atomic row locking, a 60-second lease, a three-second HTTP timeout and at most 32
attempts. Retry starts at one minute and is capped at one hour and the event deadline.

The deadline is the earliest of 24 hours after session start, context capture or the true
event time. This conservatively respects GA4's session attribution window; it does not
guarantee attribution to a session that has already ended. Expired events are never sent.
Payloads are cleared on submission/permanent failure; only a deduplication tombstone
remains until expiry. Minimal original context remains eligible for refunds until expiry,
then bounded cleanup removes it. Cleanup is eventual, in batches (including while disabled,
at most once a minute), so an inactive API or backlog can delay physical deletion.
Financial history is never deleted by analytics cleanup.

Local idempotency prevents normal duplicate enqueue. Delivery is at least once: an ambiguous
timeout or crash after Google's acceptance can still repeat an event. Do not assume exactly-once
refund/custom-event processing. HTTP 2xx means submission, not proof of GA4 ingestion.
Logs contain classification/attempt counts, never payloads, browser IDs or secret-bearing URLs.
Consent is a captured snapshot; changing a local preference does not revoke already queued
server context. The visitor consent/revocation policy must be resolved in AN04 before activation.

## Server opt-in (not enabled by this implementation)

The API additionally requires `ANALYTICS_SERVER_ENABLED=true`,
`PUBLIC_ANALYTICS_CONSENT=granted`, the same `PUBLIC_GA4_MEASUREMENT_ID` as the browser,
and private `GA4_API_SECRET`. Enabled startup rejects missing property/secret.
Keep the API secret in a private file, never frontend runtime config or source control.
Docker API entrypoint supports `GA4_API_SECRET_FILE`.

Production uses the explicit `deploy/vps/compose.analytics.yaml` overlay alongside
`compose.yaml`, with `GA4_API_SECRET_PATH` pointing to that private file. The normal
compose file leaves server analytics disabled. The overlay adds no service and mounts
the secret only into the API. In VPS dev, the optional-secret directory can supply
`ga4_api_secret`; the dev API launcher reads it only when server analytics is enabled.
No real credentials/property activation or deployment has been performed.

AN04 must validate real GA4 receipt, domain/session continuity, custom definitions,
collection/consent policy and the complete authenticated product journey before production use.
Follow [the AN04 acceptance checklist](ANALYTICS-ACCEPTANCE.md) for the concrete operator
steps, local test coverage and outstanding live evidence. Local software checks do not
enable collection or certify the target property.

Server protocol sources:
- https://developers.google.com/analytics/devguides/collection/protocol/ga4/reference
- https://developers.google.com/analytics/devguides/collection/protocol/ga4/sending-events
- https://developers.google.com/analytics/devguides/collection/protocol/ga4/use-cases
