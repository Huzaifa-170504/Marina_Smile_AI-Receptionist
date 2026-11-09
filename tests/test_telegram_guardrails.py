from app.telegram import bot


def test_emergency_symptoms_are_detected() -> None:
    assert bot._is_emergency("I am having difficulty breathing")
    assert bot._is_emergency("There is severe facial swelling")
    assert bot._is_emergency("The bleeding won't stop")
    assert not bot._is_emergency("I would like a teeth cleaning")


def test_rate_limit_blocks_excess_messages(monkeypatch) -> None:
    chat_id = 999999
    bot._rate_buckets.pop(chat_id, None)
    monkeypatch.setattr(bot, "RATE_LIMIT_MAX_MESSAGES", 2)
    try:
        assert not bot._rate_limited(chat_id)
        assert not bot._rate_limited(chat_id)
        assert bot._rate_limited(chat_id)
    finally:
        bot._rate_buckets.pop(chat_id, None)
