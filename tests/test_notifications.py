from app.telegram.notifications import _format_notification, _format_when


def _appointment() -> dict:
    return {
        "id": "appointment-1",
        "appointment_reference": "MS-APT-20261110-000001",
        "status": "confirmed",
        "start_at": "2026-11-10T10:00:00+00:00",
        "end_at": "2026-11-10T11:15:00+00:00",
        "payment_mode": "cash",
        "payment_status": "unpaid",
        "dentists": {"name": "Dr. Sara Haddad"},
        "appointment_services": [
            {"service_name": "Scaling and polishing"},
            {"service_name": "Composite filling"},
        ],
        "patients": {"full_name": "Sara Ali"},
        "clinics": {"name": "Marina Smile Dental Clinic", "timezone": "Asia/Dubai"},
    }


def test_notification_types_include_reference_and_details() -> None:
    appointment = _appointment()
    for notification_type in (
        "booking_confirmation",
        "reschedule_confirmation",
        "cancellation_confirmation",
        "appointment_reminder",
        "final_reminder",
        "feedback_request",
    ):
        text = _format_notification(
            {"notification_type": notification_type, "template_data": {}},
            appointment,
        )
        assert text is not None
        assert "MS-APT-20261110-000001" in text
        assert "10 Nov 2026" in text
        assert "Dr. Sara Haddad" in text


def test_feedback_request_names_the_clinic_and_asks_for_feedback() -> None:
    text = _format_notification(
        {"notification_type": "feedback_request", "template_data": {}},
        _appointment(),
    )
    assert "Marina Smile Dental Clinic" in text
    assert "feedback" in text


def test_unknown_notification_type_returns_none() -> None:
    assert _format_notification(
        {"notification_type": "unknown_type", "template_data": {}},
        _appointment(),
    ) is None


def test_format_when_uses_clinic_timezone() -> None:
    formatted = _format_when("2026-11-10T10:00:00+00:00", "Asia/Dubai")
    assert "10 Nov 2026" in formatted
    assert "02:00 PM" in formatted
