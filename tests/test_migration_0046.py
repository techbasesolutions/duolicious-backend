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
