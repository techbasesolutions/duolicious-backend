"""Public feedback capture (2026-05-26) - service.feedback tests."""

from __future__ import annotations

import pytest


def test_insert_returns_id_and_count_today_increments():
    from database import api_tx
    from service.feedback import insert, count_today
    with api_tx() as tx:
        before = count_today(tx)
        fid = insert(
            tx,
            category="idea",
            message="great app",
            email=None,
            path="/feedback",
            user_agent="pytest",
        )
        assert isinstance(fid, int)
        assert count_today(tx) == before + 1
        # cleanup
        tx.execute("DELETE FROM feedback WHERE id = %(id)s", dict(id=fid))


def test_postfeedback_strips_message_and_normalizes_email():
    import duotypes as t
    m = t.PostFeedback(category="idea", message="  hi  ", email="")
    assert m.message == "hi"
    assert m.email is None
    assert not m.website  # honeypot defaults empty


def test_postfeedback_rejects_bad_category_and_empty_message():
    import duotypes as t
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        t.PostFeedback(category="spam", message="x")
    with pytest.raises(ValidationError):
        t.PostFeedback(category="idea", message="   ")
