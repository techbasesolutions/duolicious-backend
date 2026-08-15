# Mechanism audit findings — 2026-08-11

Verified register of broken/suspect mechanisms found by the deep audit
that followed the zombie-pass fix (`6edac1b`, skipped upsert ON CONFLICT
DO NOTHING froze `created_at`; 96 percent of all passes were
permanently recycling). Three audit passes: visibility-surface
consistency, upsert/idempotency sweep, lifecycle crons. Every BROKEN
item below was re-verified against the code by the operator session
before this spec was written. Live-data checks are noted inline.

Rule set being enforced (migrations 0035 + 0036):
- Your own pass hides them from YOUR deck for 7 days. One-directional.
- Reports/blocks are mutual and permanent on EVERY surface.
- `hide_me_from_strangers` hides from stranger surfaces; mutual-consent
  surfaces (a match) must be exempt.
- Matched pairs never re-enter the deck.

## P0 — safety, money, matching

**F1 BROKEN. Match creation is gated on the like INSERT winning.**
`service/decisions/__init__.py:103` (`Q_RECORD_LIKE`): `liked` insert is
ON CONFLICT DO NOTHING and the `inserted_match` CTE hangs off the
insert's RETURNING. Two members liking each other concurrently under
REPEATABLE READ each insert without seeing the other: no match row,
and every later re-like conflicts so the match can never be created.
The pair then vanishes from Matches, Liked-you, and You-liked
(exclusion clauses at :204 and :257). Bonus defect: the conflict path
re-fires the "Someone likes you" push on every duplicate like.
Live check 2026-08-11: all 4 current mutual-like pairs have match rows;
nobody is stuck yet. Repair shape proven in-repo:
`service/tokens/actions/super_like.py:34-56` derives reciprocity from
current state, not insert success.

**F2 BROKEN. Payment webhooks latch the replay guard BEFORE applying
paid effects.** `service/checkout/__init__.py:775` and
`service/revenuecat_webhook/__init__.py:108-116`: `record_event`
commits the event-id latch in its own tx, then grant/stipend/credit
run in later txs. Any failure in between + provider retry = retry hits
the replay path and returns 200; the member paid and never receives
Premium/tokens, permanently. Concrete lossy path:
`invoice.payment_succeeded` (`checkout:880-898`) drops the renewal
stipend if `stripe.Subscription.retrieve` throws once.

**F3 BROKEN. Inbox treats a plain pass as a permanent mutual block.**
`service/person/sql/__init__.py:1521-1540`: raw `EXISTS (skipped)`
both directions with no `reported` check and no 7-day window, feeding
the CASE gates at :1573-1669. A plain pass by either side archives the
conversation forever and blanks the peer card (name/photo NULLed),
while the chat gate itself correctly allows messaging. The exact bug
class 0035 was written to kill, on the one surface it missed.

**F4 BROKEN. /matches ignores blocks and deactivation.**
`service/decisions/__init__.py:137-171` (`Q_LIST_MATCHES`) and
:351-380 (`Q_GET_MATCH`): WHERE is membership-only. Reporting/blocking
someone never removes the match card in either direction; a
grace-period-deleted account lingers as a match card for 7 days.
Contrast `Q_LIST_INCOMING/OUTGOING_LIKES` (:211-213, :264-266) which
check both.

## P1 — visibility correctness

**F5 BROKEN. `hide_me_from_strangers` has no match exemption.**
`Q_SELECT_PROSPECT_PROFILE` (`person/sql:925-953`) and
`Q_SELECT_CONVERSATION_PROSPECT` (:1334-1349) exempt only
`messaged.subject_person_id = prospect`. A fresh match with a hidden
member: match card exists, but chat header 404s and profile collapses
to the limited stub until the HIDDEN member messages first. Same hole
for the `privacy_verification_level_id` gate.

**F6 BROKEN. Automodded bots stay in deck + map.** Trustworthy report
sets `verification_required = TRUE` (`antiabuse/sql:262-275`); Views
and cold-chat honor it; `Q_UNCACHED_SEARCH_2` and `Q_MAP_MARKERS`
have no gate at all (0018 said search should).

**F7 SUSPECT->fix. Deck lacks the defensive match guard.** The likes
lists carry an `ahavah_match` guard; the deck relies only on the
searcher's own `liked` row. `/decisions/reset` wipes `liked` while the
match survives: your confirmed match re-enters your deck.

**F8 SUSPECT->fix. Empty gender preference: map fails open, deck fails
closed.** `search/sql:433-436` vs :132. Zero preference rows = full
map, permanently empty deck, silently.

**F9 SUSPECT->fix. Rewind does not clear search_cache.**
`tokens/actions/rewind.py` deletes skip+swipe but not the pair's
cache rows (see-passes and take-back-like both do). Paid rewind whose
profile does not reappear until the next cold rebuild.

## P2 — lifecycle

**F10 BROKEN. Premium expiry enforced nowhere.**
`entitlements.expire_stale` has zero callers; no reconciliation cron
exists; `has_entitlement` never reads `subscription_expires_at`. The
entire community's 6-month grants (Dec 2026 - Feb 2027) expire on
paper only. Also the false safety net assumed by F2.

**F11 BROKEN. Hard delete leaks CDN objects.**
`service/cron/pendingdeletion/__init__.py:52-60` raw-deletes person;
photo rows cascade but nothing stages uuids into
`undeleted_photo`/`undeleted_audio` (the only feeds photocleaner/
audiocleaner read). Deleted members' photos stay publicly fetchable
forever. The admin-ban path (`Q_DELETE_ACCOUNT`) stages correctly.

**F12 BROKEN (regression from 0037). Referral rows survive hard
delete.** 0024's FK cascade via beta_signup was the only cleanup;
0037 dropped the FK; pendingdeletion was not updated. Deleted members'
emails persist in `referral`, and UNIQUE(invitee_email) blocks
re-attribution on re-registration.

**F13 BROKEN. Sign-in during the 7-day grace resurrects visibility but
not the account.** `Q_MAYBE_SIGN_IN` sets `activated=TRUE` but leaves
`deletion_requested_at`; pendingdeletion keys only on that column. A
regretful deleter who signs back in is visible, chatting, and then
hard-deleted mid-conversation on day 7. (Directly relevant: Ruth,
self-deleted 79 seconds after the Aug 9 digest; purge lands Aug 16
~14:02 UTC.)

**F14 BROKEN. Admin reactivation is reverted within 5 minutes.**
`users_action_routes.py` `_Q_REACTIVATE` sets only `activated`;
autodeactivate2 (armed, 300s poll, 30-50 day window on
`last_online_time`) immediately re-deactivates and re-emails. Club
counts also drift (deactivate decrements, reactivate never
re-increments).

**F15 BROKEN. `last_online_time` is written only by OTP sign-in and
chat-socket presence.** REST usage (swiping, browsing) never touches
it. A member actively using the app whose websocket does not connect
accrues 30 days of "inactivity" and gets force-logged-out + hidden by
autodeactivate2. Likely explanation for the June dormants (Krystal,
Michael, Vict, Shaun).

**F16 SUSPECT->fix. Onboardee wipe window measures creation, not
activity.** `Q_MAYBE_DELETE_ONBOARDEE` (`person/sql:319`) wipes wizard
state older than 1 hour by `created_at`; the field upserts never
refresh anything. A slow onboarder who re-verifies OTP after an hour
loses everything. Same class as the zombie-pass bug.

**F17 SUSPECT->fix. Onboardee photo replacement forgets `hash`.**
`person/__init__.py:708` upsert updates uuid/blurhash/extra_exts but
not `hash` (main-profile upsert at :1563 does). Later photo bans latch
the WRONG hash: offender re-uploadable, innocent image blocked.

**F18 SUSPECT->fix. Selfie anti-replay latch consumes the hash before
the upload succeeds.** `person/__init__.py:2503` latches
`verification_photo_hash` + job row, then the object-store put can
fail; retry with the same capture = V_REUSED_SELFIE for a selfie never
used.

**F19 SUSPECT->fix. NSFW auto-delete is silent and leaks storage.**
`garbagerecords` q7 hard-deletes photos with nsfw_score > 0.8 with no
staging into undeleted_photo (CDN leak, same as F11) and no admin
visibility. On a 34-member faith community the false-positive cost is
high.

**F20 config. `DUO_REPORT_EMAIL=ahavah@example.com`** - abuse-report
emails go nowhere; admin reports screen is the only surface.

**F21 minor->fix. Beta re-opt-in after unsubscribe is a silent no-op.**
`service/beta/__init__.py:18` DO NOTHING cannot clear
`unsubscribed_at`; UI reports success, member never hears anything.

**F22 janitorial. Dead `swipe` exclusion in the deck** (`search/sql:
163-167`): table nothing inserts into, comment claims "any direction"
while SQL is one-directional. Trap if ever populated.

## OK by design (checked, leave alone)

Map showing passed/liked/matched members (directory); search_cache
invalidation on skip (both parties, precedence correct) and
re-validation of activated/hidden on cached reads; Views tabs; the
`messaged` DO NOTHING (first-contact time IS the rate-limit semantic);
club-join counting; robot9000 and profile-view upserts (correct
window pattern); betareengagement (cannot email live members);
notifications cron gates.

## Operational decisions attached to this audit (owner, not code)

- Ruth: any outreach must happen before Aug 16 ~10:02 AST; if she
  signs in without emailing support, F13 deletes her anyway (fixed by
  this plan if deployed first).
- December premium cliff: scheduling expire_stale (F10) makes the
  Dec 16 mass-expiry REAL. Owner comms (extension, pricing, warning
  emails) should be decided before that date; out of this plan's
  scope.
- Yazy illustrated avatar: outreach sent Aug 10; admin removal if no
  change by ~Aug 17.
