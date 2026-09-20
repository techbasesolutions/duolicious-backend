"""E6, new faces for people who never finished onboarding (2026-09-19).

The claim under test: an unfinished signup is told how many of the people
they would be looking for have joined since, and is never told who.
"""
from database import api_tx
from emails.new_faces_signup import new_faces_signup_html, SUBJECT
import emails.send_new_faces_signup as e6


def _sendable_email(person_id: int, tag: str) -> str:
    """make_person hands out an @example.com address, which emails.base
    suppresses, so a person expected IN a cohort needs an unsuppressed one."""
    email = f'{tag}-{person_id}@ahavah-test.invalid'
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s, normalized_email = %(e)s WHERE id = %(i)s",
                   dict(e=email, i=person_id))
    return email


def test_html_counts_the_opposite_gender_and_names_nobody():
    html = new_faces_signup_html('Ehud', 5, 'women', 13, 'https://ahavah.app/s/k',
                                 'https://ahavah.app/u/x')
    assert '5 women have joined' in html
    assert '13 countries' in html
    assert 'title-finish-profile.png' in html
    assert '—' not in html and '—' not in SUBJECT


def test_html_reads_naturally_for_one_and_for_none():
    one = new_faces_signup_html('Ehud', 1, 'men', 4, 'https://a/s/k', 'https://a/u/x')
    assert '1 man has joined' in one
    none = new_faces_signup_html(None, 0, 'women', 4, 'https://a/s/k', 'https://a/u/x')
    assert 'has joined' not in none and 'have joined' not in none
    assert '4 countries' in none
    assert none.strip().startswith('<!') or '<html' in none


def test_reader_name_is_escaped():
    html = new_faces_signup_html('Ri"vka <script>', 2, 'men', 4, 'https://a/s/k', 'https://a/u/x')
    assert '<script>' not in html
    assert 'Ri&quot;vka &lt;script&gt;' in html


def test_recipients_are_unactivated_and_carry_an_opposite_gender_count(make_person):
    stalled = make_person(name='Stalled', gender='Man')
    email = _sendable_email(stalled['id'], 'e6')
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = FALSE, sign_up_time = NOW() - interval '40 days'"
                   " WHERE id = %(i)s", dict(i=stalled['id']))
    joiner = make_person(name='Joiner', gender='Woman')
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = TRUE, sign_up_time = NOW() - interval '2 days'"
                   " WHERE id = %(i)s", dict(i=joiner['id']))

    rows = [r for r in e6.recipients() if r['email'] == email]
    assert len(rows) == 1
    row = rows[0]
    assert row['gender_label'] == 'women'
    assert row['joined_count'] >= 1
    subject, html = e6.build_for(row)
    assert subject == SUBJECT
    assert 'Joiner' not in html


def test_an_activated_member_is_never_a_recipient(make_person):
    member = make_person(name='Active', gender='Man')
    email = _sendable_email(member['id'], 'e6-active')
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = TRUE WHERE id = %(i)s", dict(i=member['id']))
    assert all(r['email'] != email for r in e6.recipients())
