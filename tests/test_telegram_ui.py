from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.telegram.ui import CANCEL_LABEL, append_cancel, cancel_keyboard


def test_append_cancel_preserves_existing_buttons() -> None:
    original = InlineKeyboardMarkup([
        [InlineKeyboardButton("A", callback_data="a")],
        [InlineKeyboardButton("B", callback_data="b")],
    ])

    result = append_cancel(original)

    callbacks = [
        button.callback_data for row in result.inline_keyboard for button in row
    ]
    assert callbacks == ["a", "b", "main-menu"]
    assert result.inline_keyboard[-1][0].text == CANCEL_LABEL


def test_append_cancel_does_not_duplicate_main_menu() -> None:
    original = cancel_keyboard()
    result = append_cancel(original)
    callbacks = [
        button.callback_data for row in result.inline_keyboard for button in row
    ]
    assert callbacks == ["main-menu"]
