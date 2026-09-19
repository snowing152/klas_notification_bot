from app.keyboards import create_quick_access_keyboard, quick_access_labels
from app.strings import Language, Strings


def test_quick_access_labels_cover_every_language():
    """The reply keyboard is client-side and only refreshes on the next
    message carrying reply_markup, so a user who just switched language may
    still be looking at the old labels - the filter that routes their tap
    has to match all three languages, not just the current one."""
    labels = quick_access_labels("button_todos")

    assert labels == {Strings.get("button_todos", lang) for lang in Language}
    # Sanity: the languages actually produce different text, or this test
    # would pass even if quick_access_labels only ever looked at English.
    assert Strings.get("button_todos", Language.EN) != Strings.get(
        "button_todos", Language.KO
    )


def test_quick_access_keyboard_uses_localized_labels():
    markup = create_quick_access_keyboard(Language.KO)
    labels = [button.text for row in markup.keyboard for button in row]

    assert labels == [
        Strings.get("button_todos", Language.KO),
        Strings.get("button_qr", Language.KO),
        Strings.get("button_menu", Language.KO),
        Strings.get("button_news", Language.KO),
    ]
