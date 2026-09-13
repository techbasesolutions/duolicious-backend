from database import api_tx
from emails.reinvite import reinvite_html, SUBJECT
from emails.send_reinvite import recipients

def test_reinvite_html_lists_names_and_count():
    html = reinvite_html('Ehud', [dict(first_name='Rivka', country='GB'), dict(first_name='Sarah', country='US')], 4,
                         'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert 'title-reinvite.png' in html and 'Rivka' in html and 'Sarah' in html and '4 new members' in html
    assert '—' not in html and '—' not in SUBJECT

def test_recipients_require_a_newcomer(make_person):
    stale = make_person(name='Stale', gender='Man')
    other = make_person(name='Other', gender='Woman')
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


def test_newcomer_name_and_country_are_escaped():
    html = reinvite_html('Ehud', [dict(first_name='Ri"vka <script>', country='G<b>B</b>')], 1,
                         'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert '<script>' not in html
    assert 'Ri&quot;vka &lt;script&gt;' in html
    assert 'G&lt;b&gt;B&lt;/b&gt;' in html
