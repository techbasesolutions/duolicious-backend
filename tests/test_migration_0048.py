import uuid

from database import api_tx

def test_receipt_columns_exist_and_are_unique():
    with api_tx() as tx:
        cols = {r['column_name'] for r in tx.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'campaign_click'
        """).fetchall()}
    assert {'receipt', 'platform', 'consumed_at'} <= cols

def test_receipt_is_unique_when_present(make_campaign_link):
    # A fresh receipt per run: api_tx() commits for real against the shared
    # dev database (no test-rollback wrapper here), so a literal value would
    # collide with the row this same test committed on its previous run.
    r = f'r-{uuid.uuid4()}'
    key = make_campaign_link()
    with api_tx() as tx:
        tx.execute("INSERT INTO campaign_click (link_key, receipt) VALUES (%(k)s, %(r)s)", dict(k=key, r=r))
    with api_tx() as tx:
        try:
            tx.execute("INSERT INTO campaign_click (link_key, receipt) VALUES (%(k)s, %(r)s)", dict(k=key, r=r))
            assert False, 'the second insert should have violated the unique index'
        except Exception:
            pass

def test_two_null_receipts_are_allowed(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        tx.execute("INSERT INTO campaign_click (link_key) VALUES (%(k)s)", dict(k=key))
        tx.execute("INSERT INTO campaign_click (link_key) VALUES (%(k)s)", dict(k=key))
