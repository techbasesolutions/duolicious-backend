from database import api_tx
from emails.reinvite import reinvite_html, SUBJECT
from emails.send_reinvite import build_for, recipient_count, recipients
from service.unsubscribe import stamp_unsubscribed

def test_reinvite_html_counts_the_people_the_member_seeks_and_names_nobody():
    """Owner decision 2026-09-19: counts, not names. Nobody consented to being
    named in a campaign email; Spotlight consent covers its own card only."""
    html = reinvite_html('Ehud', 4, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x',
                         gender_label='women')
    assert '4 women have joined Ahavah since you were last online' in html
    # The old Ultra title image reads "New faces since you were away", which
    # would promise faces this email does not show (owner, 2026-09-19).
    assert 'title-reinvite.png' not in html
    assert 'faces' not in html.lower()
    assert '—' not in html and '—' not in SUBJECT


def test_reinvite_html_reads_naturally_for_one_person_and_without_a_label():
    one = reinvite_html('Ehud', 1, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x', gender_label='men')
    assert '1 man has joined' in one
    plain = reinvite_html('Ehud', 3, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert '3 new members have joined' in plain


def test_a_paused_member_is_told_their_profile_is_paused_and_how_to_restore_it():
    html = reinvite_html('Ehud', 10, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x',
                         gender_label='men', state='paused')
    assert 'your profile is paused' in html.lower()
    assert 'Bring my profile back' in html
    assert 'Signing in is all it takes' in html
    assert '10 men have joined' in html
    assert 'faces' not in html.lower()

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
    assert row['total_new'] >= 1 and row['state'] == 'quiet'


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
    html = reinvite_html('Ri"vka <script>', 1, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x',
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


# ---------------------------------------------------------------------------
# Owner decision 2026-09-19: "new faces should go to all members". A member
# who acted yesterday still hears about who joined since; only the resend cap,
# the unsubscribe scope and "has somebody new to show" hold them back.
# ---------------------------------------------------------------------------

def test_a_paused_member_is_a_recipient_with_the_paused_state(make_person):
    """A member the dormancy cron deactivated is in this cohort, and is told
    their profile is paused rather than being treated as merely quiet."""
    paused = make_person(name='PausedMember', gender='Man')
    other = make_person(name='PausedJoiner', gender='Woman')
    email = _sendable_email(paused['id'])
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = FALSE,"
                   " last_online_time = NOW() - interval '45 days' WHERE id = %(i)s",
                   dict(i=paused['id']))
        tx.execute("INSERT INTO search_preference_gender (person_id, gender_id)"
                   " SELECT %(p)s, id FROM gender WHERE name = 'Woman' ON CONFLICT DO NOTHING",
                   dict(p=paused['id']))
        tx.execute("UPDATE person SET sign_up_time = NOW() - interval '2 days' WHERE id = %(i)s",
                   dict(i=other['id']))
    rows = [r for r in recipients() if r['email'] == email]
    assert len(rows) == 1 and rows[0]['state'] == 'paused'
    subject, html = build_for(rows[0])
    assert 'paused' in subject.lower()
    assert 'PausedJoiner' not in html


def test_a_recently_active_member_is_not_a_recipient(make_person):
    active = make_person(name='ActiveSeeker', gender='Man')
    other = make_person(name='FreshJoiner', gender='Woman')
    email = _sendable_email(active['id'])
    with api_tx() as tx:
        tx.execute("INSERT INTO liked (liker_id, liked_id, created_at) VALUES (%(a)s, %(b)s, NOW() - interval '1 hour')",
                   dict(a=active['id'], b=other['id']))
        tx.execute("INSERT INTO search_preference_gender (person_id, gender_id)"
                   " SELECT %(p)s, id FROM gender WHERE name = 'Woman' ON CONFLICT DO NOTHING",
                   dict(p=active['id']))
        tx.execute("UPDATE person SET sign_up_time = NOW() - interval '1 minute' WHERE id = %(i)s",
                   dict(i=other['id']))
    assert all(r['email'] != email for r in recipients()),         'a member who acted an hour ago is not in the quiet cohort'


def test_a_member_who_never_acted_is_quiet_and_counts_from_last_online(make_person):
    never = make_person(name='NeverActed', gender='Woman')
    joiner = make_person(name='ManJoiner', gender='Man')
    email = _sendable_email(never['id'])
    with api_tx() as tx:
        tx.execute("UPDATE person SET sign_up_time = NOW() - interval '20 days',"
                   " last_online_time = NOW() - interval '20 days' WHERE id = %(i)s",
                   dict(i=never['id']))
        tx.execute("INSERT INTO search_preference_gender (person_id, gender_id)"
                   " SELECT %(p)s, id FROM gender WHERE name = 'Man' ON CONFLICT DO NOTHING",
                   dict(p=never['id']))
        tx.execute("UPDATE person SET sign_up_time = NOW() - interval '2 days' WHERE id = %(i)s",
                   dict(i=joiner['id']))
    rows = [r for r in recipients() if r['email'] == email]
    assert len(rows) == 1
    assert rows[0]['gender_label'] == 'men'
    assert rows[0]['total_new'] >= 1, 'counted from last_online_time, 20 days back'
