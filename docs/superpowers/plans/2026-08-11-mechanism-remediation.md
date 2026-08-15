# Mechanism Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every BROKEN/fix-rated mechanism from the 2026-08-11 audit: matching race, lossy payment webhooks, pass-as-block inbox, blocked matches, hidden-member match deadlock, lifecycle cron gaps, deletion leaks, and the presence/deactivation loop.

**Architecture:** All fixes are surgical SQL/handler corrections inside the existing service modules, each locked by a DB-backed pytest in the existing docker-compose test harness. One new cron task (entitlements sweep) and one tiny migration (0038, onboardee.updated_at). No API-shape changes; the one frontend task is an isolated hook tweak in ahavah-web.

**Tech Stack:** Python 3.11 / Flask / psycopg3 / PostgreSQL 16, pytest via `docker compose -f docker-compose.test.yml`, Next.js 16 + vitest (T20 only).

**Spec:** `docs/superpowers/specs/2026-08-11-mechanism-audit-findings.md` (findings F1-F22; read it first — every task cites its finding).

## Global Constraints

- Test runner: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh <paths> -q` from `d:/Antigravity/ahavah-api`. Full suite must stay green after every task (baseline: 179 passed).
- Deploys: `git push` on `ahavah/main`; CI applies `migrations/*.sql` (idempotent) before the api rebuild. Never hand-apply to prod.
- Migrations must be idempotent (IF NOT EXISTS / CREATE OR REPLACE).
- No em dashes in any member-facing string. Sentence case.
- No literal `%` inside SQL strings passed to psycopg (write "percent").
- `token_ledger.person_id` holds the person UUID, not the integer id.
- Fixture helper: `make_person` (tests/conftest.py) returns `{'id', 'uuid'}`; fetch email via `SELECT email FROM person WHERE id = ...`.
- Commit after every task with the exact message given; end commits with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Phase P0 — matching, money, safety surfaces

### Task 1: Match creation derives from state, not insert success (F1)

**Files:**
- Modify: `service/decisions/__init__.py:88-135` (`Q_RECORD_LIKE`)
- Modify: `service/decisions/__init__.py:431-457` (duplicate-like push suppression — the branch that runs when the query returns no `inserted_match` row)
- Test: `tests/test_match_creation.py` (create)

**Interfaces:**
- Consumes: `make_person` fixture; `liked(liker_id, liked_id)`; `ahavah_match(user_a_id, user_b_id, match_id uuid)`.
- Produces: `Q_RECORD_LIKE` returning `(match_id, peer_id, peer_uuid, was_new_like boolean)`; route behavior: push notification fires only when `was_new_like` is true.

- [ ] **Step 1: Write the failing tests**

```python
"""F1: match creation must be derivable from CURRENT state, not from
whether this call's INSERT won. Repro of the live hazard: the liked
half-rows both exist (concurrent inserts) but no match row was made;
a re-like must repair it."""
from database import api_tx
from service.decisions import Q_RECORD_LIKE


def _like(tx, me_id: int, prospect_uuid: str):
    return tx.execute(Q_RECORD_LIKE, dict(
        me_id=me_id, prospect_uuid=prospect_uuid)).fetchone()


def _uuid_of(tx, pid):
    return tx.execute('SELECT uuid::text AS u FROM person WHERE id = %(p)s',
                      dict(p=pid)).fetchone()['u']


def test_relike_repairs_a_matchless_mutual_like(make_person):
    a = make_person(name='Aleph', gender='Man')
    b = make_person(name='Bet', gender='Woman')
    with api_tx() as tx:
        # Simulate the REPEATABLE READ race: both half-rows exist,
        # no match row (exactly what two concurrent likes produce).
        tx.execute(
            'INSERT INTO liked (liker_id, liked_id) VALUES '
            '(%(a)s, %(b)s), (%(b)s, %(a)s)', dict(a=a['id'], b=b['id']))
        row = _like(tx, a['id'], _uuid_of(tx, b['id']))
        assert row is not None and row['match_id'] is not None, \
            're-like on a matchless mutual pair must create the match'
        assert row['was_new_like'] is False
        n = tx.execute('SELECT count(*) AS n FROM ahavah_match WHERE '
                       '(user_a_id = LEAST(%(a)s,%(b)s) AND user_b_id = '
                       'GREATEST(%(a)s,%(b)s))',
                       dict(a=a['id'], b=b['id'])).fetchone()['n']
        assert n == 1


def test_duplicate_like_returns_existing_match_and_flags_not_new(make_person):
    a = make_person(name='Gimel', gender='Man')
    b = make_person(name='Dalet', gender='Woman')
    with api_tx() as tx:
        b_uuid = _uuid_of(tx, b['id'])
        a_uuid = _uuid_of(tx, a['id'])
        first = _like(tx, a['id'], b_uuid)
        assert first['match_id'] is None and first['was_new_like'] is True
        second = _like(tx, b['id'], a_uuid)
        assert second['match_id'] is not None and second['was_new_like']
        again = _like(tx, a['id'], b_uuid)
        assert again['match_id'] == second['match_id'], \
            'duplicate like must return the existing match, not null'
        assert again['was_new_like'] is False


def test_one_sided_duplicate_like_stays_matchless(make_person):
    a = make_person(name='He', gender='Man')
    b = make_person(name='Vav', gender='Woman')
    with api_tx() as tx:
        b_uuid = _uuid_of(tx, b['id'])
        _like(tx, a['id'], b_uuid)
        row = _like(tx, a['id'], b_uuid)
        assert row['match_id'] is None and row['was_new_like'] is False
```

- [ ] **Step 2: Run to verify failure**

Run: the Global Constraints runner with `tests/test_match_creation.py -q`
Expected: FAIL (`was_new_like` column missing; repair case returns no row).

- [ ] **Step 3: Rewrite Q_RECORD_LIKE**

Replace the `reciprocal`/`inserted_match`/final-SELECT portion (keep the `new_like` CTE exactly as is) with state-derived CTEs, following the proven shape in `service/tokens/actions/super_like.py:34-56`:

```sql
), prospect AS (
    SELECT id FROM person
    WHERE uuid = uuid_or_null(%(prospect_uuid)s)
      AND id <> %(me_id)s
), reciprocal AS (
    -- Derived from CURRENT state, not from whether new_like won.
    SELECT 1 FROM liked rev, prospect p
    WHERE rev.liker_id = p.id
      AND rev.liked_id = %(me_id)s
), my_like AS (
    -- My half-row, whether inserted this call or previously.
    SELECT 1 FROM liked l, prospect p
    WHERE l.liker_id = %(me_id)s AND l.liked_id = p.id
    UNION ALL
    SELECT 1 FROM new_like
    LIMIT 1
), upserted_match AS (
    INSERT INTO ahavah_match (user_a_id, user_b_id)
    SELECT LEAST(%(me_id)s, p.id), GREATEST(%(me_id)s, p.id)
    FROM prospect p
    WHERE EXISTS (SELECT 1 FROM reciprocal)
      AND EXISTS (SELECT 1 FROM my_like)
    ON CONFLICT (user_a_id, user_b_id) DO NOTHING
    RETURNING match_id, user_a_id, user_b_id
), the_match AS (
    -- Upserted this call OR already existing: either way, return it.
    SELECT match_id, user_a_id, user_b_id FROM upserted_match
    UNION ALL
    SELECT m.match_id, m.user_a_id, m.user_b_id
    FROM ahavah_match m, prospect p
    WHERE m.user_a_id = LEAST(%(me_id)s, p.id)
      AND m.user_b_id = GREATEST(%(me_id)s, p.id)
      AND EXISTS (SELECT 1 FROM reciprocal)
    LIMIT 1
)
SELECT
    m.match_id::text AS match_id,
    CASE WHEN m.user_a_id = %(me_id)s THEN m.user_b_id
         ELSE m.user_a_id END AS peer_id,
    peer.uuid::text AS peer_uuid,
    EXISTS (SELECT 1 FROM new_like) AS was_new_like
FROM the_match m
JOIN person peer
  ON peer.id = CASE WHEN m.user_a_id = %(me_id)s THEN m.user_b_id
                    ELSE m.user_a_id END
UNION ALL
SELECT NULL, NULL, NULL, EXISTS (SELECT 1 FROM new_like)
WHERE NOT EXISTS (SELECT 1 FROM the_match)
```

In the route handler (`:431-457`), gate the "Someone likes you" push and the like-notification email on `row['was_new_like']` being true; gate the "It's a match" push on `match_id IS NOT NULL AND was_new_like` (a repair re-like may fire the match push once — acceptable and correct, since the pair never saw it).

- [ ] **Step 4: Run tests**

Run: `tests/test_match_creation.py tests/test_search.py -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add service/decisions/__init__.py tests/test_match_creation.py
git commit -m "fix(match): derive match creation from state, not insert success

F1: two concurrent mutual likes under REPEATABLE READ each inserted
without seeing the other, creating a matchless pair invisible on every
tab and unrepairable (re-likes hit ON CONFLICT DO NOTHING and the match
CTE hung off the insert's RETURNING). Match upsert now derives from
current liked rows, so any later like repairs the pair. Duplicate likes
no longer re-fire the push notification.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 2: Payment webhooks apply effects before latching the replay guard (F2)

**Files:**
- Modify: `service/entitlements/__init__.py` (add `record_event_tx(tx, ...)` beside `record_event`)
- Modify: `service/checkout/__init__.py:770-782` and the `invoice.payment_succeeded` branch `:880-898`
- Modify: `service/revenuecat_webhook/__init__.py:100-120`
- Test: `tests/test_webhook_replay_safety.py` (create)

**Interfaces:**
- Produces: `record_event_tx(tx, event_id, event_type, app_user_id, payload) -> bool` — same semantics as `record_event` but participates in the caller's transaction.
- Ordering contract (both webhooks): resolve + validate first; open ONE `api_tx`; apply all effects that can run inside a tx; call `record_event_tx` LAST inside that same tx. Effects that must open their own tx (`entitlements.grant`) run BEFORE the latch tx; if they raise, the event is never latched and the provider retry re-runs cleanly. `grant()` is idempotent (re-grant bumps expiry to the later value only), so the crash window between grant-tx and latch-tx double-applies harmlessly instead of losing the effect. Prefer lost-latch over lost-grant.

- [ ] **Step 1: Failing test**

```python
"""F2/F3: a webhook failure AFTER the replay latch used to eat the paid
effect forever (provider retry hits the replay path). Contract under
test: if effect application raises, the event id must NOT be latched."""
import pytest
from database import api_tx
from service import entitlements


def test_record_event_tx_latches_inside_caller_tx(make_person):
    p = make_person(name='Payer', gender='Man')
    class Boom(Exception):
        pass
    with pytest.raises(Boom):
        with api_tx() as tx:
            assert entitlements.record_event_tx(
                tx, event_id='evt_test_rollback', event_type='t',
                app_user_id=str(p['id']), payload={}) is True
            raise Boom()
    # The latch must have rolled back with the failed effects.
    with api_tx() as tx:
        n = tx.execute(
            "SELECT count(*) AS n FROM entitlement_event "
            "WHERE event_id = 'evt_test_rollback'").fetchone()['n']
    assert n == 0, 'latch survived a rolled-back effect tx'


def test_record_event_tx_replay_returns_false(make_person):
    p = make_person(name='Payer2', gender='Man')
    with api_tx() as tx:
        assert entitlements.record_event_tx(
            tx, event_id='evt_test_replay', event_type='t',
            app_user_id=str(p['id']), payload={}) is True
    with api_tx() as tx:
        assert entitlements.record_event_tx(
            tx, event_id='evt_test_replay', event_type='t',
            app_user_id=str(p['id']), payload={}) is False
        tx.execute("DELETE FROM entitlement_event WHERE event_id = 'evt_test_replay'")
```

- [ ] **Step 2: Run to verify failure** — `record_event_tx` undefined.

- [ ] **Step 3: Implement**

In `service/entitlements/__init__.py`, extract the INSERT from `record_event` into:

```python
def record_event_tx(tx, event_id: str, event_type: str,
                    app_user_id: str, payload: dict) -> bool:
    """Same replay latch as record_event, but inside the CALLER's tx so
    the latch commits or rolls back atomically with the effects it
    guards. Webhook ordering contract (F2): apply effects first, latch
    LAST. Returns False on replay."""
    row = tx.execute(
        """
        INSERT INTO entitlement_event (event_id, event_type, app_user_id, payload)
        VALUES (%(event_id)s, %(event_type)s, %(app_user_id)s, %(payload)s)
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """,
        dict(event_id=event_id, event_type=event_type,
             app_user_id=app_user_id, payload=json.dumps(payload)),
    ).fetchone()
    return row is not None


def record_event(event_id, event_type, app_user_id, payload) -> bool:
    with api_tx() as tx:
        return record_event_tx(tx, event_id, event_type, app_user_id, payload)
```

Rework both webhook handlers to the ordering contract: replace the up-front `record_event(...)` call with a cheap replay PRE-CHECK (`SELECT 1 FROM entitlement_event WHERE event_id = ...` — read-only, safe to repeat), run the effect branches, and at the very end open `api_tx` and call `record_event_tx`; where a branch already opens a tx for token credits (e.g. the stipend credit), move the latch call into that same tx. Any raise before the latch = 500 = provider retries = clean re-run.

- [ ] **Step 4: Run** `tests/test_webhook_replay_safety.py tests/test_tokens.py -q` — PASS.

- [ ] **Step 5: Commit**

```bash
git add service/entitlements/__init__.py service/checkout/__init__.py service/revenuecat_webhook/__init__.py tests/test_webhook_replay_safety.py
git commit -m "fix(payments): latch webhook replay guard AFTER effects, atomically

F2: record_event committed the replay latch before grant/stipend ran in
later transactions, so any failure in between made the provider retry
hit the replay path and the member permanently lost what they paid for.
Effects now run first and the latch commits inside the same tx as the
final effect; a pre-latch failure 500s and the retry re-runs cleanly.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 3: Inbox honors blocks only, never plain passes (F3)

**Files:**
- Modify: `service/person/sql/__init__.py:1521-1540`
- Test: `tests/test_inbox_visibility.py` (create)

**Interfaces:** none new — the two boolean columns keep their names; their semantics tighten to reported-only.

- [ ] **Step 1: Failing test** — call the inbox query directly with a fixture pair (the query takes `%(person_id)s` and the prospect uuid list; read the exact param names at the top of the query before writing the test):

```python
from database import api_tx
from service.person.sql import Q_SELECT_INBOX_INFO


def _flags(tx, me, peer_uuid):
    rows = tx.execute(Q_SELECT_INBOX_INFO, dict(
        person_id=me, prospect_uuids=[peer_uuid])).fetchall()
    assert rows, 'inbox query returned nothing for the fixture pair'
    return rows[0]


def test_plain_pass_does_not_touch_inbox(make_person):
    me = make_person(name='Inbal', gender='Woman')
    peer = make_person(name='Yona', gender='Man')
    with api_tx() as tx:
        peer_uuid = tx.execute('SELECT uuid::text AS u FROM person WHERE id=%(p)s',
                               dict(p=peer['id'])).fetchone()['u']
        for subject, obj in ((me['id'], peer['id']), (peer['id'], me['id'])):
            tx.execute('INSERT INTO skipped (subject_person_id, object_person_id, reported) '
                       'VALUES (%(s)s, %(o)s, FALSE) ON CONFLICT (subject_person_id, object_person_id) '
                       'DO UPDATE SET reported = EXCLUDED.reported',
                       dict(s=subject, o=obj))
        row = _flags(tx, me['id'], peer_uuid)
        assert not row['person_skipped_prospect'], 'plain pass leaked into inbox gating'
        assert not row['prospect_skipped_person'], 'their plain pass leaked into my inbox'


def test_report_still_gates_inbox(make_person):
    me = make_person(name='Tikva', gender='Woman')
    peer = make_person(name='Zev', gender='Man')
    with api_tx() as tx:
        peer_uuid = tx.execute('SELECT uuid::text AS u FROM person WHERE id=%(p)s',
                               dict(p=peer['id'])).fetchone()['u']
        tx.execute('INSERT INTO skipped (subject_person_id, object_person_id, reported) '
                   'VALUES (%(s)s, %(o)s, TRUE)', dict(s=me['id'], o=peer['id']))
        row = _flags(tx, me['id'], peer_uuid)
        assert row['person_skipped_prospect'], 'report must still gate the inbox'
```

(Adjust the query constant name and params to the real ones at `service/person/sql/__init__.py` around line 1500 — the constant feeding `post_inbox_info` at `service/person/__init__.py:1083`.)

- [ ] **Step 2: Run — first assertion fails** (plain pass currently gates).

- [ ] **Step 3: Fix** — add `AND reported` to BOTH `EXISTS` subqueries at :1521-1540:

```sql
        EXISTS (
            SELECT 1 FROM skipped
            WHERE subject_person_id = %(person_id)s
              AND object_person_id = id_table.id
              AND reported            -- blocks only; a plain pass is
                                      -- deck state, never inbox state
                                      -- (0035/0036, fixed 2026-08-11)
        ) AS person_skipped_prospect,
        EXISTS (
            SELECT 1 FROM skipped
            WHERE subject_person_id = id_table.id
              AND object_person_id = %(person_id)s
              AND reported
        ) AS prospect_skipped_person
```

- [ ] **Step 4: Run** the new file + `tests/test_visibility_rules.py -q` — PASS.

- [ ] **Step 5: Commit**

```bash
git add service/person/sql/__init__.py tests/test_inbox_visibility.py
git commit -m "fix(inbox): plain passes never archive or blank a conversation

F3: the inbox was the last surface on the pre-0035 rule, treating a
plain pass (either direction, forever) as a mutual block: conversation
shunted to archive, peer card blanked. Blocks (reported=TRUE) keep
gating; passes are deck state only.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 4: Matches exclude blocked pairs and deactivated peers (F4)

**Files:**
- Modify: `service/decisions/__init__.py` `Q_LIST_MATCHES` (:137-171) and `Q_GET_MATCH` (:351-380)
- Test: `tests/test_match_visibility.py` (create)

- [ ] **Step 1: Failing test**

```python
from database import api_tx
from service.decisions import Q_LIST_MATCHES, Q_GET_MATCH


def _mk_match(tx, a, b):
    return tx.execute(
        'INSERT INTO ahavah_match (user_a_id, user_b_id) '
        'VALUES (LEAST(%(a)s,%(b)s), GREATEST(%(a)s,%(b)s)) '
        'RETURNING match_id::text AS mid',
        dict(a=a, b=b)).fetchone()['mid']


def test_blocked_peer_vanishes_from_matches_both_ways(make_person):
    a = make_person(name='Ari', gender='Man')
    b = make_person(name='Bracha', gender='Woman')
    with api_tx() as tx:
        mid = _mk_match(tx, a['id'], b['id'])
        tx.execute('INSERT INTO skipped (subject_person_id, object_person_id, reported) '
                   'VALUES (%(s)s, %(o)s, TRUE)', dict(s=a['id'], o=b['id']))
        for viewer in (a['id'], b['id']):
            rows = tx.execute(Q_LIST_MATCHES, dict(me_id=viewer)).fetchall()
            assert all(r['match_id'] != mid for r in rows), \
                f'blocked match still listed for viewer {viewer}'
            got = tx.execute(Q_GET_MATCH, dict(me_id=viewer, match_id=mid)).fetchone()
            assert got is None, 'blocked match still fetchable'


def test_deactivated_peer_hidden_from_matches(make_person):
    a = make_person(name='Chaim', gender='Man')
    b = make_person(name='Dina', gender='Woman')
    with api_tx() as tx:
        mid = _mk_match(tx, a['id'], b['id'])
        tx.execute('UPDATE person SET activated = FALSE WHERE id = %(p)s', dict(p=b['id']))
        rows = tx.execute(Q_LIST_MATCHES, dict(me_id=a['id'])).fetchall()
        assert all(r['match_id'] != mid for r in rows)
```

- [ ] **Step 2: Run — fails** (both listed today).

- [ ] **Step 3: Fix** — extend both WHERE clauses:

```sql
WHERE
    (m.user_a_id = %(me_id)s OR m.user_b_id = %(me_id)s)
    -- Blocks are permanent on every surface (0035); a reported pair
    -- must not keep exchanging presence via match cards.
    AND NOT EXISTS (
        SELECT 1 FROM skipped s
        WHERE s.reported
          AND ((s.subject_person_id = m.user_a_id AND s.object_person_id = m.user_b_id)
            OR (s.subject_person_id = m.user_b_id AND s.object_person_id = m.user_a_id))
    )
    -- Deactivated/grace-deleted peers disappear immediately, matching
    -- the delete_or_ban_account docstring's promise.
    AND peer.activated
```

(`Q_GET_MATCH` gets the same two conditions appended to its existing WHERE.)

- [ ] **Step 4: Run** new file + full suite — PASS, 179+ green.

- [ ] **Step 5: Commit**

```bash
git add service/decisions/__init__.py tests/test_match_visibility.py
git commit -m "fix(matches): blocked pairs and deactivated peers leave /matches

F4: reporting someone never removed the match card in either direction,
and grace-deleted accounts lingered as cards for 7 days. Both list and
single-match queries now exclude reported pairs and inactive peers.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase P1 — visibility correctness

### Task 5: Matched pairs are exempt from stranger-privacy gates (F5)

**Files:**
- Modify: `service/person/sql/__init__.py:925-953` (`Q_SELECT_PROSPECT_PROFILE`) and `:1334-1349` (`Q_SELECT_CONVERSATION_PROSPECT`)
- Test: append to `tests/test_visibility_rules.py`

- [ ] **Step 1: Failing test**

```python
def test_match_exempts_hidden_member_from_privacy_gates(make_person):
    """F5: a match is PROVEN mutual consent; the hide_me_from_strangers
    and verification-privacy gates must not apply between matched
    members (previously only `messaged` exempted, deadlocking fresh
    matches with hidden members)."""
    from service.person.sql import Q_SELECT_PROSPECT_PROFILE
    me = make_person(name='Ezra', gender='Man')
    hidden = make_person(name='Gila', gender='Woman')
    with api_tx() as tx:
        tx.execute('UPDATE person SET hide_me_from_strangers = TRUE WHERE id = %(p)s',
                   dict(p=hidden['id']))
        tx.execute('INSERT INTO ahavah_match (user_a_id, user_b_id) VALUES '
                   '(LEAST(%(a)s,%(b)s), GREATEST(%(a)s,%(b)s))',
                   dict(a=me['id'], b=hidden['id']))
        row = tx.execute(Q_SELECT_PROSPECT_PROFILE, dict(
            person_id=me['id'], prospect_uuid=hidden['uuid'])).fetchone()
        assert row and row.get('j'), 'matched hidden member must be fetchable'
        assert row['j'].get('name'), 'profile collapsed to the limited stub'
```

- [ ] **Step 2: Run — fails** (limited stub or empty).

- [ ] **Step 3: Fix** — in BOTH queries, wherever the gate currently reads `... OR EXISTS (SELECT 1 FROM messaged WHERE subject_person_id = prospect... )`, add a sibling exemption:

```sql
OR EXISTS (
    SELECT 1 FROM ahavah_match m
    WHERE m.user_a_id = LEAST(%(person_id)s, prospect.id)
      AND m.user_b_id = GREATEST(%(person_id)s, prospect.id)
)
```

Apply to the `hide_me_from_strangers` gate AND the `privacy_verification_level_id` gate in both queries (four sites total; match the local alias for the prospect id in each).

- [ ] **Step 4: Run** `tests/test_visibility_rules.py -q` — PASS.

- [ ] **Step 5: Commit**

```bash
git add service/person/sql/__init__.py tests/test_visibility_rules.py
git commit -m "fix(privacy): a match exempts stranger gates on profile fetches

F5: hide_me_from_strangers and the verification-privacy gate only
exempted messaged-first peers, so a fresh match with a hidden member
deadlocked: card in /matches, chat header 404, limited profile stub,
until the hidden member messaged first. Mutual consent is proven by
the match; both gates now exempt matched pairs.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 6: Deck and map hide verification_required accounts; deck gets the match guard and fail-open gender; rewind clears cache (F6, F7, F8, F9)

Four one-clause fixes in one task (same two files, one test file).

**Files:**
- Modify: `service/search/sql/__init__.py` — deck WHERE (`:128-184`) and `Q_MAP_MARKERS` WHERE (`:429-441`)
- Modify: `service/tokens/actions/rewind.py` (add cache delete)
- Test: `tests/test_deck_gates.py` (create)

- [ ] **Step 1: Failing tests**

```python
"""F6 automodded bots out of deck+map; F7 defensive match guard;
F8 empty gender preference fails open like the map; F9 rewind clears
the pair's search_cache rows."""
from database import api_tx
from service.search.sql import Q_UNCACHED_SEARCH_2, Q_MAP_MARKERS


DECK_PARAMS = dict(  # minimal param set; copy defaults from tests/test_search.py
    searcher_person_id=None, gender_preference=[], n=10, o=0,
)  # extend with whatever Q_UNCACHED_SEARCH_2 requires per test_search.py


def _deck_uuids(tx, searcher_id, gender_pref):
    p = dict(DECK_PARAMS, searcher_person_id=searcher_id,
             gender_preference=gender_pref)
    return {r['prospect_uuid'] for r in tx.execute(Q_UNCACHED_SEARCH_2, p).fetchall()}


def test_verification_required_hidden_from_deck_and_map(make_person):
    me = make_person(name='Viewer', gender='Man')
    bot = make_person(name='Botina', gender='Woman')
    with api_tx() as tx:
        tx.execute('UPDATE person SET verification_required = TRUE WHERE id = %(p)s',
                   dict(p=bot['id']))
        assert bot['uuid'] not in _deck_uuids(tx, me['id'], [2])
        marks = tx.execute(Q_MAP_MARKERS, dict(
            searcher_person_id=me['id'], gender_preference=[])).fetchall()
        assert all(m['uuid'] != bot['uuid'] for m in marks)


def test_matched_pair_never_in_deck_even_without_liked_row(make_person):
    me = make_person(name='Matcher', gender='Man')
    peer = make_person(name='Matched', gender='Woman')
    with api_tx() as tx:
        tx.execute('INSERT INTO ahavah_match (user_a_id, user_b_id) VALUES '
                   '(LEAST(%(a)s,%(b)s), GREATEST(%(a)s,%(b)s))',
                   dict(a=me['id'], b=peer['id']))
        assert peer['uuid'] not in _deck_uuids(tx, me['id'], [2])


def test_empty_gender_preference_fails_open(make_person):
    me = make_person(name='Opener', gender='Man')
    w = make_person(name='Chava', gender='Woman')
    with api_tx() as tx:
        assert w['uuid'] in _deck_uuids(tx, me['id'], []), \
            'empty preference must mean "no filter", as on the map'
```

(Adapt `DECK_PARAMS` from the existing full param dict in `tests/test_search.py` — copy it verbatim, do not invent names.)

- [ ] **Step 2: Run — all three fail.**

- [ ] **Step 3: Fix**

Deck WHERE (`search/sql`): change `:132` and add two clauses near the `liked` exclusion:

```sql
      -- Fail-open like the map: empty preference = no gender filter.
      AND (
          cardinality(%(gender_preference)s::SMALLINT[]) = 0
          OR p.gender_id = ANY(%(gender_preference)s::SMALLINT[])
      )
      -- Automod: a trustworthy report flags verification_required;
      -- flagged accounts leave deck + map until they verify (0018
      -- intended this; the rewritten search lost it).
      AND NOT p.verification_required
      -- Defensive: a match must never re-enter the deck, even if the
      -- searcher's liked half-row was wiped (/decisions/reset).
      AND NOT EXISTS (
          SELECT 1 FROM ahavah_match m
          WHERE m.user_a_id = LEAST(%(searcher_person_id)s, p.id)
            AND m.user_b_id = GREATEST(%(searcher_person_id)s, p.id)
      )
```

`Q_MAP_MARKERS`: add `AND NOT p.verification_required` after `p.activated`.

`rewind.py` `perform()`: after `_Q_DELETE_SWIPE`, execute:

```python
_Q_DELETE_CACHE_PAIR = """
  DELETE FROM search_cache
   WHERE searcher_person_id = %(me_id)s
     AND prospect_person_id = (
       SELECT id FROM person WHERE uuid = uuid_or_null(%(prospect_uuid)s)
     )
"""
```

so the paid-back profile can reappear inside the current cached session (see-passes and take-back-like both already do this).

- [ ] **Step 4: Run** `tests/test_deck_gates.py tests/test_search.py tests/test_token_rewind.py -q` — PASS.

- [ ] **Step 5: Commit**

```bash
git add service/search/sql/__init__.py service/tokens/actions/rewind.py tests/test_deck_gates.py
git commit -m "fix(deck+map): automod gate, match guard, fail-open gender, rewind cache

F6: verification_required accounts (automod-flagged bots) now leave the
deck and map, not just Views and cold-chat. F7: matched pairs carry a
defensive deck exclusion so a wiped liked row cannot resurface a match.
F8: an empty gender preference now fails open exactly like the map
instead of silently emptying the deck. F9: rewind clears the pair's
search_cache rows so the paid-back profile reappears immediately.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase P2 — lifecycle

### Task 7: Entitlements expiry cron (F10)

**Files:**
- Create: `service/cron/entitlements/__init__.py`
- Modify: `service/cron/__init__.py` (import + gather)
- Modify: `.env.production` (poll env)
- Test: `tests/test_expire_stale.py` (create)

**Interfaces:**
- Produces: `entitlements_forever()` coroutine following the exact pattern of `service/cron/autodeactivate2/__init__.py` (poll env `DUO_CRON_ENTITLEMENTS_POLL_SECONDS`, default 3600; dry-run env honored and NOT sending anything in dry-run).

- [ ] **Step 1: Failing test**

```python
from datetime import datetime, timedelta, timezone
from database import api_tx
from service.entitlements import expire_stale


def test_expire_stale_strips_past_expiry_premium(make_person):
    p = make_person(name='Lapsed', gender='Man')
    keep = make_person(name='Current', gender='Woman')
    with api_tx() as tx:
        tx.execute("UPDATE person SET entitlements = ARRAY['premium'], "
                   "subscription_expires_at = NOW() - INTERVAL '1 day' "
                   "WHERE id = %(p)s", dict(p=p['id']))
        tx.execute("UPDATE person SET entitlements = ARRAY['premium'], "
                   "subscription_expires_at = NOW() + INTERVAL '30 days' "
                   "WHERE id = %(p)s", dict(p=keep['id']))
    n = expire_stale(datetime.now(timezone.utc))
    assert n >= 1
    with api_tx() as tx:
        lapsed = tx.execute("SELECT 'premium' = ANY(entitlements) AS h FROM person "
                            "WHERE id = %(p)s", dict(p=p['id'])).fetchone()['h']
        kept = tx.execute("SELECT 'premium' = ANY(entitlements) AS h FROM person "
                          "WHERE id = %(p)s", dict(p=keep['id'])).fetchone()['h']
    assert not lapsed and kept
```

- [ ] **Step 2: Run.** If `expire_stale` already passes this, the test locks it; the cron wrapper is still the missing piece — proceed.

- [ ] **Step 3: Implement the cron task** (mirror autodeactivate2's loop shape):

```python
"""entitlements cron — hourly sweep stripping expired premium.

F10: expire_stale existed with ZERO callers, so subscription_expires_at
was decorative and every grant was premium-forever. This task is the
single enforcement point. NOTE the member-visible consequence: the day
the founding cohort's dates pass (Dec 2026 - Feb 2027), premium
genuinely ends for them; owner comms are tracked outside this repo.
"""
import asyncio
import os
import traceback
from datetime import datetime, timezone

from service.entitlements import expire_stale

_POLL = int(os.environ.get('DUO_CRON_ENTITLEMENTS_POLL_SECONDS', '3600'))


async def entitlements_forever():
    while True:
        try:
            n = expire_stale(datetime.now(timezone.utc))
            if n:
                print(f'entitlements: stripped {n} expired premium grant(s)', flush=True)
        except Exception:
            print(traceback.format_exc(), flush=True)
        await asyncio.sleep(_POLL)
```

Add to `service/cron/__init__.py` imports and the `asyncio.gather(...)` list; add `DUO_CRON_ENTITLEMENTS_POLL_SECONDS=3600` to `.env.production`.

- [ ] **Step 4: Run** new test + full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add service/cron/entitlements/__init__.py service/cron/__init__.py .env.production tests/test_expire_stale.py
git commit -m "feat(entitlements): schedule the expiry sweep (was dead code)

F10: expire_stale had zero callers; every premium grant was effectively
permanent and the payment webhooks' assumed reconciliation backstop did
not exist. Hourly cron now enforces subscription_expires_at.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 8: Hard delete stages CDN cleanup and purges referral rows (F11, F12)

**Files:**
- Modify: `service/cron/pendingdeletion/__init__.py` (the hard-delete tx, `:52-90`)
- Test: `tests/test_pendingdeletion.py` (create)

- [ ] **Step 1: Failing test**

```python
from database import api_tx
from service.cron.pendingdeletion import delete_expired  # match the real fn name at :37


def test_hard_delete_stages_media_and_purges_referrals(make_person):
    p = make_person(name='Leaver', gender='Woman')
    with api_tx() as tx:
        email = tx.execute('SELECT email FROM person WHERE id = %(p)s',
                           dict(p=p['id'])).fetchone()['email']
        tx.execute("INSERT INTO photo (person_id, position, uuid, blurhash) "
                   "VALUES (%(p)s, 1, 'del-test-uuid-1', '')", dict(p=p['id']))
        tx.execute("INSERT INTO referral (inviter_email, invitee_email) VALUES "
                   "(%(e)s, 'ghost-invitee@example.org'), "
                   "('ghost-inviter@example.org', %(e)s)", dict(e=email))
        tx.execute("UPDATE person SET activated = FALSE, "
                   "deletion_requested_at = NOW() - INTERVAL '8 days' "
                   "WHERE id = %(p)s", dict(p=p['id']))
    delete_expired()
    with api_tx() as tx:
        staged = tx.execute("SELECT 1 FROM undeleted_photo WHERE uuid = 'del-test-uuid-1'").fetchone()
        assert staged, 'hard delete must stage photo uuids for the CDN cleaner (F11)'
        ref = tx.execute('SELECT count(*) AS n FROM referral WHERE '
                         'inviter_email = %(e)s OR invitee_email = %(e)s',
                         dict(e=email)).fetchone()['n']
        assert ref == 0, 'referral rows survived hard delete (F12)'
        gone = tx.execute('SELECT 1 FROM person WHERE id = %(p)s', dict(p=p['id'])).fetchone()
        assert gone is None
        tx.execute("DELETE FROM undeleted_photo WHERE uuid = 'del-test-uuid-1'")
```

(Adjust the `photo` INSERT columns to the real NOT NULL set — check `init-api.sql`; add `nsfw_score`/`hash` defaults if required. Match the real exported function name in pendingdeletion.)

- [ ] **Step 2: Run — fails** on staging and referral assertions.

- [ ] **Step 3: Fix** — inside the existing hard-delete tx, BEFORE `DELETE FROM person`:

```python
tx.execute(
    """
    INSERT INTO undeleted_photo (uuid)
    SELECT uuid FROM photo WHERE person_id = ANY(%(ids)s)
    ON CONFLICT DO NOTHING
    """, dict(ids=ids))
tx.execute(
    """
    INSERT INTO undeleted_audio (uuid)
    SELECT uuid FROM audio WHERE person_id = ANY(%(ids)s)
    ON CONFLICT DO NOTHING
    """, dict(ids=ids))
tx.execute(
    """
    -- F12 (0037 regression): referral emails have no FK anymore;
    -- purge both directions so deleted members' addresses do not
    -- persist and UNIQUE(invitee_email) cannot block re-signup.
    DELETE FROM referral
    WHERE inviter_email = ANY(%(emails)s)
       OR invitee_email = ANY(%(emails)s)
    """, dict(emails=emails))
```

(`ids`/`emails` come from the existing reaper SELECT at `:52-57`; check the `audio` table name — if the schema calls it `audio` with `person_id`, keep; else match `Q_DELETE_ACCOUNT`'s staging block at `person/sql:1741-1770` verbatim.)

- [ ] **Step 4: Run** new test + full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add service/cron/pendingdeletion/__init__.py tests/test_pendingdeletion.py
git commit -m "fix(deletion): stage CDN cleanup and purge referral rows on hard delete

F11: self-service hard deletion never fed undeleted_photo/audio, the
only queues the CDN cleaners read, so deleted members' photos stayed
publicly fetchable forever. F12 (0037 regression): referral rows lost
their cascade when the beta_signup FK was dropped; emails now purge
both directions inside the same tx.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 9: Sign-in cancels a pending deletion; the reaper double-checks (F13)

**Files:**
- Modify: `service/person/sql/__init__.py` `Q_MAYBE_SIGN_IN` `existing_person` CTE (`:351-358`)
- Modify: `service/cron/pendingdeletion/__init__.py` reaper SELECT (`:52-57`)
- Test: append to `tests/test_pendingdeletion.py`

- [ ] **Step 1: Failing test**

```python
def test_sign_in_after_delete_cancels_the_purge(make_person):
    """F13: signing back in during the 7-day grace resurrected
    visibility but the reaper still hard-deleted on day 7. Sign-in now
    clears the request, and the reaper independently skips anyone who
    signed in after requesting deletion."""
    p = make_person(name='Regret', gender='Man')
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = FALSE, "
                   "deletion_requested_at = NOW() - INTERVAL '8 days', "
                   "sign_in_time = NOW() "  # signed in AFTER requesting
                   "WHERE id = %(p)s", dict(p=p['id']))
    delete_expired()
    with api_tx() as tx:
        alive = tx.execute('SELECT 1 FROM person WHERE id = %(p)s',
                           dict(p=p['id'])).fetchone()
    assert alive, 'reaper deleted a member who signed in after requesting deletion'
```

- [ ] **Step 2: Run — fails** (row deleted).

- [ ] **Step 3: Fix both sides**

`Q_MAYBE_SIGN_IN` `existing_person` CTE — add one SET line:

```sql
        activated = TRUE,
        deletion_requested_at = NULL,   -- signing in IS regret; a member
                                        -- must never be purged mid-use
        sign_in_count = sign_in_count + 1,
```

Reaper SELECT — belt and braces for rows written before this deploy:

```sql
               AND deletion_requested_at < NOW() - (%(days)s || ' days')::INTERVAL
               AND (sign_in_time IS NULL OR sign_in_time < deletion_requested_at)
```

- [ ] **Step 4: Run** `tests/test_pendingdeletion.py -q` + full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add service/person/sql/__init__.py service/cron/pendingdeletion/__init__.py tests/test_pendingdeletion.py
git commit -m "fix(deletion): signing back in cancels the pending purge

F13: a regretful deleter who signed in during the grace window became
fully visible and chatting, then was hard-deleted mid-conversation on
day 7. OTP sign-in now clears deletion_requested_at, and the reaper
independently skips anyone whose sign_in_time postdates the request.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 10: Admin reactivation survives the cron; REST usage counts as presence (F14, F15)

**Files:**
- Modify: `service/api/admin/users_action_routes.py` (`_Q_REACTIVATE`)
- Modify: the request-session layer — the function that resolves `SessionInfo` per authenticated request (grep `session_token_hash` in `service/api/__init__.py`; add the throttled presence bump where the session row is validated)
- Test: `tests/test_presence_and_reactivation.py` (create)

- [ ] **Step 1: Failing tests**

```python
from database import api_tx
from service.api.admin.users_action_routes import _Q_REACTIVATE


def test_admin_reactivate_bumps_presence_out_of_the_cron_window(make_person):
    p = make_person(name='Sleeper', gender='Woman')
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = FALSE, "
                   "last_online_time = NOW() - INTERVAL '35 days' WHERE id = %(p)s",
                   dict(p=p['id']))
        tx.execute(_Q_REACTIVATE, dict(uuid=p['uuid']))
        row = tx.execute(
            "SELECT activated, last_online_time > NOW() - INTERVAL '1 minute' AS fresh "
            "FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()
    assert row['activated'] and row['fresh'], \
        'reactivation left last_online_time inside the 30-50d window; ' \
        'autodeactivate2 will revert within 5 minutes'


def test_presence_bump_helper_is_throttled(make_person):
    from service.person import bump_presence  # produced by this task
    p = make_person(name='Browser', gender='Man')
    with api_tx() as tx:
        tx.execute("UPDATE person SET last_online_time = NOW() - INTERVAL '20 minutes' "
                   "WHERE id = %(p)s", dict(p=p['id']))
    bump_presence(p['id'])
    with api_tx() as tx:
        t1 = tx.execute('SELECT last_online_time FROM person WHERE id = %(p)s',
                        dict(p=p['id'])).fetchone()['last_online_time']
    bump_presence(p['id'])  # immediate second call: throttled no-op
    with api_tx() as tx:
        t2 = tx.execute('SELECT last_online_time FROM person WHERE id = %(p)s',
                        dict(p=p['id'])).fetchone()['last_online_time']
    assert t1 == t2, 'presence bump must be throttled (one write per 10 min)'
```

- [ ] **Step 2: Run — fails** (`fresh` false; `bump_presence` missing).

- [ ] **Step 3: Implement**

`_Q_REACTIVATE`:

```sql
    UPDATE person SET
        activated = TRUE,
        -- Pull them out of autodeactivate2's 30-50 day window, or the
        -- cron reverts this within one 300s poll (F14).
        last_online_time = NOW()
    WHERE uuid = %(uuid)s::uuid
    RETURNING email
```

`service/person/__init__.py` — add:

```python
def bump_presence(person_id: int) -> None:
    """Throttled REST presence: one UPDATE per 10 minutes per member.
    F15: last_online_time was only written by OTP sign-in and the chat
    socket, so members actively swiping over plain HTTP looked idle and
    autodeactivate2 logged them out at day 30. Called from the session
    check on every authenticated request; the WHERE clause makes the
    hot path a no-op read."""
    if not person_id:
        return
    with api_tx() as tx:
        tx.execute(
            """
            UPDATE person SET last_online_time = NOW()
            WHERE id = %(id)s
              AND last_online_time < NOW() - INTERVAL '10 minutes'
            """, dict(id=person_id))
```

Wire it in the session-validation path right after `SessionInfo` resolves (fire-and-forget; wrap in try/except so presence can never fail a request). Note for F14's club-count drift: reuse the sign-in query's `club_to_increment` shape only if `_Q_REACTIVATE` is also expected to restore clubs — add the same `count_members + 1` CTE guarded on `NOT existing.activated`; if the admin route's tx structure makes that awkward, restore counts in the same UPDATE-CTE chain.

- [ ] **Step 4: Run** new tests + full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add service/api/admin/users_action_routes.py service/person/__init__.py tests/test_presence_and_reactivation.py
git commit -m "fix(presence): REST usage counts; admin reactivation sticks

F15: last_online_time was only written at OTP sign-in and by the chat
socket, so active members with a dead websocket were force-logged-out
and hidden by autodeactivate2 at day 30. Authenticated requests now
bump presence, throttled to one write per 10 minutes. F14: admin
reactivation also sets last_online_time so the cron cannot revert it
five minutes later, and restores club counts.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 11: Onboarding state survives slow onboarders; photo hash updates on replace; selfie latch moves after upload (F16, F17, F18)

**Files:**
- Create: `migrations/0038_onboardee_updated_at.sql`
- Modify: `service/person/sql/__init__.py` (`Q_MAYBE_DELETE_ONBOARDEE` window at `:319`)
- Modify: `service/person/__init__.py:553-645` (field upserts add `updated_at = NOW()`), `:700-715` (photo upsert adds `hash`), `:2495-2515` (selfie ordering)
- Test: `tests/test_onboarding_freshness.py` (create)

- [ ] **Step 1: Migration**

```sql
-- 0038: onboardee activity timestamp (F16).
-- Q_MAYBE_DELETE_ONBOARDEE wiped wizard state older than 1 hour by
-- created_at, but no field write refreshed anything, so a slow
-- onboarder re-verifying an OTP lost name/DOB/photos. Same bug class
-- as the zombie-pass fix: a window whose clock nothing restarts.
ALTER TABLE onboardee ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
```

- [ ] **Step 2: Failing tests**

```python
from database import api_tx


def test_onboardee_wipe_uses_activity_not_creation():
    from service.person.sql import Q_MAYBE_DELETE_ONBOARDEE
    assert 'updated_at' in Q_MAYBE_DELETE_ONBOARDEE, \
        'wipe window must read the activity timestamp (F16)'
    assert "created_at < NOW() - INTERVAL '1 hour'" not in Q_MAYBE_DELETE_ONBOARDEE


def test_onboardee_photo_upsert_updates_hash():
    import inspect
    import service.person as sp
    src = inspect.getsource(sp)
    marker = 'ON CONFLICT (email, position) DO UPDATE SET'
    idx = src.find(marker)
    assert idx > -1
    clause = src[idx:idx + 400]
    assert 'hash = EXCLUDED.hash' in clause, \
        'replacing an onboarding photo must update its stored hash (F17)'
```

Plus a behavioral test for F18: monkeypatch `put_image_in_object_store` to raise, call the selfie-verification entry function, assert no `verification_photo_hash` row was latched (copy the function's real name and signature from `service/person/__init__.py:2495-2515` when writing it).

- [ ] **Step 3: Implement**

- Field upserts (`:553-645`): every `ON CONFLICT (email) DO UPDATE SET <field> = EXCLUDED.<field>` gains `, updated_at = NOW()`.
- `Q_MAYBE_DELETE_ONBOARDEE`: window becomes `updated_at < NOW() - INTERVAL '1 hour'`.
- Photo upsert (`:708`): add `hash = EXCLUDED.hash,` and `updated_at` bump on the onboardee row it belongs to.
- Selfie flow (`:2495-2515`): reorder to (1) read-only reuse check on the hash, (2) `put_image_in_object_store`, (3) only on success latch `Q_INSERT_VERIFICATION_PHOTO_HASH` + insert the verification job, in one tx.

- [ ] **Step 4: Apply migration to the local test DB (psql -f, as with 0037), run** `tests/test_onboarding_freshness.py` + full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/0038_onboardee_updated_at.sql service/person/sql/__init__.py service/person/__init__.py tests/test_onboarding_freshness.py
git commit -m "fix(onboarding): activity keeps wizard state alive; hash + selfie latches corrected

F16: the 1-hour onboardee wipe measured row creation, not activity, so
slow onboarders lost everything on an OTP re-verify; field writes now
stamp updated_at and the wipe reads it. F17: replacing a photo at the
same position kept the FIRST upload's hash, so later photo bans latched
the wrong image. F18: the selfie anti-replay hash latched before the
object-store put; a failed upload burned the capture. Latch now commits
only after a successful upload.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 12: NSFW auto-delete stages CDN cleanup and notifies the admin; report email points somewhere real; beta re-opt-in works (F19, F20, F21)

**Files:**
- Modify: `service/cron/garbagerecords/sql/__init__.py` (q7 region, `:44-58`)
- Modify: `service/cron/garbagerecords/__init__.py` (admin notice after the sweep)
- Modify: `service/beta/__init__.py:15-20` (`_Q_REGISTER`)
- Config: `.env.production` `DUO_REPORT_EMAIL=admin@techbaseltd.com` (repo copy; droplet env updated at deploy)
- Test: `tests/test_garbage_and_beta.py` (create)

- [ ] **Step 1: Failing tests**

```python
from database import api_tx
from service.beta import register


def test_nsfw_delete_stages_undeleted_photo(make_person):
    from service.cron.garbagerecords.sql import Q_GARBAGE  # use the real constant name
    assert 'undeleted_photo' in Q_GARBAGE.split('nsfw_score')[1][:600], \
        'NSFW hard-delete must stage uuids for the CDN cleaner (F19)'


def test_beta_reoptin_clears_unsubscribe():
    with api_tx() as tx:
        tx.execute("INSERT INTO beta_signup (email, unsubscribed_at) "
                   "VALUES ('reopt@example.org', NOW()) "
                   "ON CONFLICT (email) DO UPDATE SET unsubscribed_at = NOW()")
    assert register('reopt@example.org', person_id=None) is True, \
        'explicit re-opt-in must succeed (F21)'
    with api_tx() as tx:
        row = tx.execute("SELECT unsubscribed_at FROM beta_signup "
                         "WHERE email = 'reopt@example.org'").fetchone()
        assert row['unsubscribed_at'] is None
        tx.execute("DELETE FROM beta_signup WHERE email = 'reopt@example.org'")
```

(Match `register`'s real signature and the garbagerecords constant name before running.)

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Implement**

q7 region — stage before deleting, and surface what happened:

```sql
), nsfw_staged AS (
    INSERT INTO undeleted_photo (uuid)
    SELECT uuid FROM photo WHERE nsfw_score > 0.8
    ON CONFLICT DO NOTHING
), q7 AS (
    DELETE FROM photo
    WHERE nsfw_score > 0.8
    RETURNING uuid, person_id
```

In the cron wrapper, when q7 returned rows, send ONE admin email via the existing `emails.waitlist_admin` FROM/TO constants listing `(person name, photo uuid, score)` per row, subject `"NSFW auto-removal: <n> photo(s)"` — so a false positive on a member's photo is at least visible to the operator the hour it happens.

`_Q_REGISTER`:

```sql
  INSERT INTO beta_signup (email, person_id)
  VALUES (%(email)s, %(person_id)s)
  ON CONFLICT (email) DO UPDATE SET
      -- An explicit re-registration IS consent; a sticky unsubscribe
      -- that silently eats the re-opt-in is a trap (F21).
      unsubscribed_at = NULL
  RETURNING email
```

(Note: DO UPDATE makes RETURNING always fire, so `register` now returns True for an existing subscribed row too — check callers at `service/api/beta_routes.py:41-48`: the welcome email send is gated on `register()`'s return; keep it single-send by gating on `unsubscribed_at` having been non-null OR the row being new — return a `(created, resubscribed)` tuple and only email on either flag.)

- [ ] **Step 4: Run** new tests + full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add service/cron/garbagerecords/ service/beta/__init__.py .env.production tests/test_garbage_and_beta.py
git commit -m "fix(lifecycle): NSFW removals visible + staged; report email real; beta re-opt-in works

F19: the nsfw_score > 0.8 hard-delete now stages uuids for the CDN
cleaner (same leak as F11) and emails the admin a per-photo summary so
false positives on a 34-member community are seen the hour they
happen. F20: DUO_REPORT_EMAIL pointed at example.com; abuse reports now
reach a monitored inbox. F21: re-ticking the beta checkbox clears
unsubscribed_at instead of silently no-oping.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 13: Janitorial — remove the dead swipe exclusion (F22)

**Files:**
- Modify: `service/search/sql/__init__.py:163-167` (delete the `swipe` NOT EXISTS block and its misleading comment)
- Modify: `service/tokens/actions/rewind.py` (`_Q_DELETE_SWIPE` and its call — delete; the table is write-never)
- Test: full suite only (deletion of dead code; existing tests cover the deck)

- [ ] **Step 1: Delete the block + the rewind swipe delete.** Grep `FROM swipe|INTO swipe` repo-wide; expected: only DELETE statements remain (reset/see_passes) — remove those too ONLY if nothing else references the table; leave the table itself (dropping is a migration decision, not this plan's).

- [ ] **Step 2: Run full suite — PASS (179+ from earlier tasks).**

- [ ] **Step 3: Commit**

```bash
git add service/search/sql/__init__.py service/tokens/actions/rewind.py
git commit -m "chore(deck): remove the write-never swipe exclusion

F22: nothing has ever inserted into swipe; the deck clause was dead and
its 'any direction' comment described SQL that was one-directional. A
trap for the next reader, gone.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 14: Deck revalidates when the app returns to foreground (ahavah-web)

**Files:**
- Modify: `d:/Antigravity/ahavah-web/src/lib/use-discover-deck.ts` (add the visibility effect)
- Test: `d:/Antigravity/ahavah-web/tests/lib/use-discover-deck.test.ts` (append)

- [ ] **Step 1: Failing vitest**

```ts
it("reloads when the tab becomes visible after the stale threshold", async () => {
  vi.useFakeTimers();
  // arrange: mount hook, let the first page land (existing test helpers),
  // then age the internal lastFetchedAt by advancing timers 11 minutes,
  // fire document visibilitychange with visibilityState 'visible',
  // and assert apiClient.get was called a second time with /search.
});
```

(Flesh out with the file's existing mocking helpers — `mockSearchOnce`, `renderHook` etc.; assert the refetch happens at >10 min and does NOT happen at <10 min.)

- [ ] **Step 2: Implement** — in the hook, track `lastFetchedAt` in a ref (set wherever the seq-guarded fetch completes), and add:

```ts
// Long-lived PWA sessions never remounted the page, so server-side
// changes (pass windows lapsing, new members) only appeared after a
// full relaunch. When the app returns to the foreground after 10+
// minutes, refetch from the head in place.
const STALE_MS = 10 * 60 * 1000;
useEffect(() => {
  const onVisible = () => {
    if (document.visibilityState !== "visible") return;
    if (Date.now() - lastFetchedAt.current < STALE_MS) return;
    void reload();
  };
  document.addEventListener("visibilitychange", onVisible);
  return () => document.removeEventListener("visibilitychange", onVisible);
}, [reload]);
```

- [ ] **Step 3: Run** `npm test` in ahavah-web (full: 458+ green) + `npx tsc --noEmit`.

- [ ] **Step 4: Verify rendered** per house rule: local prod build, real browser, background/foreground the tab past the threshold with devtools network open — observe the head refetch. Screenshot the network panel.

- [ ] **Step 5: Commit (ahavah-web)**

```bash
git add src/lib/use-discover-deck.ts tests/lib/use-discover-deck.test.ts
git commit -m "feat(discover): revalidate the deck on foreground after 10 minutes

Long-lived PWA sessions only refreshed the deck on relaunch, so lapsed
pass windows and new members never appeared mid-session - one half of
the 'inconsistent reset' feel. Returning to the foreground after 10+
minutes now refetches from the head in place.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Deploy + prod verification (after all tasks)

- [ ] Push `ahavah-api` (`git push` → CI applies 0038 + rebuilds; watch `gh run watch --exit-status`), then `ahavah-web`.
- [ ] Verify build sha on the droplet (`docker exec <api> cat /app/.build-sha`).
- [ ] Update droplet runtime env: `DUO_REPORT_EMAIL=admin@techbaseltd.com`, `DUO_CRON_ENTITLEMENTS_POLL_SECONDS=3600` (compose env file + `--force-recreate` per [[ahavah-deploy-stale-container]]).
- [ ] Prod spot-checks (read-only psql): (1) `SELECT count(*) FROM entitlement_event` unchanged by deploy; (2) inbox flags query for a known passed-not-reported pair returns FALSE/FALSE; (3) `SELECT count(*) FROM person WHERE activated AND last_online_time < NOW() - INTERVAL '25 days'` — members the presence fix is about to rescue; recheck in a week (should trend to zero).
- [ ] Log the deploy in the session + memory ([[ahavah-pwa]] pointer to the spec + this plan).

## Explicitly OUT of this plan (owner decisions, tracked in the spec)

1. Ruth outreach before Aug 16 purge (operational, needs owner's words).
2. December premium-cliff comms (extension vs conversion vs warning emails) — must be decided before Task 7's cron makes Dec 16 real. The cron itself ships now; the December story is product, not plumbing.
3. Member-facing notification for NSFW auto-removals (Task 12 gives the admin visibility; member comms is a copy decision).
4. Illustration/AI-photo detection in the moderation pipeline (n=1 today; revisit if it recurs).
