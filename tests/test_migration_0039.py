# tests/test_migration_0039.py
from database import api_tx

def _cols(tx, table):
    return {r['column_name'] for r in tx.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %(t)s",
        dict(t=table)).fetchall()}

def test_0039_person_columns_and_tables_exist():
    with api_tx('read committed') as tx:
        pc = _cols(tx, 'person')
        assert {'spotlight_opt_in', 'spotlight_opt_in_at',
                'spotlight_last_featured_at', 'reinvite_sent_at',
                'spotlight_ref'} <= pc
        assert {'person_id', 'campaign', 'campaign_id', 'message_id', 'sent_at'} <= _cols(tx, 'email_send_log')
        assert {'key', 'kind', 'target_url', 'subject_person_id', 'created_at'} <= _cols(tx, 'campaign_link')
        assert {'link_key', 'clicked_at', 'ua_class'} <= _cols(tx, 'campaign_click')

def test_0039_send_log_unique_per_campaign_run(make_person):
    import psycopg, pytest
    p = make_person(name='Log')
    with api_tx() as tx:
        tx.execute("INSERT INTO email_send_log (person_id, campaign, campaign_id, message_id) VALUES (%(id)s, 'e1', 'run-1', 'm1')", dict(id=p['id']))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with api_tx() as tx:
            tx.execute("INSERT INTO email_send_log (person_id, campaign, campaign_id, message_id) VALUES (%(id)s, 'e1', 'run-1', 'm2')", dict(id=p['id']))
