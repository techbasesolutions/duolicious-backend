import uuid
from database import api_tx

def _cols(tx, t):
    return {r['column_name'] for r in tx.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %(t)s", dict(t=t)).fetchall()}

def test_0044_schema():
    with api_tx('read committed') as tx:
        assert {'request_key','revision','caption','photo_uuid','layout_version','channels','participants','asset_hash','image_key','image_url','created_by','created_at'} <= _cols(tx, 'spotlight_revision')
        assert {'revision_id','person_id','role','approved_at','nonce'} <= _cols(tx, 'spotlight_revision_consent')
        assert {'kind','person_id','request_key','created_at'} <= _cols(tx, 'spotlight_occurrence')
        assert {'nonce','person_id','purpose','epoch','issued_at','used_at'} <= _cols(tx, 'spotlight_token_nonce')
        assert 'spotlight_consent_epoch' in _cols(tx, 'person')
        assert {'current_revision_id','lease_token','cancellation_requested_at','delivery_state','post_url'} <= _cols(tx, 'publishing_queue')
        assert {'person_id','request_key'} <= _cols(tx, 'spotlight_removal_task')
        seeds = {r['key']: r['value'] for r in tx.execute("SELECT key, value FROM spotlight_setting").fetchall()}
        for k, v in (('approvals_enabled','false'), ('roundup_tiles_enabled','false'), ('invites_enabled','true'), ('publication_enabled','false'), ('external_access_enabled','true')):
            assert seeds.get(k) == v, k

def test_0044_claim_sets_lease_token_and_skips_withdrawn():
    rk_ok = uuid.uuid4().hex; rk_wd = uuid.uuid4().hex
    with api_tx() as tx:
        tx.execute("""INSERT INTO publishing_queue (request_key, kind, platform, status, scheduled_for)
                      VALUES (%(a)s, 'roundup', 'facebook', 'scheduled', NOW() - interval '1 minute'),
                             (%(b)s, 'roundup', 'facebook', 'scheduled', NOW() - interval '1 minute')""", dict(a=rk_ok, b=rk_wd))
        tx.execute("UPDATE publishing_queue SET cancellation_requested_at = NOW() WHERE request_key = %(b)s", dict(b=rk_wd))
        rows = tx.execute("SELECT * FROM claim_spotlight_posts(50)").fetchall()
        keys = {r['request_key'] for r in rows}
        assert rk_ok in keys and rk_wd not in keys
        mine = [r for r in rows if r['request_key'] == rk_ok][0]
        assert mine['delivery_state'] == 'attempting'
        assert isinstance(mine['lease_token'], str) and len(mine['lease_token']) == 32 and all(c in '0123456789abcdef' for c in mine['lease_token'])

def test_0044_consent_pk_and_occurrence_unique(make_person):
    p = make_person(name='Rev')
    rk = uuid.uuid4().hex
    import psycopg, pytest
    with api_tx() as tx:
        rev = tx.execute("INSERT INTO spotlight_revision (request_key, revision, caption, channels) VALUES (%(rk)s, 1, 'c', ARRAY['facebook']) RETURNING id", dict(rk=rk)).fetchone()['id']
        tx.execute("INSERT INTO spotlight_revision_consent (revision_id, person_id, role) VALUES (%(r)s, %(p)s, 'subject')", dict(r=rev, p=p['id']))
        tx.execute("INSERT INTO spotlight_occurrence (kind, person_id, request_key) VALUES ('welcome', %(p)s, %(rk)s)", dict(p=p['id'], rk=rk))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with api_tx() as tx:
            tx.execute("INSERT INTO spotlight_occurrence (kind, person_id, request_key) VALUES ('welcome', %(p)s, %(rk)s)", dict(p=p['id'], rk=rk))
