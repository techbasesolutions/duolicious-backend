-- A photo id is text, not an RFC uuid.
--
-- `photo.uuid` and `onboardee_photo.uuid` are `text`, and every real photo id
-- is a 64-character hex string: `service.person.upload_photo` mints them with
-- `secrets.token_hex(32)`, for example
-- 73fb77150e62cbf2ac3a5aa8b63317fa8567db18ee78c1991cc9b8f2300746f2. Only the
-- test fixtures ever used `gen_random_uuid()`, which is why the type mismatch
-- below never showed up in the suite.
--
-- `spotlight_revision.photo_uuid` was declared `uuid` (migration 0044) and
-- `publishing_queue.approved_photo_uuid` was declared `uuid` (migration 0041).
-- `create_revision` inserted the chosen photo id through `%(photo)s::uuid`, so
-- in production every welcome and member-of-week card for a real member raised
--
--   psycopg.errors.InvalidTextRepresentation:
--   invalid input syntax for type uuid: "73fb7715..."
--
-- and POST /admin/growth/spotlight/welcome answered 500. Both columns are
-- moved to `text` so they hold the id the photo table actually stores. The
-- `::uuid` cast in `create_revision` goes with this migration.
--
-- `approved_photo_uuid` is the pre-Wave-1 column nothing writes or reads any
-- more (consent lives on `spotlight_revision_consent` since Wave 1 Task 2). It
-- is converted alongside rather than dropped: dropping a column is not
-- reversible by re-running a forward-only file, and a stale row still holding
-- an old value stays readable.
--
-- Nothing else depends on either column: no index, no constraint, no view, no
-- rule and no function references them (checked against the live schema via
-- pg_index, pg_constraint, pg_rewrite and pg_proc). `claim_spotlight_posts`
-- returns SETOF publishing_queue, but its body is a quoted string rather than
-- a tracked SQL-standard body and it never names either column, so the type
-- change does not touch it; it simply returns the new row type.
--
-- Forward only and idempotent-safe: `uuid -> text` casts every existing value
-- to its canonical hyphenated text form, and re-running the file on an already
-- converted column is a `text -> text` no-op.
ALTER TABLE spotlight_revision
  ALTER COLUMN photo_uuid TYPE text USING photo_uuid::text;

ALTER TABLE publishing_queue
  ALTER COLUMN approved_photo_uuid TYPE text USING approved_photo_uuid::text;
