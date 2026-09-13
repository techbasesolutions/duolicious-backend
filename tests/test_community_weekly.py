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
