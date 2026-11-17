from app.operations.patient_booking import normalize_phone, phone_matches
from app.telegram.bot import _normalize_identifier


def test_uae_local_and_international_numbers_match() -> None:
    assert normalize_phone("+971 4 123 4567") == "97141234567"
    assert normalize_phone("04 123 4567") == "97141234567"
    assert normalize_phone("+971 50 123 4567") == "971501234567"
    assert normalize_phone("050 123 4567") == "971501234567"
    assert phone_matches("04 123 4567", "+971 4 123 4567")
    assert phone_matches("050 123 4567", "+971 50 123 4567")


def test_pakistan_local_and_international_numbers_match() -> None:
    assert normalize_phone("+92 300 1234567") == "923001234567"
    assert normalize_phone("0300 1234567") == "923001234567"
    assert normalize_phone("03118917906") == "923118917906"
    assert phone_matches("0300 1234567", "+92 300 1234567")
    assert phone_matches("03118917906", "+92 311 8917906")


def test_telegram_identifier_normalizes_supported_formats() -> None:
    assert _normalize_identifier("+971 4 123 4567") == ("phone", "97141234567")
    assert _normalize_identifier("04 123 4567") == ("phone", "97141234567")
    assert _normalize_identifier("+92 300 1234567") == ("phone", "923001234567")
    assert _normalize_identifier("0300 1234567") == ("phone", "923001234567")
    assert _normalize_identifier("patient@example.com") == (
        "email",
        "patient@example.com",
    )
