import uuid
from database import api_tx

def _cols(tx, t):
    return {r['column_name'] for r in tx.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %(t)s", dict(t=t)).fetchall()}

def test_0041_schema():
    with api_tx('read committed') as tx:
        assert {'request_key','kind','subject_person_id','platform','caption','image_url','image_key','scheduled_for','status','lease_until','attempts','external_post_id','error','member_approved_at','approved_photo_uuid'} <= _cols(tx, 'publishing_queue')
        assert {'queue_id','platform','external_post_id','reason','done_at'} <= _cols(tx, 'spotlight_removal_task')
        assert {'key','value'} <= _cols(tx, 'spotlight_setting')
        assert 'signup_person_id' in _cols(tx, 'campaign_click')
        assert tx.execute("SELECT value FROM spotlight_setting WHERE key = 'scheduler_enabled'").fetchone()['value'] == 'false'

def test_0041_claim_skips_future_and_locks():
    rk = uuid.uuid4().hex
    with api_tx() as tx:
        tx.execute("INSERT INTO publishing_queue (request_key, kind, platform, status, scheduled_for) VALUES (%(rk)s, 'roundup', 'facebook', 'scheduled', NOW() - interval '1 minute'), (%(rk)s, 'roundup', 'instagram', 'scheduled', NOW() + interval '1 hour')", dict(rk=rk))
        rows = tx.execute("SELECT * FROM claim_spotlight_posts(5)").fetchall()
        mine = [r for r in rows if r['request_key'] == rk]
        assert len(mine) == 1 and mine[0]['platform'] == 'facebook' and mine[0]['status'] == 'processing' and mine[0]['attempts'] == 1
        again = tx.execute("SELECT * FROM claim_spotlight_posts(5)").fetchall()
        assert not [r for r in again if r['request_key'] == rk]
