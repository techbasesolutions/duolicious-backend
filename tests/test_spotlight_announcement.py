from emails.spotlight_announcement import spotlight_announcement_html, SUBJECT

def test_announcement_is_on_template_and_has_no_em_dash():
    html = spotlight_announcement_html('https://ahavah.app/spotlight/confirm/t', 'https://ahavah.app/settings/privacy', 'https://ahavah.app/u/x')
    assert 'title-spotlight.png' in html and 'title-spotlight-wht.png' in html
    assert 'https://ahavah.app/spotlight/confirm/t' in html
    assert 'Unsubscribe' in html
    assert '—' not in html and '—' not in SUBJECT
    assert 'first name, age, country' in html.lower()
