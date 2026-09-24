-- migrations/0053_verification_job_lease.sql
--
-- Wave "verification tells the member the truth", task 4. A verification job
-- that dies mid run is retried, and is visible.
--
-- The runner set a row to 'running' before calling the classifier and the
-- picker only ever selected 'queued'. A worker that died between the two left
-- the row in 'running' with nothing to retry it, nothing to surface it, and
-- garbagerecords deleting it three days later. The member sat on a polling
-- screen that never resolved.
--
--   running_since          when the current run started. This is the lease:
--                          a run older than it is treated as dead and the row
--                          becomes claimable again. Cleared when the job
--                          finishes, so a finished row never looks overdue.
--
--   reap_count             how many times this row has been re-queued. Past
--                          the ceiling it stops being claimed and starts being
--                          counted, so a job that keeps dying goes visibly
--                          stuck instead of burning a classifier call a lease.
--
--   verification_level_id  the outcome of THIS attempt. Until now the only
--                          place an outcome survived was person.verification_
--                          level_id, which is a per-person latch written by
--                          whichever attempt finished LAST, while
--                          /check-verification reports the NEWEST attempt. The
--                          two can describe different runs, and a member being
--                          told about the wrong run is the class of lie this
--                          whole wave removes. The person latch is untouched:
--                          it is what the rest of the product reads.
--
-- The backfill is the part that matters. A row already sitting in 'running'
-- when this applies has no start time, and treating an unknown age as stale
-- would let the reaper double-process whatever the old code still had in
-- flight during the deploy. Stamping those rows with NOW() gives each of them
-- exactly one full lease before it can be reaped, and the claim query refuses
-- to reap a NULL lease at all.
--
-- Additive and idempotent. Historical rows carry no level, which reads as
-- "unknown" and falls back to the person latch.
ALTER TABLE verification_job
  ADD COLUMN IF NOT EXISTS running_since         TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS reap_count            INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS verification_level_id SMALLINT
      REFERENCES verification_level(id) ON DELETE SET NULL ON UPDATE CASCADE;

UPDATE verification_job
   SET running_since = NOW()
 WHERE status = 'running'
   AND running_since IS NULL;

-- The reaper scans for overdue leases every tick, and the admin System tab
-- counts them on every load. Partial, because every other status has no lease.
CREATE INDEX IF NOT EXISTS idx__verification_job__running_since
    ON verification_job (running_since)
 WHERE status = 'running';
