# Ahavah review remediation — 9 September 2026

This delivery addresses the concrete defects in the 8 September implementation review across ahavah-web, ahavah-api, and ahavah-admin. It does not claim completion of every product proposal in the original system review.

## Delivered

| Finding | Change |
| --- | --- |
| F01 account isolation | Owner-scoped IndexedDB keys and queries; purge legacy ownerless content; retain at most 50 successful and 50 unresolved messages per thread. Clear account caches and disconnect chat on identity changes, sign-out, and terminal authentication failures. Invalidate stale responses, callbacks, and cross-tab sessions. Reload pages restored from browser history cache. |
| F02 privacy saving | Read map visibility from the server; serialize changes; display only confirmed values; retain the previous setting and display an error on failure. Initial load failures offer retry. |
| F03 transactions | Dispose failed synchronous/asynchronous connections on transaction entry, commit, or rollback failure; release locks and propagate failure. |
| F04 likes and matches | Normal and super likes lock both member rows in sorted order under READ COMMITTED before quota checks and reciprocal-match creation. Only the transaction inserting a match triggers its normal-like match notification. |
| F05 migrations | Apply files with ON_ERROR_STOP and an advisory lock; checkpoint filename/SHA-256; reject drift; build before migrations and replace containers only after successful migration. |
| F06 admin sessions | Both sign-out controls clear credentials immediately, cancel and clear query data, and attempt revocation with the captured bearer token. Reject stale responses and handle identity changes in other tabs. |
| F07 telemetry | Remove the member application's global Meta script and image beacon. Registration CAPI is disabled unless AHAVAH_REGISTRATION_TELEMETRY=enabled; its event ID no longer embeds a raw member UUID. |
| F08 local development | Default development API rewrites point to localhost in both frontends. AHAVAH_API_ORIGIN can explicitly override this. |
| F09 routing | Discover sends visitors without a session to sign-in before applying onboarding checks. |
| F10 entitlement expiry | Shared access helpers and profile output enforce subscription_expires_at at read time; NULL expiry preserves non-expiring grants. |
| N1/N3 guidance | Completion cards no longer infer server invisibility from client completeness. City guidance reflects the map's actual city requirement and privacy dependencies. |
| N2 recovery | Link to the specific failed message, retain it outside the successful-history window, offer permitted resending and explicit discard, prevent simultaneous duplicate retries, and remove the original after confirmed delivery. Uncertain delivery warns the member to check the conversation first. |

## Verification

- Backend: 235 tests passed, including independent-connection simultaneous likes/quota checks, read-time expiry, and transaction cleanup.
- Frontend: full Vitest suite, type checking, scoped application/test lint, and production build. Added account isolation, bounded retention, legacy cache removal, privacy rejection/confirmation, real hook message recovery, and session response-race checks.
- Admin: production build, type checking, and two Node regression tests for local sign-out and stale responses. Its pre-existing component edits were present during build but are excluded from this delivery.
- Browser: installed Chrome against the local production build at 390px and 1440px, with synthetic API responses. Confirmed failed privacy save, successful save, persisted server value after reload, and no advertising requests or page errors. Screenshots were inspected. This is not a real-account production test.
- Migration integration: cloned the local test stack's database into ahavah_review_migrations_20260909. Verified all existing migrations, repeat-run skipping, changed-checksum rejection, immediate SQL-error stop without marking failed/later files, and resumption after correction. No production database was used for these checks.

## Operational behavior and remaining work

The first tracked migration run executes the existing idempotent migration set before recording it. Applied files must subsequently remain immutable; use a new migration to make a correction. A failure after a legacy file commits but before its ledger entry requires that file to be replayable. The runner deliberately does not manufacture an unverified historical baseline.

Legacy local chat content is discarded because it has no trustworthy owner. Recent server history can hydrate again; old local-only failed messages cannot safely be attributed. The unresolved-message cap also means this is a bounded recovery cache, not permanent message backup.

Browser bearer-token storage and the broader cookie/WebSocket-ticket/CSP redesign remain separate architectural work. General profile/onboarding draft saves still use their existing optimistic model; only the privacy settings flow in F02 has been converted to confirmed-state saving. Dependency locking/reproducibility beyond the API rewrite is not addressed. Full payment commit-replay and mixed normal/super-like external notification delivery were not proven by this test pass.

Telemetry is suspended by default. Any future public-page tracking or registration-CAPI activation needs an explicit consent/data-minimization policy; enabling the registration flag retains the existing hashed-contact/IP/user-agent payload behavior.

Pushing these branches may start the repositories' configured deployment integrations. A green local test/build is not evidence of live deployment or real-account acceptance; record remote deployment results separately.
