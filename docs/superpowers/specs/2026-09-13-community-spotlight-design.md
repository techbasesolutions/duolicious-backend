# Ahavah Community Spotlight: design

Date: 2026-09-13. Status: approved direction, two review passes folded in. Owner decisions (2026-09-13): Ahavah's own stack, repurposing admin.ahavah.app; opt-in delivered as an email to every member and grown into a full feature (new members, member of the week, member highlights); the announcement email also carries new members; social posts alongside; dormancy at 30 days; first names allowed.

## 1. Purpose

Turn member growth into visible community life on three channels: the Ahavah Facebook Page and Instagram, the members' inbox, and admin.ahavah.app. Outcomes:

1. New members are welcomed publicly and inside the community.
2. Members who consent are featured (member of the week, highlights) and share their own card, which is the main reach channel.
3. Members who have gone quiet are re-invited with the specific reason to return: new profiles that match what they said they were looking for.
4. Every send and post, and its clicks and resulting sign-ups, is visible in one Growth tab.

Success within 30 days of launch: at least half of active members opted in, one spotlight post per week published with no step beyond approval, all three emails sending from the admin app with no shell access, clicks and sign-ups attributed per post and per email.

## 2. Constraints that shape the design

- Meta removed the Groups API on 22 April 2024. Automation can publish to the Page (`1100237303180442`) and Instagram business account (Graph id `17841447302854202`) only. Group posting stays a manual share of the Page post; the Growth tab gives a "copy caption and open post" action for it.
- Members grant Ahavah no licence to republish their photos today, and the privacy page says nothing about members appearing publicly. Both the terms and the privacy page get a Spotlight section before launch. A member's photo and name appear on social only after that member opts in and approves the specific card. Inside members-only email, first names of new members are already sent by the digest, so names are allowed there.
- The proven engine is the President dashboard's publishing queue (`barbados-president/server/publishing.js`, migration `0013_publishing_queue.sql`, per-minute cron, `tests/dashboard.test.mjs`). It ports in spirit: server-side scheduling, idempotent request keys, atomic claim, lost confirmations parked for review and never auto-retried, tokens only in Bearer headers.
- Ahavah's data lives in Postgres on the droplet behind `ahavah-api`; the admin app is Next.js on Vercel and talks only to the API. Vercel cannot run Chrome; the API container has no browser. Card rendering uses `next/og` (satori) in the admin app with embedded fonts.
- Email links are followed by mail scanners before the member opens the mail. No link may change state on GET. Every action link lands on a page with a button that submits a POST. The existing unsubscribe link was audited in Phase A: it stamped on GET (fixed to POST with a form) and its emailed address `ahavah.app/u/<token>` returned 404 because the web app had no route (fixed with a forwarder to the API).
- The upstream dormancy cron deactivates members idle 30 to 50 days by `last_online_time`, which background activity refreshes. Real dormancy is measured by actions instead (last like, pass or message).
- Copy rules: no em dashes, sentence case, canonical email shell with Ultra title images. Emails and captions are English only in this version; localisation is a later decision.

## 3. Feature shape

Member-facing name: **Spotlight**. Admin-facing name: **Growth** tab.

### 3.1 Consent, approval and preferences

Amended 2026-09-14 (Wave 1): consent is now bound to a specific content revision rather than to a request row, and confirm and card links carry single-use, revocable tokens.

- New columns on `person`: `spotlight_opt_in boolean not null default false`, `spotlight_opt_in_at timestamptz`, `spotlight_last_featured_at timestamptz`, `reinvite_sent_at timestamptz`, `spotlight_consent_epoch int not null default 0`.
- Opt in or out through a switch in `/settings/privacy` ("Feature me in Spotlight"), through the announcement email (signed link to a confirmation page with a POST button, 30-day expiry, idempotent), or through the admin drawer (audited). Opt-out cancels queued rows for that member in the same transaction, enqueues a cleanup job for the rendered cards, and calls the one withdrawal operation described in 3.6, which increments `spotlight_consent_epoch` and burns every unused token nonce for that member. Removal of a post already published is not instant: it retries against an operational deadline (5) that stays inside the public copy's own promise (`ahavah-web/src/lib/legal-spotlight-copy.ts`).
- Consent is per content revision, not per request. Table `spotlight_revision` holds an immutable snapshot of a card's content (caption, photo, participants, channels); table `spotlight_revision_consent` records one approval per revision per person. Any material edit, a new caption, a new photo, a changed participant set, creates a new revision with no consent carried over, so the member approves what will actually post. A request with a row in `scheduled` or `processing` is immutable: no new revision can be created for it until it clears that state. A request in a terminal state (`published`, `cancelled`) is also immutable; a caption cannot be rewritten after the fact because the posted text is the record.
- Per-card approval: before any card with a member's photo is scheduled, the member receives "your Spotlight card is ready" with a preview of the rendered revision and two POST actions, approve or skip, and a photo picker among their own photos. Approval is of the rendered revision, which means rendering happens before the member ever sees the card, not after approval as originally specified. Nothing with a photo publishes without that approval. Approval expires after 7 days and the row is cancelled. Member approval is disabled (`spotlight_setting.approvals_enabled = false`) until the designed renderer exists; while disabled, candidates are still created and queued but no invite asks a member to act, and approval attempts answer 409 `approvals_disabled`.
- Confirm links and card links each carry a single-use nonce, stored in `spotlight_token_nonce` and bound to the person's current `spotlight_consent_epoch`. The nonce is consumed only once the decision it represents has taken effect. A withdrawal bumps the epoch and burns every nonce issued before it; a token used after that point, including a link the member still has from before they withdrew, answers 410 `stale` rather than re-enabling consent.
- Opt-in text states exactly what is shared: first name, age, country, one photo they choose, on the Ahavah Page and Instagram and in the members' weekly email, and that they approve each card's specific revision.

### 3.2 Eligibility

A member can be featured only if all hold: opted in, card approved, photo-verified (Bronze or higher), 18 or older (verified at onboarding, checked again here), no open report against them, no moderation action, no pending deletion, not featured in the last 30 days, and the chosen photo still exists at claim time.

### 3.3 Spotlight kinds

Amended 2026-09-14 (Wave 1): the roundup collage is count-only until a per-member roundup approval flow exists.

Amended 2026-09-15 (Wave 3): the card's "one line of caption" is not a separate field entered at queue time. It is derived by the renderer from the social caption already stored on the row: the first sentence, with any `https://` link removed, clamped to at most 120 characters and to two rendered lines. There is no queue-time length rejection of the caption; overflow is handled by the clamp alone.

| Kind | Audience | Source | Photo and name | Cadence |
| --- | --- | --- | --- | --- |
| New member welcome | Page, Instagram, weekly email | Member finished onboarding, opted in, approved | Yes | Created when the member approves, batched daily |
| New members roundup | Page, Instagram, weekly email | Members who joined in the last 7 days | Count-only by country unless `spotlight_setting.roundup_tiles_enabled = 'true'`. Welcome approval of a member's own solo card does not carry over to a roundup tile: a tiled roundup records its own participant set on its revision and cannot publish until every pictured participant has a consent row for that revision. Email: first names for all | Weekly, Monday |
| Member of the week | Page, Instagram, weekly email | Admin confirms a pick; default suggestion is the eligible member least recently featured, alternating gender week to week | Yes | Weekly |
| Member highlight | Page, Instagram | Admin composes for an eligible member | Yes | Ad hoc |

Card content: photo, first name, age, country, one line of caption. No bio, intent, assembly, or location finer than country. No per-member roundup approval flow exists yet, so `roundup_tiles_enabled` stays `false` until it is built (see 7).

### 3.4 Growth loop and measurement

- When a member's card is published, they receive "your Spotlight is live" with the post link and a share button. Member sharing to their own feeds is the primary reach channel; the Page is secondary.
- Every social caption and every email CTA carries a short campaign link `/s/<key>` that counts clicks and stamps a `spotlight_ref` on any sign-up that follows within 7 days, reusing the referral click pattern. The Growth tab shows clicks and sign-ups per post and per email.
- Posting times default to the audiences the ad data showed (West Africa, the Caribbean, Latin America): 12:00 and 18:00 UTC, adjustable per row.

### 3.5 Emails (canonical shell)

Amended 2026-09-14 (Wave 1): E5 is now tied to the feature occurrence rather than to a single channel row, and never reaches a withdrawn member.

Amended 2026-09-15 (Wave 2): every campaign and transactional email now enqueues into a durable `email_outbox` row inside the same transaction as whatever decided to send it. The unique key (campaign, campaign_id, person_id) is the idempotency point, not the SMTP call: a retried request adds nothing. A cron drains due rows one at a time, bounded at 50 per tick, reserving each with `FOR UPDATE SKIP LOCKED` so two drains never double send, then talks to SMTP outside any transaction. Reserving one row per iteration rather than the whole batch up front means a drain that dies part way through strands at most the single message actually in flight; everything else is still `queued` for the next drain. States are `queued`, `reserved`, `accepted`, `acceptance_unknown`, `failed`, `skipped`. Suppression, scope-unsubscribe and the frequency cap are re-checked at send time, not only at enqueue, and a refusal there is `skipped`, never `failed`. An SMTP error requeues on backoff (1, 10, 60 minutes) for up to three attempts, then lands in `failed`. A reservation that never resolves, because the process that made it died between the SMTP call and recording its outcome, becomes `acceptance_unknown` rather than being retried (which could double mail a member) or silently dropped (which would lose the message); that state is surfaced on the admin status endpoint for a human, not swept up automatically. E4 and E5 are enqueued inside the same transaction as the event that triggers them, the candidate's creation for E4, the publish receipt for E5, instead of being sent from a background thread, so they survive an api restart between the trigger and the send. This closes F07 (dedupe was not durable) and F08 (E4 ran fire-and-forget on a daemon thread).

| Email | Who | When | Body |
| --- | --- | --- | --- |
| E1 Spotlight announcement | Every activated member not on the suppression list and not unsubscribed from the notifications scope, once | Launch, sent from the Growth tab | What Spotlight is, what is shared, opt-in button to the confirmation page, link to the settings switch, a line that nothing changes for those who do not opt in |
| E2 Weekly community email | Every activated, unsuppressed member not opted out of the community category | Weekly, Monday, admin-triggered with preview; cron after two clean weeks | Member of the week, new members this week by first name and country, community size, one CTA into Discover. Replaces the digest module |
| E3 Re-invite | Activated members not unsubscribed from the notifications scope, with no like, pass or message in 30 days, not sent this email in 30 days, with at least one new member since their last action who matches the member's own filters (age, country, intent), falling back to gender only when the filtered set is empty | Admin-triggered with preview; optional weekly cron | "N new members joined since you were here", up to five first names with countries, one CTA into Discover |
| E4 Card ready | Opted-in member with a candidate card | On candidate creation | Preview, photo picker, approve or skip (POST) |
| E5 Card live | Featured member | On publish | Post link, share button |

Rules across all campaign emails: a central `email_send_log` (person, campaign, sent_at, message id) enforces at most one campaign email per member per 7 days (the weekly email uses a 6-day window so a weekly cadence never skips itself), exempting E4 and E5 which the member triggered; the runner also skips any member unsubscribed from the campaign's scope, and gives the System tab the send visibility it has lacked. Each send carries a campaign id checked server-side so a double click cannot send twice. The weekly email is its own unsubscribe category so leaving it does not silence match notifications. Titles get new Ultra image pairs through the design brief. E3 runs inside the 30-day window before the upstream deactivation cron could act. E5 is sent once per feature occurrence (see 3.6), on the first channel's confirmed publish, carrying that receipt's `post_url`; it is never sent to a member who has withdrawn in the meantime. Campaign mail (E1 to E3) stays at-least-once with visible uncertainty rather than exactly-once, by owner decision: the Wave 2 outbox (above) makes a crash between SMTP acceptance and recording it visible as `acceptance_unknown` instead of silently duplicating or dropping the message, but it does not promise exactly-once delivery.

### 3.6 Publishing engine (port of the President worker)

Amended 2026-09-14 (Wave 1): the single `scheduler_enabled` switch and the two auto flags are gone; delivery state now survives withdrawal; a lease binds a claim to the row that made it; one feature occurrence spans both channels; the final check before any Graph call fails closed.

- Table `publishing_queue` in `duo_api` (migration 0039, extended by migration 0044): id, request_key, kind, subject_person_id (nullable), platform (`facebook` or `instagram`), caption, image_url, image_key, scheduled_for, status (`scheduled`, `processing`, `published`, `failed`, `review`, `awaiting_member`, `cancelled`), lease_until, attempts, external_post_id, error, created_by, created_at, updated_at, plus `current_revision_id` (references `spotlight_revision`), `lease_token`, `cancellation_requested_at`, `delivery_state` (`none`, `attempting`, `published`, `delivery_unknown`, `failed`), `post_url`. Unique on (request_key, platform).
- Claim: SQL function `claim_spotlight_posts(limit)` using `for update skip locked`; a claim also mints a random `lease_token`, sets `delivery_state = 'attempting'`, and never selects a row with `cancellation_requested_at` already set.
- One withdrawal operation, called from every lifecycle exit point (the settings switch, account deletion, admin delete or ban, the pending-deletion cron, moderation actions): it sets `cancellation_requested_at` on every open row for the member and cancels every row that is not currently `attempting`; a row already `attempting` is left alone because an external call may already be in flight.
- The worker sends the lease token with every completion call. `record_receipt` accepts a `published` outcome even after `cancellation_requested_at` was set, because the external post already exists: the row becomes `published`, the post is recorded, and a removal task is filed immediately so the published card is taken down. A lease token that no longer matches the row (a second worker, an expired lease) is rejected and counted separately, never silently ignored. A timeout or an unclear response after a publish call was actually sent becomes `delivery_state = 'delivery_unknown'` and the row parks in `review`: it is never auto-retried, only resolved by an operator or a later, definite receipt.
- Feature occurrence: table `spotlight_occurrence` is the unit the 30-day cooldown reads, one row per `(request_key, person)`. The two channel rows of one request share an occurrence, so completing on Facebook no longer blocks the still-pending Instagram row for the same card. Roundup participants each get their own occurrence.
- The final dispatch check before any external call fails closed on: the lease held and current; the row's status still `processing`; `cancellation_requested_at` unset; `current_revision_id` present and rendered; consent complete for every pictured person on that revision; the exact approved photo still owned, approved and present (for a roundup, every tile's own photo); and both `publication_enabled` and `external_access_enabled` set to `true`. A subject-bearing row with no subject is not ok; anything the check cannot positively confirm is treated as not ok.
- Admin-gated API endpoints: list queue, create, claim, complete, cancel, purge, plus a health endpoint proxying `debug_token` so the Growth tab warns 14 days before the token expires.
- Review mode is the default. `auto_welcome` and `auto_roundup` are removed until an auto mode that still requires revision-bound consent exists; until then every card is reviewed by an admin and, unless approvals are disabled, approved by the member. Member approval is never skipped.
- Three named controls replace the one scheduler switch: `invites_enabled` (gates welcome and roundup candidate creation and the E4 invite; a member candidate can still be created while approvals are disabled, but no invite is sent until approvals reopen), `publication_enabled` (gates the per-minute claim), `external_access_enabled` (an emergency stop: halts every outbound call to Meta, both publishing and removals, and is the only switch removals answer to). Publication pausing does not pause removals or the daily tick's rendering steps; only the emergency stop does. The purge endpoint still cancels every non-published row on demand.
- The welcome cohort is defined by sign-up time (`sign_up_time > now() - 14 days`), not by opt-in time. The weekly roundup uses the business key `roundup:<iso-year>-W<week>` as its `request_key`, so a duplicate tick in the same week converges on the existing row instead of creating a second one.
- Removal: on withdrawal, Page posts are deleted through the API and the stored card is removed. Instagram media cannot be deleted through the API; the Growth tab lists it as a manual task until an admin marks it done. Rendered cards are deleted from storage 90 days after publish. Removals run whenever `external_access_enabled` is true, regardless of `publication_enabled`. A removal task carries an operational deadline, `deadline_at = created_at + 72 hours` (`REMOVAL_DEADLINE_HOURS` in `service/spotlight/withdrawal.py`, Wave 2); a task past its deadline and still open is counted as overdue and surfaced to the operator (5) rather than retried silently forever. Seventy-two hours is comfortably inside the public copy's own promise to remove controllable posts within seven days (3.1); the spec states nothing shorter, so no change to the member-facing promise was needed (Wave 2 Task 8 ruling).

### 3.7 Card rendering

Amended 2026-09-14 (Wave 1): rendering is now one render per revision, and a rejected re-upload can no longer overwrite an already-approved image.

Amended 2026-09-15 (Wave 2): object keys are now content-hashed and immutable, `spotlight/<request_key>/<revision_id>-<sha256 prefix>-<platform>.png`, so identical bytes always resolve to the identical key and different bytes for the same revision and platform can never collide. Attaching an upload to its row is compare-and-set: it succeeds only when the row is still pinned to the exact revision the upload was rendered against, still in an uploadable status, has no delivery unresolved, and the revision has no render already attached; anything else answers `superseded`, and the now-orphaned object is queued for cleanup (5) rather than left in the bucket or left to silently overwrite bytes a member has already approved. Uploaded objects are private by default; a member's preview reads a short-lived presigned URL, never the object's public address, and an object is made public only after an admin approves the card and the commit that moved it to `scheduled` has landed. This closes the remainder of F06.

Amended 2026-09-15 (Wave 3): the renderer exists. Three modules in `ahavah-admin` implement it: `src/lib/spotlight-card-text.ts` (pure caption and name-display logic, no `next/*` import), `src/lib/spotlight-card-layout.tsx` (pure element tree, no `next/*` import) and `src/lib/spotlight-card.tsx` (`renderCard`, which loads fonts and photos once per process and calls `next/og`'s `ImageResponse`). Fonts embedded: Ultra at weight 400, Plus Jakarta Sans at weights 500, 600 and 700, and Noto Serif Hebrew at weight 900 as the Hebrew substitute (Ultra carries Latin and Latin Extended only and has no Hebrew block; Noto Serif Hebrew Black is the closest slab-weight serif with one). Photos are fetched only from the hosts in `AHAVAH_PHOTO_HOSTS` (default `user-images.ahavah.app`). A missing photo, a photo host outside the allowlist, or a name with no glyph in any loaded font is a render failure rather than a broken card, thrown as one of three named errors, `photo_missing`, `photo_host_refused`, `name_glyphs_unsupported`, and counted by the tick as `render_failed`. Satori runs no bidirectional text algorithm, so a Hebrew name is not laid out by a bidi pass: `spotlight-card-text.ts`'s `visualOrder` explicitly reverses the order of right-to-left runs, and the characters inside them, before the layout draws the name right-aligned against the card's margin, which is what a right-to-left paragraph resolves to. Digit and Latin runs inside an otherwise Hebrew name, an age suffix, a precomposed accent, stay in reading order and to the digits' left.

- One Claude Design template, square 1080x1080 only, with three variants: photo card, roundup collage, member of the week. Brand tokens, Ultra display, Plus Jakarta Sans. Fonts embedded with coverage for Hebrew and Latin with diacritics; a render test uses real member names.
- Rendered in `ahavah-admin` with `next/og` `ImageResponse` from a JSX transcription of the template. Route `/api/growth/render` (admin session or cron bearer) returns PNG.
- PNGs go to the existing DigitalOcean Spaces bucket under `spotlight/<request_key>/<revision_id>-<sha256 prefix>-<platform>.png` (Wave 2), private until the card is approved; the row stores the private key, a presigned preview URL is generated on read, and the object is made public at approval time. Keys are unique per render so CDN caching cannot serve a stale card.
- A revision is rendered at most once: `attach_render` refuses a second attempt on a revision that already has an asset (409 `already_rendered`). A rejected or mistaken upload therefore cannot silently replace bytes a member has already consented to; a new attempt requires a new revision, which clears consent. Content-hashed, immutable object keys and a compare-and-set attach that survives a race are done (Wave 2, above).
- A card is rendered only for an eligible member, enforced in the API candidate query and again in the render route. Rendering happens before member approval, not at it, so the member approves the exact image that will post; the worker re-checks the photo at claim and cancels if it is gone.

### 3.8 Growth tab (admin.ahavah.app)

Amended 2026-09-15 (Wave 3): the tab is built. Items 2, 3 and 5 differ from the original text below, as built:

- Item 2, the Spotlight queue, groups the API's per-platform rows into one card per `request_key`, with one badge per platform when the two disagree on status (for example "Facebook published", "Instagram failed"), and its row menu offers only the moves the API's own state machine accepts: Approve on a row in `review` (gated on consent and a render, disabled with "Waiting for the member" on `awaiting_member`), Post now and Reschedule on a row in `scheduled`, Cancel on anything not `published`, `cancelled` or `processing`, Retry on `failed` under the three-attempt cap, and Copy caption and open post whenever a `post_url` exists. A `delivery_unknown` card narrows to Cancel and Copy caption and open post only; the reconcile route stays a curl-level operation this wave. Retry was added beyond the five actions named below because without it a failed card had no action the API would accept.
- Item 3, Member of the week, offers two alternatives from the eligible pool alongside the suggested pick, not the full pool, with "Pick someone else" cycling through the whole pool by index. The schedule is a select of the next four Mondays at 12:00 UTC rather than an open date, and the caption editor is prefilled from the API's own `suggested_caption` with a counter to 2200 characters, over which Queue disables.
- Item 5, Controls, is not the scheduler switch and the per-kind auto flags named below (both removed per 3.6). It is the SOT's switch-row pattern applied to the three current controls, `invites_enabled`, `publication_enabled` and `external_access_enabled` (the emergency stop's track renders in the red tone while off), plus two read-only status chips, `approvals_enabled` and `roundup_tiles_enabled`, with the hint that both are set from the API when the renderer and the roundup approval flow are activated.

Original text:

1. Stats: members by gender, joined 7 and 30 days, acted in 14 days, stale 30 days, never acted, matches, likes 7 days, messages 7 and 30 days, opted in, approved cards waiting. One endpoint `/admin/growth/stats`, excluding `admin@ahavah.app` and a configured test-account list, is the single source the emails also read.
2. Spotlight queue: thumbnail, kind, member, platform, scheduled time, status, clicks and sign-ups; actions approve, post now, reschedule, cancel, copy caption and open post (for the manual group share); token health chip; manual-task list for Instagram removals.
3. Member of the week: suggested member with reason, alternatives from the eligible pool, caption editor, schedule.
4. Emails: five rows with recipient count, last sent, preview, dry run, send, campaign id shown. Sends are audited in `admin_audit_log`.
5. Controls: scheduler on or off, purge queue, per-kind auto flags.

Desktop primary, read-only on mobile, consistent with the admin spec of 2026-06-06.

## 4. Data flow

1. Member opts in (settings, confirmation page, or admin) -> API sets columns. If they joined in the last 14 days and have a photo, a candidate card is created and E4 is sent.
2. Member approves on the E4 page -> row moves from `awaiting_member` to `review` with the rendered image (the approval page calls the admin render route through the API).
3. A daily Vercel cron in the admin app, `/api/growth/tick`, asks the API for candidates: approved welcomes, the Monday roundup (rendering only approved newcomers with photos), and expired approvals to cancel. Member of the week is created when the admin confirms a pick and the member approves.
4. Admin approves -> `scheduled` -> the per-minute worker claims, re-checks eligibility and the photo, publishes, records external ids -> E5 to the member.
5. Weekly email reads the stats endpoint and the published spotlight of the week, sends through the canonical shell, logs each send.
6. Re-invite reads the dormancy cohort, applies each member's filters, sends, stamps `reinvite_sent_at`, logs each send.

## 5. Error handling

Amended 2026-09-14 (Wave 1): a lost confirmation now has its own state distinct from an ordinary retryable failure, and a stale token is its own rejection.

Amended 2026-09-15 (Wave 2): stored-object deletion and platform removal both retry with confirmed evidence rather than clearing state on a best-effort basis. A `cleanup_job` row is enqueued in the same transaction that decides an object is finished with (the retention sweep, a removal marked done, a withdrawal, a superseded upload); `publishing_queue.image_key`, `image_url` and `image_sha256` are cleared only once a later batch confirms the deletion, so a storage outage or a bad credential leaves the key in place and the job pending rather than orphaning the object in the bucket. A job backs off on failure (1, 10, 60, 360, 1440 minutes, then daily) and is marked `abandoned` with an alert line after 10 attempts, staying in the table for a human to see rather than being swept up automatically. A platform removal task carries `deadline_at = created_at + 72 hours`; each reported failure records evidence (`{at, code, subcode, error}`) and pushes `next_attempt_at` forward on the same backoff, and a failure the worker classifies as a permission problem sets `reason = 'needs_attention'` so it stops being retried automatically and surfaces to an operator instead. A task past its deadline and still open is counted as overdue and surfaced on `GET /admin/growth/removals` and printed by the cleanup cron on every run. The emergency stop halts cleanup exactly as it halts publishing: a halted batch makes no delete call and reports the outstanding pending count instead of attempting anything. This closes the remainder of F09.

- Graph errors: transient -> `failed` with error text, retried next tick up to 3 attempts; a timeout or unclear response after the publish call was actually sent -> `delivery_state = 'delivery_unknown'`, row parks in `review`, never auto-retried, resolved only by an operator or a later definite receipt. Token invalid -> all rows `review`, health chip red, alert email to the admin copy address through SES (the same path the member notes use).
- Render failure -> row stays in its current state with the error; nothing publishes without an image. A revision can be rendered only once; a second attempt is refused (`already_rendered`) rather than overwriting the existing asset.
- Receipt-persistence failure (worker confirms a Graph post but the completion call back to the API does not go through): the worker retries the completion call itself before counting it separately as `receipt_failed`; it is never counted as published on the strength of the Graph call alone.
- A lease token that does not match the row's current lease (another worker already claimed it, or the lease expired) is rejected as `lease_mismatch`; the row is left alone.
- Confirm and card tokens: a token whose nonce is already used, or whose epoch no longer matches the person's current `spotlight_consent_epoch` (set by a withdrawal since the token was issued), is rejected as `stale` rather than acted on.
- Opt-out or deletion races: cancellation runs through the one withdrawal operation in the same transaction as the triggering action; the final dispatch check re-confirms eligibility, consent and lease immediately before any Graph call.
- Email sends are idempotent per member per campaign through `email_send_log`; a crash mid-send resumes without double sends, though campaign mail is at-least-once with visible uncertainty rather than a hard guarantee (see 3.5).

## 6. Testing

Amended 2026-09-14 (Wave 1): the review's acceptance matrix is now the named gate for the first live post.

Amended 2026-09-15 (Wave 2): the Storage and Mail acceptance groups are now in scope. Storage acceptance is proven by the content-hash and compare-and-set tests in `tests/test_spotlight_storage.py` and `tests/test_spotlight_assets.py`, and by the cleanup job's confirmed-deletion and retry tests in `tests/test_spotlight_cleanup.py`. Mail acceptance is proven by the outbox's `FOR UPDATE SKIP LOCKED` reservation, backoff and `acceptance_unknown` reaper tests in `tests/test_email_outbox.py`, and by the enqueue-in-transaction tests for E4 in `tests/test_spotlight_card.py`. The row-by-row proof, including what remains partial or not covered, is in the Wave 2 acceptance evidence document alongside the Wave 1 one.

- API (pytest, disposable stack): migration applies under ON_ERROR_STOP; opt-in endpoints; signed links reject expiry and replay and never act on GET; candidate and eligibility queries never return ineligible members (seeded cases for each exclusion); dormancy cohort with filter fallback (seeded edge cases: acted exactly 30 days ago, resent within 30 days, no filtered newcomers); frequency cap and campaign idempotency; email templates locked like `test_member_note.py`.
- Admin (node:test): port of the President publishing tests with mocked fetch: idempotency, Instagram poll, lost confirmation to review, opt-out cancellation, dry run makes no Graph call, API unreachable exits cleanly.
- Render: `ImageResponse` output compared at 1080 against the Claude Design frames for all three variants, plus a run with real member names including non-Latin scripts.
- Browser: Playwright against the local admin build at 1440 and 390 with fixture API responses; one read-only real-session check on admin.ahavah.app.
- Live: Graph dry run with `?dry=1`, then one real post approved by the owner and the featured member.
- Release acceptance matrix (the adversarial review's own rows: Consent, Lifecycle, Delivery, Storage, Mail, Controls) is the gate for the first live post, run against staging before any post goes out. Wave 1 closes Consent, Lifecycle, Delivery and Controls; Wave 2 closes Storage (immutable rendering and object keys) and Mail (the durable outbox); the matrix's remaining gap is the acceptance-matrix staging run itself (Wave 4).

## 7. Out of scope

Amended 2026-09-14 (Wave 1): two items already listed for a later phase are now explicitly deferred rather than merely unbuilt.

Amended 2026-09-15 (Wave 3): the member photo picker on the card approval page (3.1) is deferred. The design brief dropped the approval page from the design round, saying it reuses the confirm page pattern plus the card, so a thumbnail picker among the member's other photos has no design to build from. Wave 3 ships approve or skip of the rendered card only, the approval passing the card's own `photo_uuid`; the API already answers `new_revision` if a member is later given a way to choose a different photo, so a follow-up design brief can add the picker without an API change.

Facebook group automation (not possible), automatic caption writing by an LLM, comments or DM handling, Instagram stories and reels, ads, member-supplied quotes, localisation, any change to Discover ranking. Also deferred for now: the per-kind `auto_welcome`/`auto_roundup` flags, removed from settings until an auto mode exists that still requires revision-bound consent (3.6); and per-member roundup approval, so roundups stay count-only (`roundup_tiles_enabled = false`) until every pictured member can approve the specific roundup revision they appear in (3.3).

## 8. Pre-flight (must clear before W3 onward)

1. Meta app and permissions: confirm the app behind the Chapman token can be granted the Ahavah Page and Instagram account with `pages_manage_posts` and `instagram_content_publish`, or register and review a new app. Blocking for the posting side.
2. Vercel plan on the ahavah-admin project supports a per-minute cron.
3. Grant the admin role to the owner's own account (audited).
4. List and reconcile any email waves still scheduled on the droplet so no member receives the digest and the weekly email in the same week.
5. Terms and privacy page sections for Spotlight drafted and published with the announcement.

## 9. Rollout

1. W0: nudge removal, migrations, send log, stats endpoint (nothing member-visible).
2. Design round-trip: card template, five email title images, Growth tab screens, confirmation and approval pages.
3. Growth tab stats and emails panel; opt-in surfaces; E1 on owner go.
4. Queue, worker, render, E4 and E5; posting in review mode.
5. First member of the week and weekly email; auto mode for welcomes and roundups after two clean weeks.
