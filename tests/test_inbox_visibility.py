from database import api_tx
from service.person.sql import Q_INBOX_INFO


def _inbox_row(tx, me, peer_uuid):
    rows = tx.execute(Q_INBOX_INFO, dict(
        person_id=me, prospect_person_uuids=[peer_uuid])).fetchall()
    assert rows, 'inbox query returned nothing for the fixture pair'
    return rows[0]


def test_plain_pass_does_not_touch_inbox(make_person):
    me = make_person(name='Inbal', gender='Woman')
    peer = make_person(name='Yona', gender='Man')
    with api_tx() as tx:
        peer_uuid = tx.execute('SELECT uuid::text AS u FROM person WHERE id=%(p)s',
                               dict(p=peer['id'])).fetchone()['u']
        # Establish messaging relationship so they appear in inbox
        tx.execute('INSERT INTO messaged (subject_person_id, object_person_id) '
                   'VALUES (%(s)s, %(o)s)',
                   dict(s=me['id'], o=peer['id']))
        # Insert bidirectional plain passes (reported=FALSE)
        for subject, obj in ((me['id'], peer['id']), (peer['id'], me['id'])):
            tx.execute('INSERT INTO skipped (subject_person_id, object_person_id, reported) '
                       'VALUES (%(s)s, %(o)s, FALSE) ON CONFLICT (subject_person_id, object_person_id) '
                       'DO UPDATE SET reported = EXCLUDED.reported',
                       dict(s=subject, o=obj))
        row = _inbox_row(tx, me['id'], peer_uuid)
        # Plain passes should NOT gate inbox: conversation_location != 'archive' and name is visible
        assert row['conversation_location'] != 'archive', 'plain pass should not archive conversation'
        assert row['name'] is not None, 'plain pass should not blank peer name'


def test_report_still_gates_inbox(make_person):
    me = make_person(name='Tikva', gender='Woman')
    peer = make_person(name='Zev', gender='Man')
    with api_tx() as tx:
        peer_uuid = tx.execute('SELECT uuid::text AS u FROM person WHERE id=%(p)s',
                               dict(p=peer['id'])).fetchone()['u']
        # Establish bidirectional messaging relationship
        tx.execute('INSERT INTO messaged (subject_person_id, object_person_id) '
                   'VALUES (%(s)s, %(o)s)',
                   dict(s=me['id'], o=peer['id']))
        tx.execute('INSERT INTO messaged (subject_person_id, object_person_id) '
                   'VALUES (%(s)s, %(o)s)',
                   dict(s=peer['id'], o=me['id']))
        # Insert reported block (reported=TRUE)
        tx.execute('INSERT INTO skipped (subject_person_id, object_person_id, reported) '
                   'VALUES (%(s)s, %(o)s, TRUE)', dict(s=me['id'], o=peer['id']))
        row = _inbox_row(tx, me['id'], peer_uuid)
        # Reported blocks should still gate inbox: conversation_location = 'archive'
        assert row['conversation_location'] == 'archive', 'reported block must archive conversation'
