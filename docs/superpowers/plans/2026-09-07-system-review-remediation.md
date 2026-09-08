# Ahavah system-review remediation — implementation plan

Source: `C:\Users\Ehud\Documents\2026-09-07-ahavah-system-review-and-improvement-handoff.md`
(external review, findings F01-F10 + product proposals P01-P05, work packages W0-W8).

Reviewed revisions match current trees: `ahavah-web f2fa94d` (the CARTO->Esri
map fix) and `ahavah-api 4333d9a` (the /referrals/me endpoint). So the review
reflects today's code, not a stale snapshot.

**Governing constraints (do not violate):**
- Plan-before-code is inviolable; this doc is that plan. No edits until the
  owner authorizes a package.
- Ahavah deploys by pushing to `main` (CI). Therefore work is implemented and
  verified LOCALLY, and commits are HELD per package until the owner approves
  that package. One approval = one deploy.
- No dependency installs, infra changes, or production-data mutation without
  explicit authorization (the review withholds this too).
- No em dashes in any user-facing string; sentence case.

## Finding triage against current code (operator-verified where noted)

| ID | Severity | Current-code status | Package |
| --- | --- | --- | --- |
| F03 commit-error propagation | P0 | **CONFIRMED** in `database/__init__.py:107-121`: commit failure on a successful body hits `except:` with the body's (None) exc-tuple and never re-raises. Silent false-success. | W1 |
| F01 chat cache account isolation | P0 | Code-confirmed by review (`chat-cache.ts`, `use-chat-thread.ts`, `use-profile.ts` sign-out). Needs re-read at head before edit. | W2 |
| F02 map privacy vs server state | P0 | Code-confirmed (`settings/privacy/page.tsx` fire-and-forget `updateProfile`; `use-show-on-map.ts` separate unscoped localStorage). | W3 |
| F06 admin/consumer sign-out cleanup | P1 (admin portion immediate) | Code-confirmed across `ahavah-admin` shell / api-client / unauthorized page. | W2 (admin) + W7 (cookie migration) |
| F04 match/quota concurrency | P1 | Partial: `Q_RECORD_LIKE` repair already shipped (Aug 11). Remaining: cross-worker sync for simultaneous first likes + daily quota. 4 gunicorn workers. | W4 |
| F05 migration stop-on-error | P0 before next deploy | Code-confirmed in `deploy-ahavah.yml` (psql `-f | tail -3`, no `ON_ERROR_STOP`). | W5 |
| F07 ad telemetry boundary | P1 | Code-confirmed (`layout.tsx` Meta load unconditional; `metapixel.py` sends UUID-bearing event id). Needs product policy decision. | W6 |
| F08 dev/prod isolation + reproducible verify | P1 | Code-confirmed (`next.config.ts` rewrites to prod `/api`; unpinned `requirements.txt`). | W0 + W5 |
| F09 auth/onboarding routing + honest save | P1 | Observed (tokenless `/discover` -> `/onboarding/name`). | W3 |
| F10 read-time entitlement enforcement | P1/P2 | Code-confirmed (`has_entitlement` ignores expiry; hourly worker shipped Aug 11 but is reconciliation, not read-time gate). Needs expiry-semantics decision. | W6 |

Already-resolved / do-not-reopen (review credits these): map coordinate gating,
match list/detail block+activation checks, Stripe transaction-local event
recording, pending-deletion staging, entitlement expiry worker existence, the
Aug 11 mechanism remediation. Do not re-run the Aug 11 audit as if open.

## Execution order (adopt the review's W0-W8)

W0 (baseline+isolation) -> W1 (F03) -> W2 (F01 + admin F06) -> W3 (F02+F09)
-> W4 (F04) -> W5 (F05+F08 deps) -> W6 (F07+F10, needs decisions) -> W7 (cookie/WS)
-> W8 (P01-P05 product/UX). Each package = small reviewable change, local
verification, then a held commit awaiting approval.

## Decisions needed from the owner before their packages (not before W0/W1)

1. Map default: participation-on vs explicit opt-in? (W3 save-correctness does
   not depend on this; only onboarding copy does.)
2. Private-message cache retention limit on shared devices? (W2 isolation
   proceeds regardless.)
3. Which acquisition telemetry is permitted, on which public routes, member
   controls? (W6. Meanwhile restrict private-route ad script.)
4. Premium expiry: exact timestamp or documented grace period? Same for
   promo grants vs paid subs. (W6/F10.)
5. Should automatic match repair notify, and include inactive/blocked pairs?
   (W4 reconciliation command.)
6. Which verification claims are shown publicly; which admin roles need
   financial vs moderation powers? (W8/admin.)

---

## First executable increment: W0 + F03 (TDD)

W0 confirms the local harness can run API tests in isolation (it can: this
session runs them via `docker-compose.test.yml`, never touching prod), and
records the current green baseline. F03 is self-contained, is the stated W1
gate, and every later concurrency/payment guarantee builds on it.

### W0 - baseline (no code change)

- [ ] Run the full API suite via the test compose stack; record pass count.
      `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q`
      Expected: current green baseline (226+ at last run).
- [ ] Confirm the test stack targets the disposable test DB, never prod
      (`conftest.py` DB DSN points at the compose postgres, not the droplet).
- [ ] Record web baseline: `pnpm test` (477) + `tsc --noEmit`.

### F03 - propagate commit failures

**File:** `ahavah-api/database/__init__.py` (`__exit__`, lines ~107-121).
**Test:** `ahavah-api/tests/test_tx_commit_propagation.py` (create).

- [ ] **Step 1 - failing test.** Fault-inject a commit failure after a
      successful body and assert the caller sees the failure (not success),
      and that the connection is reusable next transaction.

```python
"""F03: a commit failure on a SUCCESSFUL body must propagate, not be
swallowed. Before the fix, __exit__ caught the commit error, logged the
body's (None) exc-tuple, and returned normally, so callers ran success
handling on uncommitted data."""
import psycopg
import pytest
from unittest.mock import patch
from database import api_tx


def test_commit_failure_propagates_and_conn_recovers():
    class CommitBoom(psycopg.OperationalError):
        pass

    # Body succeeds; commit() raises. The context manager must let the
    # error escape, not swallow it into a false success.
    with patch("database._api_conn") as conn:
        conn.commit.side_effect = CommitBoom("commit failed")
        with pytest.raises(psycopg.OperationalError):
            with api_tx() as tx:
                pass  # body succeeds; exc_type is None at __exit__

    # A subsequent real transaction still works (lock released, cursor
    # closed, connection usable). Exercised against the test DB.
    with api_tx() as tx:
        assert tx.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
```

(Adjust the patch target / connection global to the real name in
`database/__init__.py`; `_api_conn` and `_api_conn_lock` are the current
globals. Confirm `api_tx` is the public entry the wrapper exposes.)

- [ ] **Step 2 - run, expect fail** (today it swallows -> no raise -> test
      fails at `pytest.raises`).

- [ ] **Step 3 - fix `__exit__`.** Re-raise the commit exception; keep body
      exceptions intact if rollback also fails; guarantee cursor close and
      lock release in `finally`.

```python
def __exit__(self, exc_type, exc_val, exc_tb):
    commit_error = None
    try:
        if exc_type is None:
            _api_conn.commit()
        else:
            _api_conn.rollback()
    except BaseException as e:
        # A commit/rollback failure is real. Log it with ITS OWN
        # traceback (not the body's exc-tuple, which is None on a
        # successful body), and remember it to re-raise below. A body
        # exception still takes precedence: if the body already failed,
        # let that propagate and only log the rollback failure.
        print(traceback.format_exc())
        if exc_type is None:
            commit_error = e
    finally:
        try:
            self.cur.close()
        except Exception:
            print(traceback.format_exc())
        _api_conn_lock.release()

    if commit_error is not None:
        raise commit_error
```

(Note: the lock release moves INTO `finally` so an exception path cannot leak
the process-wide `_api_conn_lock`. Verify the current code releases it exactly
once on every path after this change.)

- [ ] **Step 4 - run** the new test + full suite; expect green (baseline+1).

- [ ] **Step 5 - review the async wrapper** in the same file for parity
      (the review says do not assume it shares the bug; confirm by reading).
      If it has the same swallow, fix identically with its own test; if not,
      note why in the report.

- [ ] **Step 6 - HOLD commit.** Do not push. Report to owner with the diff,
      test evidence, and the async-wrapper finding, and request approval to
      commit (which deploys). Commit message when approved:

```
fix(db): propagate commit failures instead of swallowing them (F03)

The api_tx __exit__ caught commit/rollback errors and, on a successful
body, logged the body's (None) exc-tuple and returned normally, so
callers ran success handling on data the database never committed. Now
the commit exception is re-raised (with its own traceback), body
exceptions still take precedence, and cursor-close plus lock-release are
guaranteed in finally. Shared dependency of matching, profile writes,
moderation, and payments.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

**Acceptance (from the review):** injected commit failure -> caller receives
failure, no success notification, next transaction executes; separate
rollback-failure and connection-close cases covered; for a payment event,
replay converges to exactly one paid effect even when the first commit result
is uncertain (the payment-replay assertion belongs with W1's integration
tests, layered on the webhook replay-safety suite that already exists).

## What I will NOT do without explicit authorization

Commit, push, deploy, install dependencies, run migrations, change infra,
mint sessions against prod, or mutate any production data. W0 baseline runs
are read-only against the disposable test stack.
