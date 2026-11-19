import asyncio
from types import SimpleNamespace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.telegram import bot
from app.telegram.bot import _is_main_menu_markup, _menu
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


def test_main_menu_is_not_treated_as_a_cancellable_workflow() -> None:
    assert _is_main_menu_markup(_menu())
    assert not _is_main_menu_markup(
        ReplyKeyboardMarkup(
            [[KeyboardButton("Share registered phone", request_contact=True)]]
        )
    )


def test_booking_and_existing_booking_prompts_use_separate_states() -> None:
    async def run() -> tuple[str, str, str, str]:
        class Message:
            def __init__(self) -> None:
                self.texts: list[str] = []

            async def reply_text(self, text: str, **_kwargs: object) -> None:
                self.texts.append(text)

        booking_message = Message()
        lookup_message = Message()
        booking_context = SimpleNamespace(user_data={})
        lookup_context = SimpleNamespace(user_data={})
        await bot._begin_booking(
            SimpleNamespace(effective_message=booking_message), booking_context
        )
        await bot._begin_lookup(
            SimpleNamespace(effective_message=lookup_message),
            lookup_context,
            "appointments",
        )
        return (
            booking_message.texts[0],
            lookup_message.texts[0],
            booking_context.user_data[bot.STATE],
            lookup_context.user_data[bot.STATE],
        )

    booking_prompt, lookup_prompt, booking_state, lookup_state = asyncio.run(run())

    assert booking_prompt.startswith("Enter the phone number you want to use for the booking.")
    assert lookup_prompt.startswith("Enter the phone number you used to book.")
    assert booking_state == "booking_identifier"
    assert lookup_state == "lookup_identifier"


def test_booking_slot_callback_is_bound_to_selected_date() -> None:
    slot = object()
    context = SimpleNamespace(
        user_data={"booking_date": "2026-11-20", "slots": [slot]}
    )

    assert bot._booking_slot_from_callback("bslot:2026-11-20:0", context) is slot

    try:
        bot._booking_slot_from_callback("bslot:2026-11-21:0", context)
    except ValueError as exc:
        assert "different date" in str(exc)
    else:
        raise AssertionError("stale slot callback was accepted")
