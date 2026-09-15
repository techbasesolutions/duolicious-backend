import pytest
from database import api_tx
from service.spotlight.nonce import issue_nonce, check_nonce, consume_nonce
from service.spotlight.withdrawal import withdraw_member


def test_nonce_lifecycle(make_person):
    p = make_person(name='Nonce')
    with api_tx() as tx:
        n = issue_nonce(tx, p['id'], 'confirm')
        assert check_nonce(tx, n, p['id'], 'confirm') == 'ok'
        assert check_nonce(tx, n, p['id'], 'card') == 'invalid'
        assert check_nonce(tx, n, p['id'] + 1, 'confirm') == 'invalid'
        assert consume_nonce(tx, n) is True and consume_nonce(tx, n) is False
        assert check_nonce(tx, n, p['id'], 'confirm') == 'used'
        withdraw_member(tx, p['id'], 'opt_out')
        assert check_nonce(tx, n, p['id'], 'confirm') == 'stale'
        m = issue_nonce(tx, p['id'], 'confirm')
        assert check_nonce(tx, m, p['id'], 'confirm') == 'ok'
        with pytest.raises(ValueError, match='bad_purpose'):
            issue_nonce(tx, p['id'], 'other')


def test_issue_nonce_requires_an_existing_person():
    with api_tx() as tx:
        with pytest.raises(ValueError, match='not_found'):
            issue_nonce(tx, -1, 'confirm')
