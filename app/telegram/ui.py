from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


CANCEL_LABEL = "Cancel & Return to Main Menu"
CANCEL_CALLBACK = "main-menu"


def cancel_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(CANCEL_LABEL, callback_data=CANCEL_CALLBACK)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[cancel_button()]])


def append_cancel(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = [list(row) for row in markup.inline_keyboard]
    if any(
        button.callback_data == CANCEL_CALLBACK
        for row in rows
        for button in row
    ):
        return markup
    rows.append([cancel_button()])
    return InlineKeyboardMarkup(rows)
