# Ahavah Notification System Redesign — Design Spec

**Date:** 2026-06-14
**Status:** Approved in brainstorming; pending written-spec review
**Repos:** `ahavah-api` (Flask/PostgreSQL backend) · `ahavah-web` (Next.js 16 PWA)

## 1. Problem

Ahavah runs **two parallel, uncoordinated notification systems**:

1. **Modern web push** (`service/notifications/`, VAPID + pywebpush) — fires immediately for matches, messages, likes. Gated by 4 per-event push toggles. VAPID keys are set on prod; deliveries work where a subscription exists.
2. **A legacy email cron** (`ahavah-api-cron-1`, `service/cron/notifications/`) — every ~10s finds unread messages/intros and decides push-vs-email by checking `person.push_token`, a **dead native-app field that 0 of 6 users have**. So it emails *everyone* a **hand-rolled, off-brand email** (old `#70f` purple, no dark mode, none of the `base.py` brand shell; subject "You have a new message 😍").

Consequences observed on prod:
- Web-push subscribers (Ehud, Jem, Lily) are **double-notified** — a proper push *and* the ugly email.
- Non-subscribers (Shemele, John, Tester) get **only the ugly email**.
- "Push is inconsistent across devices" is really a **coverage** problem: only 3/6 users have any subscription; iOS only allows web push to *installed* PWAs; revoked/expired subscriptions linger. The push *engine* is healthy.
- Users have **zero email control** — the only "frequency" settings live in unexposed DB columns and the ugly email's own footer links.

## 2. Goals

1. Redesign the new-message email to the current brand standard (`base.py`), and make it serve all email-capable events.
2. Give users **full per-event control over push and email** via one preference matrix.
3. Make **email a smart fallback** (push primary; email only when push didn't land), eliminating the double-notify.
4. Improve push **reliability/coverage** (prune dead subs, per-device status, self-test, iOS guidance).
5. Retire the blind legacy cron; route everything through one preference-aware, batched dispatcher.

Non-goals: a native mobile app, an in-app notification feed/badge, SMS, weekly digest (deferred), redesigning marketing emails (waitlist/referral/etc. already on-brand).

## 3. The preference matrix (final)

Events × channels, with defaults. Defaults chosen to fix today's pain without spamming.

| Event | `push` default | `email` default | Notes |
|-------|:---:|:---:|-------|
| `messages` | ON | ON | someone you matched with messaged you. Email = **batched fallback**. |
| `matches` | ON | ON | mutual like. Email = fallback. |
| `verification` | ON | ON | bronze/silver/gold approved/rejected. Email = record. **New event.** |
| `likes` | OFF | OFF | someone liked you. Opt-in; reveal stays premium; identity-blind. |
| `profile_views` | OFF | OFF | someone viewed you. Opt-in; identity-blind; throttled. **New event, highest-effort.** |

**Always-on, outside the matrix:** essential account/security email (sign-in / magic-link / OTP). Never muteable.

**Channel semantics** (per the approved "email only if push didn't land" rule):
- `push_<event>` ON + live subscription → push fires.
- `email_<event>` ON → email sends **only if push did not land** for that user/event (see §5 for the precise definition).
- `push` OFF + `email` ON → email always (email is the channel).
- both OFF → no notification for that event.

## 4. Data model

Replace the wide-but-push-only `notification_preference` with a wide per-event × per-channel shape. Migration `00NN_notification_channel_prefs.sql`:

```sql
-- Add email_* columns and the two new events; drop the unused weekly digest.
ALTER TABLE notification_preference
  ADD COLUMN IF NOT EXISTS email_messages       BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS email_matches        BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS email_likes          BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS push_verification    BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS email_verification   BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS push_profile_views   BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS email_profile_views  BOOLEAN NOT NULL DEFAULT FALSE;
-- push_matches, push_messages, push_likes already exist (mig 0013).
-- push_weekly_digest is retained but ignored (no destructive drop; deferred feature).
```

Final logical columns: `push_<e>` + `email_<e>` for `e ∈ {messages, matches, likes, verification, profile_views}`. Rows stay lazily created; a missing row → the defaults above. The legacy `person.chats_notification` / `intros_notification` immediacy columns are **repurposed only for email-batching cadence** (see §5), not exposed as new UI.

## 5. Dispatch architecture

One conceptual entry point in `service/notifications/`:

```
notify(person_id, event, *, push={title, body, url, tag}, email={subject, template_kwargs})
```

**Real-time events (`messages`, `matches`, `likes`, `verification`, `profile_views`):**
1. If `push_<event>` is ON and the user has ≥1 live subscription → `send_to_user_safe(...)` (existing path), which prunes dead subs on 404/410.
2. Email is **never** sent inline here. Email is the batched fallback (below).

**Batched email fallback (the redesigned cron):**
The cron is rewritten to be push- and preference-aware, and stays **batched** (this preserves anti-spam behavior — one email summarizing unread, never one per message):
- For each user with pending unread/eventful state, for each event with `email_<event>` ON:
  - **Skip** if `push_<event>` is ON **and** the user has ≥1 live push subscription. (This is the operational definition of "push landed" — see approximation note.)
  - Otherwise send the brand-shell email (§6), respecting the per-user cadence from `chats_notification` (Immediately / Daily / Every-3-days / Weekly / Never) so a chatty thread can't generate a flood.
- The `if not person.push_token` branch is **deleted**; `push_token` is no longer consulted.

**"Push landed" is an approximation.** Web push transient failures are swallowed (no per-message delivery receipt). We therefore treat *"has ≥1 live subscription with the event enabled"* as "reachable by push," and suppress the email on that basis. To keep this honest, dead subscriptions must be pruned aggressively (§7) so "has a subscription" ≈ "actually reachable." Documented as an accepted limitation.

**Event trigger points:**
- `messages` — already fires push in `service/chat/messagestorage/__init__.py:_push_chat_message`; email handled by the batched cron.
- `matches` — `service/decisions/__init__.py` (mutual-like path) push stays; add email-fallback eligibility to the cron's scan.
- `verification` — add `notify(...)` where a `verification_job` transitions to approved/rejected: the selfie-result handler(s) behind `/verification-selfie` `/verification-multi-selfie` `/check-verification`, and the Stripe Identity webhook `/webhooks/stripe-identity` (gold/ID). Implementer places the call at the single point where final status is written.
- `likes` — `service/decisions/__init__.py` (incoming like). Default off; respects `push_likes`/`email_likes`.
- `profile_views` — **new tracking required**: record a view when a profile is fetched by another user, throttle to **at most one notification per (viewer → viewed) per 24h**, identity-blind ("Someone viewed your profile"). Phased last.

## 6. Email redesign

Delete `service/cron/notifications/template/__init__.py` (the hand-rolled email). Create one parameterized template `emails/notification.py` on the `base.py` shell, covering every email-capable event:

- `render(title, preheader, body_html, footer_html)` shell — white card on canvas, dark-mode swap.
- `chip(label)` — event label ("New message", "It's a match", "Verification").
- `title_image(light_png, dark_png, alt, width)` — pre-rendered Ultra PNGs per event family (`title-message.png`/`-wht`, `title-match.png`/`-wht`, `title-verified.png`/`-wht`), produced with the existing `scripts/render-title-png.mjs` convention (528px @2x, transparent, ink-on-light + white-on-dark).
- lede paragraph(s) in `INK_SOFT`; for `messages` the batched copy ("You have N unread messages" / per-sender when N==1).
- lime `button("Open Ahavah →", url)` CTA + plaintext URL fallback.
- Canonical footer with the **notification** unsubscribe (see §8), not the marketing one.
- Brand tokens only (`INK`, `INDIGO #5524F5`, `LIME`, `LAVENDER`, `CANVAS`, `PANEL`, `SERIF`/`SANS`).

Exemplar to match: `emails/waitlist_welcome.py`.

## 7. Push reliability

- **Aggressive pruning:** keep 404/410 deletion; additionally prune any subscription that has failed every send for K consecutive attempts (track `consecutive_failures` + `last_failure_at` on `push_subscription`; delete past threshold). This makes "has subscription" ≈ "reachable" (supports §5).
- **Per-device status** surfaced on the new screen: `unsupported | default | denied | subscribed-here | subscribed-elsewhere`.
- **Self-test:** a "Send a test notification" button → `POST /notifications/test` → `send_to_user_safe(this device only)`, so a user can confirm push works on *that* device.
- **iOS guidance:** keep the existing install-prompt flow; the dedicated screen states plainly that iPhone requires "Add to Home Screen" for push, and that **email fallback covers them** until then. (This is the real coverage win — iOS-browser users now get a clean branded email instead of nothing.)

## 8. Unsubscribe semantics

Notification emails must carry RFC-8058 `List-Unsubscribe` + `List-Unsubscribe-Post` (Gmail/Yahoo requirement). One-click unsubscribe on a notification email must **not** touch the account or marketing prefs. New signed-token endpoint:
- `GET/POST /notifications/unsubscribe?token=<signed>` where the token encodes `(person_id, scope)` and `scope ∈ {this-event, all-notification-email}`.
- Default scope from a notification email = **that event's `email_<event>`** → set false. A secondary "turn off all notification emails" link sets every `email_*` false.
- Distinct from `service/unsubscribe`'s marketing unsubscribe (waitlist/beta lists). The two never cross-affect.

## 9. Frontend: dedicated Notifications screen

A redesigned, dedicated screen (replacing the current 4-toggle `/settings/notifications`), reached by a prominent entry from **Profile**. Old route redirects to it.

Sections (built with kit primitives + design tokens only, per `feedback_new_surfaces_frontend_design` → run `/frontend-design` then `/ui-implementer`):
1. **This device** — per-device push status + master enable/blocked message + "Send a test notification".
2. **What to notify me about** — the matrix: one row per event, each with a **Push** toggle and an **Email** toggle. Per-event one-line descriptions. Push column disabled with an explainer when push isn't enabled on this device.
3. **Email** — a "turn off all notification emails" affordance + note that sign-in/security email is always sent.

Hooks: extend `useNotificationPreferences()` to the new columns (single-field PATCH, optimistic + rollback). `GET/PATCH /notifications/preferences` return/accept all `push_*`/`email_*` fields.

## 10. Phasing

- **Phase 1 — Kill the pain (highest priority):** redesigned `emails/notification.py`; rewrite the cron to be push+preference-aware and batched (delete `push_token` branch + old template). Outcome: no more ugly/redundant email; subscribers get push only; non-subscribers (incl. iOS-browser) get a clean batched email.
- **Phase 2 — Control:** migration (email_* + new-event columns); extend prefs endpoints + hook; build the dedicated Notifications screen with the full matrix; unsubscribe-token endpoint.
- **Phase 3 — New events + reliability:** wire `verification` and `likes`; subscription failure-pruning + `/notifications/test`. **`profile_views` last** (view-tracking + 24h throttle + identity-blind), as the highest-effort/most-speculative item.

## 11. Testing

- Backend unit tests: `notify()` channel selection truth-table (push on/off × email on/off × has-subscription) → asserts push sent / email queued / suppressed correctly; cadence throttle; unsubscribe-token scope mapping.
- Email rendering: render `emails/notification.py` for each event to HTML and **screenshot in headless Chrome** (light + dark) — verify brand shell, Ultra title image, CTA, no `#70f`.
- Push: `/notifications/test` round-trip on a real subscribed device; verify 410-pruning + consecutive-failure pruning with a synthetic dead endpoint.
- Migration idempotency (re-runnable) + defaults applied to legacy rows.

## 12. Open approximations / accepted limitations

- "Push landed" = "has a live subscription with the event enabled" (no true delivery receipt). Mitigated by aggressive pruning.
- `profile_views` notifications are inherently noisy; mitigated by default-off + 24h per-viewer throttle + identity-blind. If it proves spammy in practice, it can be removed without touching the rest of the matrix.
- iOS-browser (non-PWA) users cannot receive web push at all; email fallback is their channel until they install.
