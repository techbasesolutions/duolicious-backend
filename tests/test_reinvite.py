from database import api_tx
from emails.reinvite import reinvite_html, SUBJECT
from emails.send_reinvite import recipient_count, recipients
from service.unsubscribe import stamp_unsubscribed

def test_reinvite_html_counts_the_people_the_member_seeks_and_names_nobody():
    """Owner decision 2026-09-19: counts, not names. Nobody consented to being
    named in a campaign email; Spotlight consent covers its own card only."""
    html = reinvite_html('Ehud', [dict(first_name='Rivka', country='GB'), dict(first_name='Sarah', country='US')], 4,
                         'https://ahavah.app/s/k', 'https://ahavah.app/u/x', gender_label='women')
    assert 'title-reinvite.png' in html
    assert '4 women have joined' in html
    assert 'Rivka' not in html and 'Sarah' not in html
    assert '—' not in html and '—' not in SUBJECT


def test_reinvite_html_reads_naturally_for_one_person_and_without_a_label():
    one = reinvite_html('Ehud', [], 1, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x', gender_label='men')
    assert '1 man has joined' in one
    plain = reinvite_html('Ehud', [], 3, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert '3 new members have joined' in plain

def _sendable_email(person_id: int) -> str:
    """make_person hands out an @example.com address, which is on
    emails.base's default suppressed-domain list -- and recipients() now
    excludes suppressed addresses (final review item 3), so a person this
    test expects to see IN the cohort needs a reserved-but-unsuppressed
    domain instead."""
    email = f'reinvite-{person_id}@ahavah-test.invalid'
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s, normalized_email = %(e)s WHERE id = %(i)s",
                  dict(e=email, i=person_id))
    return email


def test_recipients_require_a_newcomer(make_person):
    stale = make_person(name='Stale', gender='Man')
    other = make_person(name='Other', gender='Woman')
    _sendable_email(stale['id'])
    with api_tx() as tx:
        tx.execute("INSERT INTO liked (liker_id, liked_id, created_at) VALUES (%(a)s, %(b)s, NOW() - interval '31 days')", dict(a=stale['id'], b=other['id']))
        tx.execute("INSERT INTO search_preference_gender (person_id, gender_id) SELECT %(p)s, id FROM gender WHERE name = 'Woman' ON CONFLICT DO NOTHING", dict(p=stale['id']))
    rows = recipients()
    ids = {r['person_id'] for r in rows}
    assert stale['id'] in ids            # `other` joined after the stale like (fixture sign_up_time is NOW())
    row = next(r for r in rows if r['person_id'] == stale['id'])
    assert row['total_new'] == len(row['newcomers'])


# ---------------------------------------------------------------------------
# C2: the greeting name and every newcomer name/country come from member
# profiles, so they must be escaped before they reach the HTML.
# ---------------------------------------------------------------------------

def test_greeting_name_with_markup_is_escaped():
    html = reinvite_html('<a href="x">Ehud</a>', [dict(first_name='Rivka', country='GB')], 1,
                         'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert '<a href="x">Ehud</a>' not in html
    assert '&lt;a href=&quot;x&quot;&gt;Ehud&lt;/a&gt;' in html


def test_member_supplied_text_is_escaped():
    """The reader's own first name is the only member-supplied value left in
    this template now that newcomers are counted rather than named."""
    html = reinvite_html('Ri"vka <script>', [], 1, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x',
                         gender_label='men')
    assert '<script>' not in html
    assert 'Ri&quot;vka &lt;script&gt;' in html


# ---------------------------------------------------------------------------
# Final review, item 3: E3 is a `notifications`-scope campaign, so a member
# who unsubscribed from notifications must drop out of both recipients()
# and recipient_count(), the same way run_campaign() would skip them
# per-row at send time.
# ---------------------------------------------------------------------------

def test_notifications_unsubscribe_drops_a_dormant_member_from_recipients(make_person):
    stale = make_person(name='StaleUnsub', gender='Man')
    other = make_person(name='OtherUnsub', gender='Woman')
    stale_email = _sendable_email(stale['id'])
    with api_tx() as tx:
        tx.execute("INSERT INTO liked (liker_id, liked_id, created_at) VALUES (%(a)s, %(b)s, NOW() - interval '31 days')",
                  dict(a=stale['id'], b=other['id']))
        tx.execute("INSERT INTO search_preference_gender (person_id, gender_id) SELECT %(p)s, id FROM gender WHERE name = 'Woman' ON CONFLICT DO NOTHING",
                  dict(p=stale['id']))

    before_ids = {r['person_id'] for r in recipients()}
    assert stale['id'] in before_ids
    before_count = recipient_count()

    with api_tx() as tx:
        assert stamp_unsubscribed(tx, 'notifications', stale_email)

    after_ids = {r['person_id'] for r in recipients()}
    assert stale['id'] not in after_ids
    assert recipient_count() == before_count - 1
    assert recipient_count() == len(recipients())
