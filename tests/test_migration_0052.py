"""Migration 0052: the spotlight photo id columns are text, not uuid.

A real photo id is a 64-character hex string (`secrets.token_hex(32)`), and
`photo.uuid` is a text column. `spotlight_revision.photo_uuid` (migration
0044) and `publishing_queue.approved_photo_uuid` (migration 0041) were
declared `uuid`, so `create_revision` could not store a production photo id
at all: it raised InvalidTextRepresentation and the welcome route answered
500.

The re-apply check runs the file against temporary tables named
`spotlight_revision` and `publishing_queue` that already hold a
production-shaped row: a temp table shadows the real one for its own
session, so the migration's SQL runs unmodified without touching the shared
tables.
"""
from pathlib import Path

import psycopg

import database
from database import api_tx

_MIGRATION = Path(__file__).resolve().parent.parent / 'migrations' / '0052_spotlight_photo_id_text.sql'

_HEX_ID = '73fb77150e62cbf2ac3a5aa8b63317fa8567db18ee78c1991cc9b8f2300746f2'


def test_0052_photo_id_columns_are_text():
    with api_tx('read committed') as tx:
        cols = {(r['table_name'], r['column_name']): r for r in tx.execute(
            """SELECT table_name, column_name, data_type, is_nullable
                 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND ((table_name = 'spotlight_revision' AND column_name = 'photo_uuid')
                    OR (table_name = 'publishing_queue' AND column_name = 'approved_photo_uuid'))"""
        ).fetchall()}
    assert set(cols) == {('spotlight_revision', 'photo_uuid'),
                         ('publishing_queue', 'approved_photo_uuid')}
    assert cols[('spotlight_revision', 'photo_uuid')]['data_type'] == 'text', \
        'migration 0052 has not been applied'
    assert cols[('spotlight_revision', 'photo_uuid')]['is_nullable'] == 'YES'
    assert cols[('publishing_queue', 'approved_photo_uuid')]['data_type'] == 'text', \
        'migration 0052 has not been applied'
    assert cols[('publishing_queue', 'approved_photo_uuid')]['is_nullable'] == 'YES'


def test_0052_stores_a_sixty_four_character_hex_photo_id():
    """The point of the change: the column holds the id the photo table
    actually stores, byte for byte, with no cast anywhere in the way."""
    with api_tx() as tx:
        row = tx.execute(
            """INSERT INTO spotlight_revision
                   (request_key, revision, caption, photo_uuid, channels)
               VALUES ('mig-0052-check', 1, 'c', %(u)s, ARRAY['facebook'])
               RETURNING photo_uuid""", dict(u=_HEX_ID)).fetchone()
        tx.execute("DELETE FROM spotlight_revision WHERE request_key = 'mig-0052-check'")
    assert row['photo_uuid'] == _HEX_ID


def test_0052_applies_twice_over_existing_rows():
    with psycopg.connect(database._api_conninfo, autocommit=True) as conn:
        try:
            conn.execute("""CREATE TEMP TABLE spotlight_revision (
                                request_key text NOT NULL, caption text NOT NULL,
                                photo_uuid uuid)""")
            conn.execute("""CREATE TEMP TABLE publishing_queue (
                                request_key text NOT NULL, platform text NOT NULL,
                                approved_photo_uuid uuid)""")
            conn.execute("""INSERT INTO spotlight_revision VALUES
                                ('rk-existing', 'c', '11111111-2222-3333-4444-555555555555')""")
            conn.execute("""INSERT INTO publishing_queue VALUES
                                ('rk-existing', 'facebook', '66666666-7777-8888-9999-aaaaaaaaaaaa')""")
            conn.execute(_MIGRATION.read_text())
            conn.execute(_MIGRATION.read_text())
            # The second run is a text to text no-op, and a production-shaped
            # id goes in where it could not before.
            conn.execute("""INSERT INTO spotlight_revision VALUES ('rk-hex', 'c', %(u)s)""",
                         dict(u=_HEX_ID))
            kept = conn.execute(
                "SELECT photo_uuid FROM pg_temp.spotlight_revision ORDER BY request_key").fetchall()
            queue = conn.execute(
                "SELECT approved_photo_uuid FROM pg_temp.publishing_queue").fetchone()
            types = dict(conn.execute(
                """SELECT table_name || '.' || column_name, data_type
                     FROM information_schema.columns
                    WHERE starts_with(table_schema, 'pg_temp')
                      AND column_name IN ('photo_uuid', 'approved_photo_uuid')""").fetchall())
        finally:
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.execute('ROLLBACK')
            conn.execute('DROP TABLE IF EXISTS pg_temp.spotlight_revision')
            conn.execute('DROP TABLE IF EXISTS pg_temp.publishing_queue')
    assert kept == [('11111111-2222-3333-4444-555555555555',), (_HEX_ID,)]
    assert queue == ('66666666-7777-8888-9999-aaaaaaaaaaaa',)
    assert types == {'spotlight_revision.photo_uuid': 'text',
                     'publishing_queue.approved_photo_uuid': 'text'}
