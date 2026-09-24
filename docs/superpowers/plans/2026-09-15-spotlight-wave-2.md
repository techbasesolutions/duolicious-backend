Linear: TEC-869

# Spotlight Wave 2 (recoverable external effects) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close F06, F07, F08 and F09 from the 2026-09-14 adversarial review so that a rendered card can never be replaced under an approval, every campaign and transactional email is a durable, resumable, visibly-accounted record rather than a fire-and-forget, and cleanup of platform posts and stored images retries with evidence and escalates before its deadline.

**Architecture:** Three additive records in `duo_api`: content-hashed, per-revision image keys with a compare-and-set attach (F06); an `email_outbox` table that is the idempotency point for every email, drained by a cron in bounded batches with at-least-once semantics and an explicit `acceptance_unknown` state (F07, F08); and a `cleanup_job` table plus attempt, deadline and evidence columns on `spotlight_removal_task`, so deletions are confirmed before references are cleared and overdue work is visible (F09). The emergency stop (`external_access_enabled`) halts every outbound call, storage included, and the outstanding work is counted rather than lost. Nothing here changes consent semantics; Wave 1's invariants stay as they are.

**Tech Stack:** as Wave 1 (Flask, psycopg, PostgreSQL migrations `0046` onward, Pillow already in `requirements.txt`, boto3 for Spaces; admin worker in Next.js under `src/lib`, node test harness).

**Spec:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` (sections 3.5, 3.7, 5 as amended 2026-09-14; Task 8 of this plan amends them again); triage: `docs/superpowers/plans/2026-09-14-spotlight-adversarial-remediation-triage.md` (Wave 2 items 6, 7, 8); review source: `C:/Users/Ehud/Documents/2026-09-14-community-spotlight-adversarial-review-and-remediation.md` (F06 to F09 and the Storage and Mail acceptance rows).

## Global Constraints

- Production is live at api cdc6b2c, admin 842ebe5, web 7728165 (deployed 2026-09-15). Work on branches `spotlight-wave-2` from `ahavah/main` (api) and `master` (admin); push only at the owner's finishing decision. Migrations are new numbered files from `0046`, idempotent (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`, `DROP CONSTRAINT IF EXISTS` before `ADD CONSTRAINT`).
- Never nest `api_tx` (production code or tests: build fixtures before opening a transaction). One API implementer at a time; never two agents staging on one tree; never `git stash`. No literal `%` in psycopg SQL. No em dashes anywhere in touched files. Sentence case in user-facing strings. GET never mutates. Secrets in headers only. No Co-Authored-By trailer. `git add` only your paths.
- Outbound calls (SMTP, Spaces, Graph) never run inside a database transaction and never block one; they run after the commit or in a cron on a thread, exactly as `service/cron/spotlightretention` does.
- Owner decision 3 in force: campaign mail is at-least-once with visible uncertainty. A crash between SMTP acceptance and the record is surfaced as `acceptance_unknown`, never silently resent and never silently dropped.
- Backend command: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 512). Apply a migration to the warm stack with `docker compose -f docker-compose.test.yml exec -T postgres psql -U postgres -d duo_api -v ON_ERROR_STOP=1 < migrations/<file>`. Admin: `node --test tests/*.test.mjs` (baseline 51), `npx tsc --noEmit`, `npx next build`. Tests that flip a `spotlight_setting` restore it in `try/finally`. `make_person` addresses are suppressed; mailable test addresses use `@ahavah-test.invalid`. Storage and SMTP are monkeypatched in tests; no test reaches the network.
- Every finding's tests encode the desired behaviour (the review's probes inverted).

---

## File structure

| File | Responsibility |
| --- | --- |
| `migrations/0046_spotlight_wave2.sql` | `email_outbox`, `cleanup_job`, new columns on `spotlight_removal_task` and `publishing_queue`, seed nothing |
| `service/spotlight/storage.py` | Spaces client: `put_png`, `delete_images` returning confirmed keys, `presign`, `make_public`, `PNG` validation helper |
| `service/spotlight/assets.py` (new) | Content-hashed key naming, compare-and-set attach for the image route |
| `service/campaigns/outbox.py` (new) | `enqueue`, `reserve`, `drain`, `reap_reserved`, `status` |
| `service/campaigns/runner.py` | `run_campaign` becomes enqueue-only (build then `enqueue`) |
| `service/cron/emailoutbox/__init__.py` (new) | drains the outbox every 30 seconds in batches of 50 |
| `service/spotlight/cleanup.py` (new) | `enqueue_asset_delete`, `run_cleanup_batch`, backoff, evidence |
| `service/cron/spotlightcleanup/__init__.py` (new) | runs `run_cleanup_batch` every 10 minutes |
| `service/cron/spotlightretention/__init__.py` | enqueues asset deletes instead of deleting inline |
| `service/api/admin/spotlight_routes.py` | image route (F06), removal failure route, outbox status route, invite-pending route |
| `service/api/admin/growth_routes.py` | E1/E2/E3 send endpoints enqueue and return a job id |
| `emails/spotlight_card_ready.py`, `emails/spotlight_card_live.py` | E4/E5 enqueued in the caller's transaction; daemon threads removed |
| admin `src/lib/publishing.ts`, `growth-server.ts`, `tick.ts` | removal failure reporting, exact missing-target codes, invite-pending call |

Migration content used by every task (Task 1 writes it verbatim):

```sql
-- migrations/0046_spotlight_wave2.sql
CREATE TABLE IF NOT EXISTS email_outbox (
  id                  bigserial PRIMARY KEY,
  campaign            text NOT NULL,
  campaign_id         text NOT NULL,
  person_id           int NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  email               text NOT NULL,
  payload             jsonb NOT NULL,
  exempt              boolean NOT NULL DEFAULT false,
  unsub_scope         text NOT NULL,
  state               text NOT NULL DEFAULT 'queued',
  attempts            int NOT NULL DEFAULT 0,
  next_attempt_at     timestamptz NOT NULL DEFAULT NOW(),
  reserved_at         timestamptz,
  sent_at             timestamptz,
  provider_message_id text,
  last_error          text,
  created_at          timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE (campaign, campaign_id, person_id)
);
ALTER TABLE email_outbox DROP CONSTRAINT IF EXISTS email_outbox_state_check;
ALTER TABLE email_outbox ADD CONSTRAINT email_outbox_state_check
  CHECK (state IN ('queued','reserved','accepted','acceptance_unknown','failed','skipped'));
CREATE INDEX IF NOT EXISTS email_outbox_due_idx ON email_outbox (state, next_attempt_at);
CREATE INDEX IF NOT EXISTS email_outbox_campaign_idx ON email_outbox (campaign, campaign_id);

CREATE TABLE IF NOT EXISTS cleanup_job (
  id               bigserial PRIMARY KEY,
  kind             text NOT NULL,
  target           text NOT NULL,
  state            text NOT NULL DEFAULT 'pending',
  attempts         int NOT NULL DEFAULT 0,
  next_attempt_at  timestamptz NOT NULL DEFAULT NOW(),
  evidence         jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_error       text,
  created_at       timestamptz NOT NULL DEFAULT NOW(),
  done_at          timestamptz,
  UNIQUE (kind, target)
);
ALTER TABLE cleanup_job DROP CONSTRAINT IF EXISTS cleanup_job_kind_check;
ALTER TABLE cleanup_job ADD CONSTRAINT cleanup_job_kind_check CHECK (kind IN ('asset_delete'));
ALTER TABLE cleanup_job DROP CONSTRAINT IF EXISTS cleanup_job_state_check;
ALTER TABLE cleanup_job ADD CONSTRAINT cleanup_job_state_check CHECK (state IN ('pending','done','failed','abandoned'));
CREATE INDEX IF NOT EXISTS cleanup_job_due_idx ON cleanup_job (state, next_attempt_at);

ALTER TABLE spotlight_removal_task
  ADD COLUMN IF NOT EXISTS attempts        int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS deadline_at     timestamptz,
  ADD COLUMN IF NOT EXISTS last_error      text,
  ADD COLUMN IF NOT EXISTS evidence        jsonb NOT NULL DEFAULT '{}'::jsonb;
UPDATE spotlight_removal_task SET deadline_at = created_at + interval '72 hours' WHERE deadline_at IS NULL;

ALTER TABLE publishing_queue ADD COLUMN IF NOT EXISTS image_sha256 text;
```

---

### Task 1: Migration 0046

**Files:** create `migrations/0046_spotlight_wave2.sql` (verbatim above); create `tests/test_migration_0046.py`.

**Produces:** the tables and columns every later task reads.

- [ ] **Step 1: Failing test**

```python
# tests/test_migration_0046.py
from database import api_tx

def _cols(tx, t):
    return {r['column_name'] for r in tx.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %(t)s", dict(t=t)).fetchall()}

def test_0046_schema():
    with api_tx('read committed') as tx:
        assert {'campaign','campaign_id','person_id','email','payload','exempt','unsub_scope','state','attempts',
                'next_attempt_at','reserved_at','sent_at','provider_message_id','last_error'} <= _cols(tx, 'email_outbox')
        assert {'kind','target','state','attempts','next_attempt_at','evidence','last_error','done_at'} <= _cols(tx, 'cleanup_job')
        assert {'attempts','next_attempt_at','deadline_at','last_error','evidence'} <= _cols(tx, 'spotlight_removal_task')
        assert 'image_sha256' in _cols(tx, 'publishing_queue')

def test_0046_outbox_unique_and_states(make_person):
    import psycopg, pytest
    p = make_person(name='Outbox')
    with api_tx() as tx:
        tx.execute("INSERT INTO email_outbox (campaign, campaign_id, person_id, email, payload, unsub_scope) VALUES ('e1','run-1',%(p)s,'a@ahavah-test.invalid','{}','notifications')", dict(p=p['id']))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with api_tx() as tx:
            tx.execute("INSERT INTO email_outbox (campaign, campaign_id, person_id, email, payload, unsub_scope) VALUES ('e1','run-1',%(p)s,'a@ahavah-test.invalid','{}','notifications')", dict(p=p['id']))
    with pytest.raises(psycopg.errors.CheckViolation):
        with api_tx() as tx:
            tx.execute("UPDATE email_outbox SET state = 'sent' WHERE person_id = %(p)s", dict(p=p['id']))
```

- [ ] **Step 2: Run, expect failure** (tables missing).
- [ ] **Step 3: Write the migration** (verbatim block above).
- [ ] **Step 4: Apply, run scoped, apply again (idempotent), run the full suite** (baseline 512).
- [ ] **Step 5: Commit** `feat(db): email outbox, cleanup jobs, removal deadlines, image hashes (0046)`

---

### Task 2: Storage client: validated uploads, confirmed deletions, private-until-approved objects (F06 part 1, F09 part 1)

**Files:** modify `service/spotlight/storage.py`; create `tests/test_spotlight_storage.py` (move the two existing storage tests out of `tests/test_spotlight_retention.py`).

**Produces:**

```python
class InvalidImage(ValueError): ...          # reason in str(): not_png, bad_dimensions, too_large
def validate_png(data: bytes, *, size=(1080, 1080), max_bytes=5_000_000) -> str   # returns sha256 hex; raises InvalidImage
def put_png(key: str, data: bytes, *, public: bool = False) -> None                 # ACL private unless public
def make_public(key: str) -> None
def presign(key: str, seconds: int = 900) -> str
def delete_images(keys: list[str]) -> list[str]   # keys CONFIRMED deleted (NoSuchKey counts as confirmed); [] when unconfigured
```

Rules: `validate_png` decodes with Pillow (`Image.open(BytesIO(data)); img.verify()` then reopen for `.size` and `.format == 'PNG'`); `delete_images` parses the `DeleteObjects` response: keys in `Deleted` plus keys whose `Errors[].Code == 'NoSuchKey'` are confirmed; every other error leaves the key unconfirmed; a raised exception confirms nothing for that batch; unconfigured storage confirms nothing (returns `[]`) and prints once. `put_png` uses `ACL='private'` unless `public=True`; `make_public` calls `put_object_acl(ACL='public-read')`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_spotlight_storage.py
import io, pytest
from PIL import Image
from service.spotlight import storage as st

def _png(w=1080, h=1080):
    buf = io.BytesIO(); Image.new('RGB', (w, h), 'white').save(buf, 'PNG'); return buf.getvalue()

def test_validate_png_accepts_square_and_returns_hash():
    data = _png(); h = st.validate_png(data)
    assert len(h) == 64 and h == st.validate_png(data)

@pytest.mark.parametrize('data,reason', [(b'notapng', 'not_png'), (_png(800, 800), 'bad_dimensions')])
def test_validate_png_rejects(data, reason):
    with pytest.raises(st.InvalidImage, match=reason):
        st.validate_png(data)

def test_validate_png_rejects_oversize():
    with pytest.raises(st.InvalidImage, match='too_large'):
        st.validate_png(_png(), max_bytes=10)

class _Bucket:
    def __init__(self, response=None, raise_exc=None):
        self.calls = []; self.response = response; self.raise_exc = raise_exc
    def delete_objects(self, Delete):
        self.calls.append([o['Key'] for o in Delete['Objects']])
        if self.raise_exc: raise self.raise_exc
        return self.response
    def put_object(self, **kw): self.calls.append(('put', kw.get('Key'), kw.get('ACL')))

def test_delete_images_returns_only_confirmed(monkeypatch):
    b = _Bucket(response={'Deleted': [{'Key': 'a'}], 'Errors': [{'Key': 'b', 'Code': 'NoSuchKey'}, {'Key': 'c', 'Code': 'AccessDenied'}]})
    monkeypatch.setattr(st, '_configured', lambda: True); monkeypatch.setattr(st, '_bucket', lambda: b)
    assert sorted(st.delete_images(['a', 'b', 'c'])) == ['a', 'b']

def test_delete_images_confirms_nothing_on_exception_or_unconfigured(monkeypatch):
    monkeypatch.setattr(st, '_configured', lambda: True); monkeypatch.setattr(st, '_bucket', lambda: _Bucket(raise_exc=RuntimeError('down')))
    assert st.delete_images(['a']) == []
    monkeypatch.setattr(st, '_configured', lambda: False)
    assert st.delete_images(['a']) == []

def test_put_png_is_private_by_default(monkeypatch):
    b = _Bucket(); monkeypatch.setattr(st, '_configured', lambda: True); monkeypatch.setattr(st, '_bucket', lambda: b)
    st.put_png('k', b'x'); st.put_png('k2', b'x', public=True)
    assert b.calls == [('put', 'k', 'private'), ('put', 'k2', 'public-read')]
```

- [ ] **Step 2: Run, expect failure. Step 3: Implement. Step 4: Update every `delete_images` caller for the new return type** (`withdrawal.py`, `spotlightretention`, the removal-done route: they only log the count for now; Tasks 5 and 6 move them onto cleanup jobs). **Full suite.**
- [ ] **Step 5: Commit** `feat(spotlight): validated private uploads and confirmed deletions in the storage client`

---

### Task 3: Content-hashed immutable keys with compare-and-set attach (F06 part 2)

**Files:** create `service/spotlight/assets.py`; modify `service/api/admin/spotlight_routes.py` (`post_growth_queue_image`, `post_growth_queue_approve`), `service/spotlight/approval.py` (`card_state` returns a presigned `image_url` for a private object); modify `tests/test_spotlight_routes.py`, `tests/test_spotlight_card.py`.

**Produces:**

```python
# service/spotlight/assets.py
def asset_key(request_key: str, revision_id: int, sha256: str, platform: str) -> str
    # f"spotlight/{request_key}/{revision_id}-{sha256[:16]}-{platform}.png"
def attach_platform_image(tx, request_key: str, platform: str, revision_id: int, key: str, url: str, sha256: str) -> str
    # compare-and-set: UPDATE publishing_queue SET image_key, image_url, image_sha256 WHERE request_key AND platform
    #   AND current_revision_id = %(rev)s AND status IN ('awaiting_member','awaiting_render','review')
    # returns 'attached' or 'superseded' (0 rows)
def complete_render_if_ready(tx, request_key: str, revision_id: int) -> bool
    # when every row of the key has image_sha256 and current_revision_id = revision_id: attach_render(tx, revision_id,
    #   asset_hash=<facebook row's image_sha256, else the first row's>, image_key, image_url) once; True when it attached
```

Image route flow: decode base64; `validate_png` (400 `{error: 'invalid_image', reason}` on `InvalidImage`); read `known` (404 / `no_revision` / `already_rendered` / `bad_status` as today); `key = asset_key(...)`; `put_png(key, data)` (private); `attach_platform_image`; on `'superseded'` enqueue `cleanup_job(asset_delete, key)` via Task 5's `enqueue_asset_delete` (until Task 5 lands, call `delete_images([key])` directly and leave a comment; Task 5 replaces it) and answer 409 `{error: 'superseded'}`; on `'attached'` call `complete_render_if_ready`. The approve route (`review` to `scheduled`) calls `make_public` for every row's `image_key` AFTER the commit; a storage failure there answers 503 `{error: 'storage_unavailable'}` and leaves the row in `review`. `card_state` returns `image_url = presign(image_key)` when the revision has an image (the object is private until scheduled; the member's preview link is short-lived).

- [ ] **Step 1: Failing tests** (route tests use the cron header; monkeypatch `service.spotlight.storage.put_png`, `make_public`, `presign`, `delete_images`)

```python
# additions to tests/test_spotlight_routes.py
def test_image_route_rejects_invalid_png(client, make_person, monkeypatch):
    # 800x800 png -> 400 {'error': 'invalid_image', 'reason': 'bad_dimensions'}; put_png never called

def test_image_route_uses_content_hashed_key_and_private_acl(client, make_person, monkeypatch):
    # capture put_png(key, data, public=False); key matches r'^spotlight/<rk>/<rev>-[0-9a-f]{16}-facebook\.png$';
    # row.image_sha256 == sha256(data); second identical upload for instagram completes the set;
    # spotlight_revision.asset_hash == facebook row's image_sha256

def test_image_route_superseded_when_revision_changes_mid_upload(client, make_person, monkeypatch):
    # put_png side effect performs edit_caption (new revision) inside its own api_tx; route answers 409 superseded;
    # delete_images (or cleanup enqueue) called with the orphan key; no row image columns changed

def test_approve_makes_images_public_after_commit(client, make_person, monkeypatch):
    # rendered + consented row in review; admin approve -> status scheduled and make_public called for both keys;
    # make_public raising -> 503 storage_unavailable and status still review
```

```python
# addition to tests/test_spotlight_card.py
def test_card_state_presigns_private_preview(make_person, monkeypatch):
    # presign monkeypatched to return 'https://signed/' + key; card_state()['image_url'] == 'https://signed/' + image_key
```

- [ ] **Step 2 to 4: red, implement, scoped then full suite.**
- [ ] **Step 5: Commit** `feat(spotlight): content-hashed immutable image keys, compare-and-set attach, private previews`

---

### Task 4: Durable email outbox (F07, F08)

**Files:** create `service/campaigns/outbox.py`; modify `service/campaigns/runner.py`, `emails/spotlight_card_ready.py`, `emails/spotlight_card_live.py`, `service/api/admin/spotlight_routes.py` (E4/E5 call sites), `service/api/admin/growth_routes.py` (send endpoints); create `service/cron/emailoutbox/__init__.py` and register it in `service/cron/__init__.py`; create `tests/test_email_outbox.py`; update `tests/test_spotlight_card.py`, `tests/test_spotlight_occurrence.py`, `tests/test_spotlight_announcement.py`, `tests/test_community_weekly.py`, `tests/test_reinvite.py` (whatever asserts on `sent` today asserts on outbox rows plus a drained send).

**Produces:**

```python
# service/campaigns/outbox.py
RETRY_BACKOFF_SECONDS = (60, 600, 3600)          # attempts 1, 2, 3; then 'failed'
RESERVATION_TIMEOUT_SECONDS = 600                 # a reservation older than this becomes acceptance_unknown
BATCH = 50

def enqueue(tx, *, campaign, campaign_id, person_id, email, subject, html, from_addr, unsub_scope,
            list_unsubscribe=None, exempt=False) -> int | None
    # INSERT ... ON CONFLICT (campaign, campaign_id, person_id) DO NOTHING RETURNING id; None = already queued or sent (the idempotency point)
def reserve(tx, limit=BATCH) -> list[dict]
    # UPDATE ... SET state='reserved', reserved_at=NOW(), attempts=attempts+1 WHERE id IN (SELECT id FROM email_outbox
    #   WHERE state='queued' AND next_attempt_at <= NOW() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %(n)s) RETURNING *
def reap_reserved(tx) -> int
    # reserved AND reserved_at < NOW() - RESERVATION_TIMEOUT -> acceptance_unknown, last_error='reservation expired'
def mark_accepted(tx, id, provider_message_id) -> None     # state accepted, sent_at NOW(); also log_send(...)
def mark_skipped(tx, id, reason) -> None                     # suppressed | unsubscribed | capped
def mark_failed_attempt(tx, id, error, attempts) -> None     # attempts < 3: queued with next_attempt_at += backoff; else failed
def drain(tx_factory, smtp, limit=BATCH) -> dict
    # one transaction: reap_reserved, reserve rows; then, per row OUTSIDE any transaction: re-check is_suppressed_send,
    #   campaign_unsubscribed and can_send (unless exempt) in a short transaction, mark_skipped when they refuse;
    #   smtp.send(...); then a short transaction mark_accepted; an exception from smtp.send -> mark_failed_attempt.
    # returns dict(reserved, accepted, skipped, failed, unknown_reaped)
def status(tx, campaign, campaign_id) -> dict                  # counts per state
```

`run_campaign` keeps its signature but its `send=True` path enqueues (no SMTP), returning `dict(queued, skipped_*, dry_run, campaign_id)`; the dry run still builds and sends nothing. The admin send endpoints (`/admin/growth/emails/<c>/send`) return `{campaign_id, queued, ...}` immediately; `GET /admin/growth/emails/<c>/status/<campaign_id>` returns `status()`. E4: `emails/spotlight_card_ready.py` exposes `enqueue_card_ready(tx, person_id, request_key) -> int | None` (builds the message with the given `tx`, minting the card nonce in the same transaction, then `enqueue`); the welcome and member-of-week routes call it inside their transaction when `invite_sent` is true; `send_card_ready_async` is deleted. E5 likewise: `enqueue_card_live(tx, person_id, request_key, external_post_id, platform, post_url)` called inside the complete and reconcile routes' transactions where `_send_card_live` was; `send_card_live_async` deleted. The cron `emailoutbox` runs `drain(api_tx, make_aws_smtp())` on a thread every 30 seconds (env `DUO_CRON_EMAIL_OUTBOX_POLL_SECONDS`, default 30), skipping the SMTP client construction when nothing is reserved.

- [ ] **Step 1: Failing tests**

```python
# tests/test_email_outbox.py  (fixtures before transactions; never nest api_tx)
import pytest
from database import api_tx
from service.campaigns import outbox

class _Smtp:
    def __init__(self, fail_times=0, crash=False):
        self.sent = []; self.fail_times = fail_times; self.crash = crash
    def send(self, **kw):
        if self.fail_times: self.fail_times -= 1; raise RuntimeError('smtp down')
        self.sent.append(kw); return 'mid-' + str(len(self.sent))

def _enqueue(pid, email, campaign='e1', cid='run-1', exempt=False):
    with api_tx() as tx:
        return outbox.enqueue(tx, campaign=campaign, campaign_id=cid, person_id=pid, email=email, subject='s', html='<p>h</p>',
                              from_addr='hello@ahavah.app', unsub_scope='notifications', exempt=exempt)

def _row(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT * FROM email_outbox WHERE person_id = %(p)s ORDER BY id DESC LIMIT 1", dict(p=pid)).fetchone()

def test_enqueue_is_the_idempotency_point(make_person):
    p = make_person(name='Once', email='once@ahavah-test.invalid')
    assert _enqueue(p['id'], 'once@ahavah-test.invalid') is not None
    assert _enqueue(p['id'], 'once@ahavah-test.invalid') is None
    smtp = _Smtp(); outbox.drain(api_tx, smtp); outbox.drain(api_tx, smtp)
    assert len(smtp.sent) == 1 and _row(p['id'])['state'] == 'accepted' and _row(p['id'])['provider_message_id'] == 'mid-1'

def test_concurrent_drains_never_double_send(make_person):
    # two reserve() calls in two transactions on separate raw connections (see tests/test_spotlight_delivery.py for the
    # second-connection pattern): the second sees zero rows for the ids the first reserved (FOR UPDATE SKIP LOCKED)

def test_smtp_failure_retries_then_fails(make_person):
    p = make_person(name='Retry', email='retry@ahavah-test.invalid'); _enqueue(p['id'], 'retry@ahavah-test.invalid')
    smtp = _Smtp(fail_times=3)
    for _ in range(3):
        with api_tx() as tx: tx.execute("UPDATE email_outbox SET next_attempt_at = NOW() WHERE person_id = %(p)s", dict(p=p['id']))
        outbox.drain(api_tx, smtp)
    r = _row(p['id']); assert r['state'] == 'failed' and r['attempts'] == 3 and 'smtp down' in r['last_error']

def test_reservation_that_never_completes_becomes_acceptance_unknown(make_person):
    p = make_person(name='Lost', email='lost@ahavah-test.invalid'); _enqueue(p['id'], 'lost@ahavah-test.invalid')
    with api_tx() as tx: outbox.reserve(tx)                      # reserved, process "dies" here
    with api_tx() as tx: tx.execute("UPDATE email_outbox SET reserved_at = NOW() - interval '11 minutes' WHERE person_id = %(p)s", dict(p=p['id']))
    smtp = _Smtp(); out = outbox.drain(api_tx, smtp)
    assert out['unknown_reaped'] == 1 and _row(p['id'])['state'] == 'acceptance_unknown' and smtp.sent == []

def test_suppression_and_unsubscribe_checked_at_send_time(make_person):
    p = make_person(name='Unsub', email='unsub@ahavah-test.invalid'); _enqueue(p['id'], 'unsub@ahavah-test.invalid', campaign='e2', cid='w1')
    with api_tx() as tx: tx.execute("UPDATE person SET community_unsubscribed_at = NOW() WHERE id = %(p)s", dict(p=p['id']))
    # enqueue with unsub_scope 'community' for this one; drained -> skipped, last_error 'unsubscribed', nothing sent

def test_status_counts(make_person):
    # two queued, one accepted -> status() == dict(queued=1, reserved=0, accepted=1, acceptance_unknown=0, failed=0, skipped=0) after one drain of batch 1
```

```python
# tests/test_spotlight_card.py addition
def test_e4_is_enqueued_in_the_candidate_transaction_and_survives_restart(client, make_person, monkeypatch):
    # approvals on; POST welcome -> one email_outbox row campaign 'e4', campaign_id 'e4-<rk>', state queued, payload html contains the card token;
    # no thread started (monkeypatch threading.Thread to raise); drain with a stub smtp sends exactly one
```

- [ ] **Step 2 to 4: red, implement, adjust existing email tests, scoped then full suite.**
- [ ] **Step 5: Commit** `feat(mail): durable email outbox with at-least-once delivery and visible uncertainty; E4 and E5 enqueued with their triggers`

---

### Task 5: Cleanup jobs with confirmed deletion and retained keys (F09 part 2)

**Files:** create `service/spotlight/cleanup.py`, `service/cron/spotlightcleanup/__init__.py` (register in `service/cron/__init__.py`); modify `service/cron/spotlightretention/__init__.py`, `service/api/admin/spotlight_routes.py` (removal-done route, image route's superseded path from Task 3), `service/spotlight/withdrawal.py` (cancelled rows' images go to jobs, not inline deletes); create `tests/test_spotlight_cleanup.py`.

**Produces:**

```python
# service/spotlight/cleanup.py
BACKOFF_SECONDS = (60, 600, 3600, 21600, 86400)   # attempts 1..5; later attempts stay at 86400
MAX_ATTEMPTS = 10                                  # then 'abandoned' with an alert line

def enqueue_asset_delete(tx, key: str) -> int | None        # ON CONFLICT (kind, target) DO NOTHING
def due_jobs(tx, limit=200) -> list[dict]                   # pending AND next_attempt_at <= NOW() ORDER BY id FOR UPDATE SKIP LOCKED
def record_result(tx, job_id, *, confirmed: bool, error: str | None) -> str   # done | pending (backoff) | abandoned
def run_cleanup_batch(tx_factory, delete=delete_images) -> dict
    # halted (external_access_enabled != 'true'): return dict(halted=True, outstanding=<pending count>) with no delete call
    # else: one transaction reserves due jobs (attempts+1); OUTSIDE the transaction delete(keys) -> confirmed list;
    #   one transaction records each result and, for confirmed keys, NULLs publishing_queue.image_key/image_url where image_key = key
    # returns dict(reserved, done, retried, abandoned, halted=False)
def overdue_removals(tx) -> int                              # open removal tasks with deadline_at < NOW()
```

Rules: keys are never cleared from `publishing_queue` until a job confirms the deletion; the retention sweep enqueues jobs and clears nothing itself; the removal-done route enqueues the row's image key and clears nothing; `withdraw_member` enqueues cancelled rows' keys instead of calling `delete_images` (the withdrawal transaction no longer performs any outbound call); an abandoned job prints `spotlight_cleanup: ABANDONED <key> after <n> attempts` and stays in the table; the cron prints `spotlight_cleanup: <n> removal task(s) overdue` whenever `overdue_removals` is positive and `spotlight_cleanup: halted, <n> job(s) outstanding` under the stop. `GET /admin/growth/removals` adds `overdue` and `outstanding_cleanup` counts to its response.

- [ ] **Step 1: Failing tests**

```python
# tests/test_spotlight_cleanup.py
from database import api_tx
from service.spotlight import cleanup
from service.spotlight.queue import set_setting

def test_enqueue_is_idempotent():
    with api_tx() as tx:
        assert cleanup.enqueue_asset_delete(tx, 'spotlight/x/1-abc-facebook.png') is not None
        assert cleanup.enqueue_asset_delete(tx, 'spotlight/x/1-abc-facebook.png') is None

def test_partial_confirmation_retains_unconfirmed_keys():
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, 'k-ok'); cleanup.enqueue_asset_delete(tx, 'k-bad')
    out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: [k for k in keys if k == 'k-ok'])
    assert out['done'] == 1 and out['retried'] == 1
    with api_tx('read committed') as tx:
        rows = {r['target']: r for r in tx.execute("SELECT target, state, attempts, next_attempt_at > NOW() AS later FROM cleanup_job WHERE target IN ('k-ok','k-bad')").fetchall()}
    assert rows['k-ok']['state'] == 'done' and rows['k-bad']['state'] == 'pending' and rows['k-bad']['later'] is True

def test_abandon_after_max_attempts(capsys):
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, 'k-never')
        tx.execute("UPDATE cleanup_job SET attempts = 9, next_attempt_at = NOW() WHERE target = 'k-never'")
    cleanup.run_cleanup_batch(api_tx, delete=lambda keys: [])
    with api_tx('read committed') as tx:
        assert tx.execute("SELECT state FROM cleanup_job WHERE target = 'k-never'").fetchone()['state'] == 'abandoned'
    assert 'ABANDONED k-never' in capsys.readouterr().out

def test_emergency_stop_halts_cleanup_and_counts_outstanding():
    with api_tx() as tx:
        cleanup.enqueue_asset_delete(tx, 'k-halt'); set_setting(tx, 'external_access_enabled', 'false')
    try:
        calls = []
        out = cleanup.run_cleanup_batch(api_tx, delete=lambda keys: calls.append(keys) or [])
        assert out['halted'] is True and out['outstanding'] >= 1 and calls == []
    finally:
        with api_tx() as tx: set_setting(tx, 'external_access_enabled', 'true')

def test_retention_and_removal_done_keep_keys_until_confirmed(make_person):
    # published row older than 90 days with image_key: retention_sweep(tx) enqueues a job and image_key is still set;
    # run_cleanup_batch with a confirming delete -> image_key NULL; same for the removal-done route (image_key set until the job confirms)

def test_overdue_removals_counted(make_person):
    # open removal task with deadline_at in the past -> overdue_removals(tx) == 1 and GET /admin/growth/removals?pending=1 body['overdue'] == 1
```

- [ ] **Step 2 to 4: red, implement, adjust `tests/test_spotlight_retention.py` and `tests/test_spotlight_withdrawal.py` (deletions now enqueue), scoped then full suite.**
- [ ] **Step 5: Commit** `feat(spotlight): cleanup jobs confirm deletions before clearing keys; retention and removals enqueue; overdue removals visible`

---

### Task 6: Platform removal attempts, exact missing-target handling, deadlines (F09 part 3)

**Files:** modify `service/api/admin/spotlight_routes.py` (new `POST /admin/growth/removals/<id>/failed`, deadline stamping in `_file_removal_tasks` via `service/spotlight/withdrawal.py`), `tests/test_spotlight_routes.py`; admin `src/lib/growth-server.ts` (`removalFailed(id, body)`), `src/lib/publishing.ts` (`processRemovals`), `tests/publishing.test.mjs`.

**Produces (API):** `POST /admin/growth/removals/<id>/failed` body `{error: str, code?: int, subcode?: int}` (admin-or-cron): `attempts + 1`, `last_error`, `evidence` appended with `{at, code, subcode, error}`, `next_attempt_at = NOW() + backoff(attempts)` using `cleanup.BACKOFF_SECONDS`; a body with `permission: true` (see worker rule below) sets `reason = 'needs_attention'` so the worker stops retrying it and an operator sees it. `GET /removals?pending=1` returns only tasks whose `next_attempt_at <= NOW()` and whose reason is actionable, plus the `overdue` count from Task 5. New tasks get `deadline_at = created_at + interval '72 hours'` (`REMOVAL_DEADLINE_HOURS = 72` in `withdrawal.py`; Task 8 reconciles this with the spec's stated removal promise and records the ruling).

**Produces (admin):** `processRemovals` treats a Graph error as "already gone" only when the response carries `error.code === 100 && error.error_subcode === 33` (the documented "object does not exist" pair) or HTTP 404 with that code; a `code === 10` or `code === 200` (permission) reports `removalFailed(id, {error, code, subcode, permission: true})`; any other failure reports `removalFailed(id, {error, code, subcode})` and moves on; the result gains `reported: number` and `needs_attention: number`.

- [ ] **Step 1: Failing tests** (API: a failed report increments attempts, appends evidence and pushes `next_attempt_at` forward; a permission report flips the reason; the pending list hides tasks not yet due. Admin: a DELETE answering `{error: {code: 100, error_subcode: 33}}` marks done; `{code: 10}` reports with `permission: true` and counts `needs_attention`; a network error reports and counts `reported`, never `deleted`.)
- [ ] **Step 2 to 4: red, implement, both suites, tsc, build.**
- [ ] **Step 5: Commits** API `feat(spotlight): removal attempts with evidence, exact missing-target codes, 72 hour deadlines`; admin `fix(admin): removals report failures with evidence and treat only the documented missing-target error as done`

---

### Task 7: Invites that were withheld while approvals were paused (the Wave 1 gap)

**Files:** modify `service/api/admin/spotlight_routes.py` (`GET /candidates` gains `invites_pending: int`; new `POST /admin/growth/spotlight/invite-pending`), `emails/spotlight_card_ready.py`; admin `src/lib/tick.ts`, `src/lib/growth-server.ts`, `tests/tick.test.mjs`; API tests in `tests/test_spotlight_controls.py`.

**Produces:** `invite-pending` (admin-or-cron): when `approvals_enabled == 'true'` and `invites_enabled == 'true'`, for every `awaiting_member` welcome or member-of-week request with no `email_outbox` row for campaign `e4` and `campaign_id = 'e4-<request_key>'`, `enqueue_card_ready` in one transaction per request; answers `{queued: n, skipped: n}`; 409 `{error: 'approvals_paused'}` or `invites_paused` otherwise. `runTick` calls it when `candidates.invites_pending > 0` and reports `invites_queued` in `TickResult`.

- [ ] **Step 1: Failing tests** (API: two candidates created while approvals were off have no outbox rows; after enabling approvals, `invite-pending` enqueues two and a second call enqueues zero; with approvals off it answers 409. Admin: the tick calls the route only when `invites_pending > 0` and passes the count through.)
- [ ] **Step 2 to 5: red, implement, suites, commit** API `feat(spotlight): send the invites withheld while approvals were paused`; admin `feat(admin): tick queues pending invites`.

---

### Task 8: Spec, ledger and handoff amendments

**Files:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` (3.5 outbox semantics and the `acceptance_unknown` state; 3.7 content-hashed keys, private-until-scheduled objects, presigned previews, compare-and-set; 5 cleanup states, deadlines, needs_attention, abandoned; 6 the Storage and Mail acceptance groups now in scope), the Wave 2 ledger, `docs/superpowers/handovers/2026-09-13-community-spotlight-handoff.md` (section 12: Wave 2). Reconcile `REMOVAL_DEADLINE_HOURS` with the spec's stated removal promise (spec 3.1) and record the ruling in the ledger and the spec.

- [ ] **Step 1 to 2: amend, sweep for em dashes, commit** `docs(spotlight): wave 2 amendments`.

---

### Task 9: Wave 2 acceptance run

Run the review's Storage and Mail acceptance rows (plus the re-run of Consent, Lifecycle, Delivery, Controls) against the local stack with the new tests; a dry `drain` and `run_cleanup_batch` against stub SMTP and storage; record in `docs/superpowers/plans/2026-09-15-spotlight-wave-2-evidence.md` with proven / partial / not covered per row and the open items. Commit `docs(spotlight): wave 2 acceptance evidence`.

---

## Self-review record

- Coverage: F06 (Tasks 2, 3), F07 (Task 4), F08 (Task 4, Task 7 for the withheld-invite gap), F09 (Tasks 2, 5, 6); spec and evidence (Tasks 8, 9). The review's acceptance rows: Storage (immutable identity: Task 3; concurrent edit safe: Task 3 compare-and-set; corrupt images rejected: Task 2; failed deletion retried with retained keys: Task 5), Mail (concurrent submits: Task 4 SKIP LOCKED; process death: reservation reaper; acceptance uncertainty: `acceptance_unknown`; suppression at send: Task 4; E4/E5 survive restart: Task 4 enqueue-in-transaction), Operator controls "cleanup deadlines visible": Tasks 5 and 6.
- Consistency: `delete_images` returns confirmed keys (Task 2) and is consumed by `run_cleanup_batch` (Task 5); `enqueue_asset_delete` (Task 5) is called by the image route (Task 3, with the interim direct delete replaced), retention, removal-done and withdrawal; `enqueue_card_ready` (Task 4) is called by the routes and by `invite-pending` (Task 7); `BACKOFF_SECONDS` (Task 5) is reused by the removal failure route (Task 6); `image_sha256` (Task 1) is written by Task 3.
- Not in this wave: signed URLs for published assets (they are public once scheduled, as Graph must fetch them), Instagram media deletion (still manual), attribution (F10, Wave 3), the acceptance-matrix staging run (Wave 4).
