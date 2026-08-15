"""Template-compliance lock for one-off member service notes.

Two personal notes (2026-08-10/12) shipped with a hand-rolled 28px
sans heading instead of the canonical Ultra title image; the owner
caught the drift by eye. This pins the member_note module to the
canonical shell so the regression class cannot recur silently.
"""
from emails.member_note import member_note_html


def test_member_note_is_on_the_canonical_template():
    html = member_note_html(
        preheader="Test preheader.",
        paragraphs=["First paragraph.", "Second with <strong>bold</strong>."],
        callout_text="A callout line.",
        button_label="Open Ahavah",
        button_url="https://ahavah.app/discover",
        footer_note="Only reminder note.",
    )
    # The canonical Ultra title image, both variants.
    assert "title-note.png" in html
    assert "title-note-wht.png" in html
    # No hand-rolled display heading: the off-template notes used an
    # inline 28px bold paragraph as a fake title.
    assert "font-size:28px" not in html
    # Body copy present, standard 17px body style, callout and button.
    assert "First paragraph." in html
    assert "font-size:17px" in html
    assert "A callout line." in html
    assert "Open Ahavah" in html
    assert "Only reminder note." in html
    # No em dashes anywhere in the rendered note.
    assert "—" not in html


def test_member_note_optional_blocks_are_optional():
    html = member_note_html(
        preheader="P.",
        paragraphs=["Only paragraph."],
    )
    assert "title-note.png" in html
    assert "Only paragraph." in html
