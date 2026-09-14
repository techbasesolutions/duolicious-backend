from database import api_tx
from emails.community_weekly import community_weekly_html, SUBJECT
from emails.send_community_weekly import _week_context

def test_weekly_without_spotlight_lists_newcomers():
    html = community_weekly_html([dict(first_name='Rivka', country='GB'), dict(first_name='Dan', country='US')], 27, None,
                                 'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert 'title-community.png' in html and 'Rivka' in html and 'Dan' in html and '27 members' in html
    assert 'Member of the week' not in html
    assert '—' not in html and '—' not in SUBJECT

def test_weekly_with_spotlight_block():
    html = community_weekly_html([], 27, dict(first_name='Sarah', age=29, country='US', image_url='https://x/y.png', post_url='https://fb/p'), 'https://a', 'https://u')
    assert 'Member of the week' in html and 'Sarah, 29' in html and 'https://x/y.png' in html


# ---------------------------------------------------------------------------
# C2: member-supplied text reaches the email HTML. Names, countries and the
# spotlight card's fields are member-controlled, so every one of them must be
# escaped before interpolation -- including inside the img alt="..." attribute,
# where a bare double quote would break out of the attribute.
# ---------------------------------------------------------------------------

def test_newcomer_name_with_markup_is_escaped():
    html = community_weekly_html([dict(first_name='<a href="x">Rivka</a>', country='<b>GB</b>')],
                                 27, None, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert '<a href="x">Rivka</a>' not in html
    assert '&lt;a href=&quot;x&quot;&gt;Rivka&lt;/a&gt;' in html
    assert '&lt;b&gt;GB&lt;/b&gt;' in html


def test_spotlight_fields_are_escaped_including_the_alt_attribute():
    html = community_weekly_html(
        [], 27,
        dict(first_name='Sa"rah <script>', age='29"', country='US" onload="x',
             image_url='https://x/y.png', post_url='https://fb/p'),
        'https://a', 'https://u')
    assert 'Sa"rah' not in html
    assert '<script>' not in html
    assert 'Sa&quot;rah &lt;script&gt;' in html
    assert 'onload="x' not in html
    assert 'US&quot; onload=&quot;x' in html
    assert '29&quot;' in html


def test_spotlight_rejects_a_non_https_image_or_post_url():
    """_spotlight_block/_esc_https still validate strictly -- the field-level
    check under test here is unchanged. See the tests below for what
    community_weekly_html() itself does with that ValueError."""
    from emails.community_weekly import _spotlight_block
    import pytest
    base = dict(first_name='Sarah', age=29, country='US',
                image_url='https://x/y.png', post_url='https://fb/p')
    with pytest.raises(ValueError):
        _spotlight_block(dict(base, image_url='javascript:alert(1)'))
    with pytest.raises(ValueError):
        _spotlight_block(dict(base, post_url='http://fb/p'))


# ---------------------------------------------------------------------------
# Final review, item 5: _esc_https raising used to propagate straight out of
# community_weekly_html(), and this function is called once per recipient
# inside the campaign runner's send loop (service/campaigns/runner.py) -- an
# uncaught exception there aborts the run at that recipient, so one bad
# curated spotlight URL must not stop the whole weekly send. The validation
# itself must still hold (see test above); only the failure mode changes:
# fall back to the newcomers-only variant and log a warning.
# ---------------------------------------------------------------------------

def test_invalid_spotlight_url_falls_back_to_newcomers_only_instead_of_raising(capsys):
    bad_spotlight = dict(first_name='Sarah', age=29, country='US',
                         image_url='javascript:alert(1)', post_url='https://fb/p')
    html = community_weekly_html([dict(first_name='Rivka', country='GB')], 27, bad_spotlight,
                                 'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert 'Member of the week' not in html
    assert 'Sarah' not in html
    assert 'Rivka' in html and '27 members' in html

    out = capsys.readouterr().out
    assert 'spotlight' in out.lower()


def test_invalid_spotlight_post_url_also_falls_back(capsys):
    bad_spotlight = dict(first_name='Sarah', age=29, country='US',
                         image_url='https://x/y.png', post_url='http://fb/p')
    html = community_weekly_html([], 27, bad_spotlight, 'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert 'Member of the week' not in html
    assert '27 members' in html
    assert 'spotlight' in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# Task 11: _week_context() fills `spotlight` from the most recent published
# member_of_week facebook row within the last 7 days. The test db persists
# between runs, so the success case stamps updated_at strictly ahead of
# real time (NOW() + 1 hour) to guarantee it outranks any leftover row from
# an earlier run of this same test without depending on run order.
# ---------------------------------------------------------------------------

def test_week_context_spotlight_from_published_member_of_week(make_person):
    p = make_person(name='Sarah Cohen')
    with api_tx() as tx:
        # spotlight_opt_in is part of the query now (C2): a published row only
        # keeps appearing for as long as the member still consents.
        tx.execute("""UPDATE person SET date_of_birth = '1995-01-01', country = 'US',
                             spotlight_opt_in = TRUE
                       WHERE id = %(id)s""",
                   dict(id=p['id']))
        tx.execute(
            """INSERT INTO publishing_queue
                   (request_key, kind, subject_person_id, platform, caption, status,
                    image_url, external_post_id, updated_at)
               VALUES (%(rk)s, 'member_of_week', %(pid)s, 'facebook', 'c', 'published',
                       'https://cdn/x.png', '9', NOW() + interval '1 hour')""",
            dict(rk=f'wk-fresh-{p["id"]}', pid=p['id']))
    spotlight = _week_context()['spotlight']
    assert spotlight is not None
    assert spotlight['first_name'] == 'Sarah'
    assert spotlight['country'] == 'US'
    assert spotlight['image_url'] == 'https://cdn/x.png'
    assert spotlight['post_url'] == 'https://www.facebook.com/9'


def test_week_context_spotlight_none_when_row_older_than_7_days(make_person):
    p = make_person(name='StaleSpot')
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO publishing_queue
                   (request_key, kind, subject_person_id, platform, caption, status,
                    image_url, external_post_id, updated_at)
               VALUES (%(rk)s, 'member_of_week', %(pid)s, 'facebook', 'c', 'published',
                       'https://cdn/stale.png', '1', NOW() - interval '8 days')""",
            dict(rk=f'wk-stale-{p["id"]}', pid=p['id']))
    spotlight = _week_context()['spotlight']
    # Not necessarily None outright (a fresher row from the test above may
    # still be within its own 7-day window), but this member's stale row
    # must never be the one surfaced.
    assert spotlight is None or spotlight['post_url'] != 'https://www.facebook.com/1'


# ---------------------------------------------------------------------------
# C2: a publishing_queue row stays 'published' forever, so the weekly block
# has to re-check consent and removal on every run. Each case below stamps
# updated_at ahead of real time so its own row would otherwise win the
# ORDER BY, which is what makes the absence meaningful.
# ---------------------------------------------------------------------------

def _publish_member_of_week(tx, person_id: int, request_key: str, **columns) -> None:
    tx.execute("DELETE FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=request_key))
    tx.execute(
        """INSERT INTO publishing_queue
               (request_key, kind, subject_person_id, platform, caption, status,
                image_url, external_post_id, updated_at)
           VALUES (%(rk)s, 'member_of_week', %(pid)s, 'facebook', 'c', 'published',
                   'https://cdn/x.png', %(ext)s, NOW() + interval '2 hours')""",
        dict(rk=request_key, pid=person_id, ext=columns.get('external_post_id', '77')))


def test_week_context_skips_a_member_who_opted_out(make_person):
    p = make_person(name='OptedOutSpot')
    rk = f'wk-optout-{p["id"]}'
    with api_tx() as tx:
        tx.execute("UPDATE person SET date_of_birth = '1995-01-01', country = 'US', spotlight_opt_in = FALSE WHERE id = %(id)s",
                   dict(id=p['id']))
        _publish_member_of_week(tx, p['id'], rk)
    spotlight = _week_context()['spotlight']
    assert spotlight is None or spotlight['post_url'] != 'https://www.facebook.com/77'


def test_week_context_skips_a_row_with_a_removal_task(make_person):
    p = make_person(name='RemovedSpot')
    rk = f'wk-removed-{p["id"]}'
    with api_tx() as tx:
        tx.execute("UPDATE person SET date_of_birth = '1995-01-01', country = 'US', spotlight_opt_in = TRUE WHERE id = %(id)s",
                   dict(id=p['id']))
        _publish_member_of_week(tx, p['id'], rk, external_post_id='78')
        tx.execute(
            """INSERT INTO spotlight_removal_task (queue_id, platform, external_post_id, reason)
               SELECT id, platform, external_post_id, 'delete_via_api'
                 FROM publishing_queue WHERE request_key = %(rk)s""",
            dict(rk=rk))
    spotlight = _week_context()['spotlight']
    assert spotlight is None or spotlight['post_url'] != 'https://www.facebook.com/78'


def test_week_context_skips_a_published_row_with_no_external_post_id(make_person):
    """post_url is built from external_post_id, so a row without one would
    link to facebook.com/None."""
    p = make_person(name='NoPostIdSpot')
    rk = f'wk-noext-{p["id"]}'
    with api_tx() as tx:
        tx.execute("UPDATE person SET date_of_birth = '1995-01-01', country = 'US', spotlight_opt_in = TRUE WHERE id = %(id)s",
                   dict(id=p['id']))
        _publish_member_of_week(tx, p['id'], rk, external_post_id=None)
    spotlight = _week_context()['spotlight']
    assert spotlight is None or spotlight['post_url'] != 'https://www.facebook.com/None'
