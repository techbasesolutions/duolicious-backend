"""Stripe Identity stops dropping retries (wave "verification tells the
member the truth", task 5).

`identity.verification_session.requires_input` is Stripe's "the member has
to try again": a blurry document, a selfie that did not match, an abandoned
capture. The webhook acknowledged it with no database write, no notification
and no operator record, and `/verify/gold` has no status poll, so the member
was told nothing at all and sat on a screen that never resolved.

These tests pin that every non-verified outcome is persisted against the
member named by `metadata.user_id`, that `requires_input` is the one that
notifies, that the copy never repeats a reason out of `last_error`, that the
webhook still answers 2xx for everything Stripe can send it, and that the
`verified` path is exactly what it was.
"""
from __future__ import annotations

import pytest

import service.identity_verification as iv
import service.notifications as notifications
from database import api_tx


SECRET = 'whsec_test'

# A real shape of Stripe's requires_input error. The code is for operators.
# The reason is Stripe's own wording, and repeating either one at a member
# would assert something we cannot evidence.
LAST_ERROR = {
    'code': 'document_unverified_other',
    'reason': 'The document could not be verified.',
}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

@pytest.fixture
def wired(monkeypatch):
    """Bypass lazy init so the real signature check runs against the test
    secret, and record every notification instead of sending one."""
    import stripe
    monkeypatch.setattr(iv, '_stripe_module', stripe)
    monkeypatch.setattr(iv, '_stripe_init_attempted', True)
    monkeypatch.setattr(iv, '_webhook_secret', SECRET)

    calls: list[dict] = []

    def record(person_id, event_kind, **kw):
        calls.append(dict(person_id=person_id, event_kind=event_kind, **kw))

    monkeypatch.setattr(notifications, 'notify', record)

    # Nothing here may reach the push helper directly: notify() owns the
    # push-or-email decision. A direct push lands in the same list so the
    # "exactly one notification" assertions catch it.
    def direct_push(*_a, **kw):
        calls.append(dict(event_kind='direct push, bypassing notify', **kw))

    monkeypatch.setattr(notifications, 'send_to_user_safe', direct_push)
    return calls


def _post(client, stripe_signed_event, **kwargs):
    payload = stripe_signed_event(secret=SECRET, **kwargs)
    return client.post(
        '/webhooks/stripe-identity',
        data=payload.body,
        headers={'Stripe-Signature': payload.sig,
                 'Content-Type': 'application/json'},
    )


def _identity_row(person_id: int) -> dict:
    with api_tx() as tx:
        return tx.execute(
            """
            SELECT ahavah_verification_tier::text  AS tier,
                   id_verified_country             AS country,
                   id_verification_status          AS status,
                   id_verification_status_at       AS status_at,
                   id_verification_session_id      AS session_id,
                   id_verification_error_code      AS error_code
              FROM person
             WHERE id = %(id)s
            """,
            dict(id=person_id),
        ).fetchone()


def _set_tier(person_id: int, tier: str) -> None:
    with api_tx() as tx:
        tx.execute(
            """
            UPDATE person
               SET ahavah_verification_tier = %(tier)s::ahavah_verification_tier
             WHERE id = %(id)s
            """,
            dict(id=person_id, tier=tier),
        )


# ---------------------------------------------------------------------------
# requires_input: the one the member can act on
# ---------------------------------------------------------------------------

def test_requires_input_persists_the_outcome(
    client, wired, make_person, stripe_signed_event,
):
    person = make_person()

    r = _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={'user_id': str(person['id'])},
        session_id='vs_requires_input_1',
        last_error=LAST_ERROR,
    )

    assert r.status_code == 200
    row = _identity_row(person['id'])
    assert row['status'] == 'requires_input'
    assert row['status_at'] is not None
    assert row['session_id'] == 'vs_requires_input_1'
    # The tier is untouched: a check that needs another try grants nothing
    # and takes nothing away.
    assert row['tier'] == 'none'


def test_requires_input_keeps_the_error_code_for_operators(
    client, wired, make_person, stripe_signed_event,
):
    """Stripe's code is the only handle an operator has on why a check
    stalled, so it is stored. It is never shown to the member."""
    person = make_person()

    _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={'user_id': str(person['id'])},
        last_error=LAST_ERROR,
    )

    assert _identity_row(person['id'])['error_code'] == 'document_unverified_other'


def test_requires_input_notifies_the_member_exactly_once(
    client, wired, make_person, stripe_signed_event,
):
    person = make_person()

    _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={'user_id': str(person['id'])},
        last_error=LAST_ERROR,
    )

    assert len(wired) == 1
    call = wired[0]
    assert call['person_id'] == person['id']
    assert call['event_kind'] == 'verification'
    assert call['url'] == '/verify/gold'
    assert call['email_html_factory'] is not None


def test_requires_input_says_the_check_needs_another_try(
    client, wired, make_person, stripe_signed_event,
):
    """It reports the outcome and offers the one action the member has.
    It asserts nothing about the document or about the member."""
    person = make_person()

    _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={'user_id': str(person['id'])},
        last_error=LAST_ERROR,
    )

    call = wired[0]
    assert call['title'] == 'Your ID check needs another try'
    assert call['body'] == 'You can start the check again when you are ready.'
    assert call['email_subject'] == 'Your ID check needs another try'


def test_requires_input_copy_names_no_reason_from_last_error(
    client, wired, make_person, stripe_signed_event,
):
    """`last_error` is an operator record, not an accusation to repeat. The
    push, the subject and the rendered email are all checked, because the
    email is where a stray reason would most plausibly be pasted in."""
    person = make_person()

    _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={'user_id': str(person['id'])},
        last_error=LAST_ERROR,
    )

    call = wired[0]
    # Deliberately not an .invalid address: the word would collide with the
    # accusation list below.
    html = call['email_html_factory']('https://unsub.test/u')
    everywhere = (
        f"{call['title']} {call['body']} {call['email_subject']} {html}"
    ).lower()

    assert LAST_ERROR['code'] not in everywhere
    assert 'could not be verified' not in everywhere
    for accusation in ('fake', 'fraud', 'reject', 'denied', 'violat',
                       'suspicious', 'invalid', 'someone else', 'failed'):
        assert accusation not in everywhere, f'{accusation!r} accuses the member'


def test_requires_input_email_carries_no_hand_rolled_headline(
    client, wired, make_person, stripe_signed_event,
):
    """No brand title image says this, so the email ships with no headline
    rather than inventing display type."""
    person = make_person()

    _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={'user_id': str(person['id'])},
    )

    html = wired[0]['email_html_factory']('https://example.invalid/unsub')
    assert '<h1' not in html.lower()
    # title_image() emits exactly these two img tags. The shell always
    # carries their dark-mode css, so the img tag is the real signal.
    assert '<img class="e-title-' not in html
    assert 'Start the check again' in html
    assert 'example.invalid/unsub' in html


# ---------------------------------------------------------------------------
# canceled: written, not notified
# ---------------------------------------------------------------------------

def test_canceled_persists_the_outcome_and_says_nothing(
    client, wired, make_person, stripe_signed_event,
):
    """The member cancelled. They know. It is still recorded, because an
    operator asking why a member has no tier deserves an answer."""
    person = make_person()

    r = _post(
        client, stripe_signed_event,
        type='identity.verification_session.canceled',
        metadata={'user_id': str(person['id'])},
        session_id='vs_canceled_1',
    )

    assert r.status_code == 200
    row = _identity_row(person['id'])
    assert row['status'] == 'canceled'
    assert row['session_id'] == 'vs_canceled_1'
    assert wired == []


@pytest.mark.parametrize('event_type, expected', [
    ('identity.verification_session.created', 'created'),
    ('identity.verification_session.processing', 'processing'),
])
def test_the_in_flight_states_are_recorded_and_silent(
    client, wired, make_person, stripe_signed_event, event_type, expected,
):
    """A member who starts a new check must not be left carrying last
    week's requires_input, so the in-flight states overwrite it. Neither is
    news to the member, so neither notifies."""
    person = make_person()

    r = _post(
        client, stripe_signed_event,
        type=event_type,
        metadata={'user_id': str(person['id'])},
    )

    assert r.status_code == 200
    assert _identity_row(person['id'])['status'] == expected
    assert wired == []


# ---------------------------------------------------------------------------
# The webhook contract: 2xx for everything Stripe can send
# ---------------------------------------------------------------------------

def test_an_unknown_event_type_is_still_acknowledged(
    client, wired, make_person, stripe_signed_event,
):
    """Anything other than 2xx and Stripe retries the same event for days."""
    person = make_person()

    r = _post(
        client, stripe_signed_event,
        type='identity.verification_session.redacted',
        metadata={'user_id': str(person['id'])},
    )

    assert r.status_code == 200
    assert _identity_row(person['id'])['status'] is None
    assert wired == []


def test_a_raising_notifier_still_returns_2xx_and_keeps_the_write(
    client, monkeypatch, wired, make_person, stripe_signed_event,
):
    """A notification is a best effort. It can never cost the member the
    record of what happened, and it can never make Stripe retry."""
    person = make_person()

    def explode(*_a, **_kw):
        raise RuntimeError('the notifier is down')

    monkeypatch.setattr(notifications, 'notify', explode)

    r = _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={'user_id': str(person['id'])},
        last_error=LAST_ERROR,
    )

    assert r.status_code == 200
    assert _identity_row(person['id'])['status'] == 'requires_input'


def test_an_event_without_a_user_id_is_acknowledged_and_writes_nothing(
    client, wired, stripe_signed_event,
):
    r = _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={},
    )

    assert r.status_code == 200
    assert wired == []


def test_an_event_for_a_person_who_does_not_exist_is_acknowledged(
    client, wired, stripe_signed_event,
):
    r = _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={'user_id': '2147483600'},
    )

    assert r.status_code == 200
    assert wired == []


def test_an_invalid_signature_is_still_rejected(
    client, wired, make_person, stripe_signed_event,
):
    """The 2xx contract is about events Stripe sent us. An unsigned payload
    is not one of those, and the 400 stays."""
    person = make_person()
    payload = stripe_signed_event(
        type='identity.verification_session.requires_input',
        metadata={'user_id': str(person['id'])},
        secret='whsec_wrong',
    )
    r = client.post('/webhooks/stripe-identity', data=payload.body,
                    headers={'Stripe-Signature': payload.sig})

    assert r.status_code == 400
    assert _identity_row(person['id'])['status'] is None


# ---------------------------------------------------------------------------
# Never contradict a tier the member already earned
# ---------------------------------------------------------------------------

def test_a_late_requires_input_never_contradicts_a_gold_member(
    client, wired, make_person, stripe_signed_event,
):
    """Stripe redelivers, and events can arrive out of order. Telling a
    verified member to try again is the exact class of lie this wave
    removes, so gold is terminal for this latch."""
    person = make_person()
    _set_tier(person['id'], 'gold')

    r = _post(
        client, stripe_signed_event,
        type='identity.verification_session.requires_input',
        metadata={'user_id': str(person['id'])},
        last_error=LAST_ERROR,
    )

    assert r.status_code == 200
    row = _identity_row(person['id'])
    assert row['tier'] == 'gold'
    assert row['status'] is None
    assert wired == []


# ---------------------------------------------------------------------------
# The verified path is exactly what it was
# ---------------------------------------------------------------------------

def test_the_verified_path_still_promotes_and_records_the_country(
    client, monkeypatch, wired, make_person, stripe_signed_event,
):
    person = make_person()

    class _Report:
        @staticmethod
        def retrieve(report_id):
            return {'id': report_id,
                    'document': {'issuing_country': 'BB', 'status': 'verified'}}

    import stripe
    monkeypatch.setattr(stripe.identity, 'VerificationReport', _Report)

    r = _post(
        client, stripe_signed_event,
        type='identity.verification_session.verified',
        metadata={'user_id': str(person['id'])},
        last_verification_report='vr_test_task5',
    )

    assert r.status_code == 200
    assert r.get_json() == {'received': True, 'promoted': True}

    row = _identity_row(person['id'])
    assert row['tier'] == 'gold'
    assert row['country'] == 'BB'
    # The new latch describes attempts that granted nothing. A success is
    # recorded by the tier, which is what the rest of the product reads.
    assert row['status'] is None
    assert row['session_id'] is None

    assert len(wired) == 1
    assert wired[0]['title'] == "You're verified"


def test_the_verified_path_still_notifies_with_the_tier_email(
    client, monkeypatch, wired, make_person, stripe_signed_event,
):
    person = make_person()

    class _Report:
        @staticmethod
        def retrieve(report_id):
            raise RuntimeError('stripe is down')

    import stripe
    monkeypatch.setattr(stripe.identity, 'VerificationReport', _Report)

    _post(
        client, stripe_signed_event,
        type='identity.verification_session.verified',
        metadata={'user_id': str(person['id'])},
        last_verification_report='vr_test_task5_b',
    )

    call = wired[0]
    assert call['email_subject'] == "You're verified on Ahavah"
    assert call['url'] == '/verify'
    html = call['email_html_factory']('https://example.invalid/unsub')
    assert 'title-verified' in html


# ---------------------------------------------------------------------------
# The recorder itself
# ---------------------------------------------------------------------------

def test_record_identity_outcome_overwrites_the_previous_attempt(make_person):
    person = make_person()

    assert iv.record_identity_outcome(
        person['id'], 'requires_input',
        session_id='vs_a', error_code='document_unverified_other') is True
    assert iv.record_identity_outcome(
        person['id'], 'canceled', session_id='vs_b') is True

    row = _identity_row(person['id'])
    assert row['status'] == 'canceled'
    assert row['session_id'] == 'vs_b'
    # A stale code from the previous attempt would send an operator after
    # the wrong thing.
    assert row['error_code'] is None


def test_record_identity_outcome_reports_when_it_wrote_nothing(make_person):
    person = make_person()
    _set_tier(person['id'], 'gold')

    assert iv.record_identity_outcome(person['id'], 'canceled') is False
    assert iv.record_identity_outcome(2147483600, 'canceled') is False
