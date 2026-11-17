from datetime import timedelta
from types import SimpleNamespace

from app.telegram import bot


def test_booking_date_keyboard_covers_today_through_thirty_days() -> None:
    clinic = SimpleNamespace(timezone="Asia/Dubai")
    today, last = bot._booking_date_limits(clinic)

    assert last == today + timedelta(days=30)

    keyboard = bot._date_keyboard(clinic)
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert f"bdate:{today.isoformat()}" in callbacks
    assert f"bdate:{last.isoformat()}" in callbacks
    assert f"bdate:{(last + timedelta(days=1)).isoformat()}" not in callbacks
    assert "bdate-manual" in callbacks
    assert "main-menu" in callbacks
