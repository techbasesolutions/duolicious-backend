"""F2/F3: a webhook failure AFTER the replay latch used to eat the paid
effect forever (provider retry hits the replay path). Contract under
test: if effect application raises, the event id must NOT be latched."""
import pytest
from database import api_tx
from service import entitlements


def test_record_event_tx_latches_inside_caller_tx(make_person):
    p = make_person(name='Payer', gender='Man')
    class Boom(Exception):
        pass
    with pytest.raises(Boom):
        with api_tx() as tx:
            assert entitlements.record_event_tx(
                tx, event_id='evt_test_rollback', event_type='t',
                app_user_id=str(p['id']), payload={}) is True
            raise Boom()
    # The latch must have rolled back with the failed effects.
    with api_tx() as tx:
        n = tx.execute(
            "SELECT count(*) AS n FROM entitlement_event "
            "WHERE event_id = 'evt_test_rollback'").fetchone()['n']
    assert n == 0, 'latch survived a rolled-back effect tx'


def test_record_event_tx_replay_returns_false(make_person):
    p = make_person(name='Payer2', gender='Man')
    with api_tx() as tx:
        assert entitlements.record_event_tx(
            tx, event_id='evt_test_replay', event_type='t',
            app_user_id=str(p['id']), payload={}) is True
    with api_tx() as tx:
        assert entitlements.record_event_tx(
            tx, event_id='evt_test_replay', event_type='t',
            app_user_id=str(p['id']), payload={}) is False
        tx.execute("DELETE FROM entitlement_event WHERE event_id = 'evt_test_replay'")
