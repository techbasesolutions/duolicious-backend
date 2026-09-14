from database import api_tx


def test_0042_payload_column():
    with api_tx('read committed') as tx:
        cols = {r['column_name'] for r in tx.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'publishing_queue'").fetchall()}
    assert 'payload' in cols
