from emails.community_weekly import community_weekly_html, SUBJECT

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
    import pytest
    base = dict(first_name='Sarah', age=29, country='US',
                image_url='https://x/y.png', post_url='https://fb/p')
    with pytest.raises(ValueError):
        community_weekly_html([], 1, dict(base, image_url='javascript:alert(1)'), 'https://a', 'https://u')
    with pytest.raises(ValueError):
        community_weekly_html([], 1, dict(base, post_url='http://fb/p'), 'https://a', 'https://u')
