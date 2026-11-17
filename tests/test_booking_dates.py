from datetime import timedelta

from app.operations.models import Clinic
from app.telegram import bot


def test_booking_date_keyboard_covers_today_through_thirty_days() -> None:
    clinic = Clinic(
        id="10000000-0000-4000-8000-000000000001",
        name="Marina Smile",
        timezone="Asia/Dubai",
    )
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

    saturday = next(
        today + timedelta(days=offset)
        for offset in range(31)
        if (today + timedelta(days=offset)).isoweekday() == 6
    )
    assert f"bdate:{saturday.isoformat()}" not in callbacks


def test_booking_date_keyboard_excludes_holidays() -> None:
    clinic = Clinic(
        id="10000000-0000-4000-8000-000000000001",
        name="Marina Smile",
        timezone="Asia/Dubai",
    )
    today, _last = bot._booking_date_limits(clinic)
    holiday = next(
        today + timedelta(days=offset)
        for offset in range(31)
        if (today + timedelta(days=offset)).isoweekday() <= 5
    )

    keyboard = bot._date_keyboard(clinic, {holiday})
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert f"bdate:{holiday.isoformat()}" not in callbacks
