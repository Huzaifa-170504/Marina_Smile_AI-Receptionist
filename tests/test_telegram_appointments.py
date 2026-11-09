from datetime import datetime, timedelta, timezone

from app.telegram import bot


def _row(**overrides):
    now = datetime.now(timezone.utc)
    row = {
        "id": "appointment-1",
        "appointment_reference": "MS-APT-20261109-000001",
        "booking_request_id": "request-1",
        "contact_id": "contact-1",
        "patient_id": "patient-1",
        "status": "confirmed",
        "start_at": (now + timedelta(days=1)).isoformat(),
        "end_at": (now + timedelta(days=1, minutes=75)).isoformat(),
        "service_id": "service-1",
        "dentist_id": "dentist-1",
        "google_calendar_event_id": "event-1",
        "total_amount": 500,
        "currency": "AED",
        "payment_mode": "cash",
        "payment_status": "unpaid",
        "dentists": {"name": "Dr. Sara Haddad"},
        "appointment_services": [
            {
                "service_id": "service-1",
                "service_name": "Scaling and polishing",
                "duration_minutes": 45,
                "start_at": (now + timedelta(days=1)).isoformat(),
                "end_at": (now + timedelta(days=1, minutes=45)).isoformat(),
            },
            {
                "service_id": "service-2",
                "service_name": "Composite filling",
                "duration_minutes": 30,
                "start_at": (now + timedelta(days=1, minutes=45)).isoformat(),
                "end_at": (now + timedelta(days=1, minutes=75)).isoformat(),
            },
        ],
    }
    row.update(overrides)
    return row


def test_appointment_state_groups_upcoming_past_and_cancelled() -> None:
    now = datetime.now(timezone.utc)
    upcoming = _row(id="upcoming")
    past = _row(
        id="past",
        status="completed",
        start_at=(now - timedelta(days=2)).isoformat(),
        end_at=(now - timedelta(days=2, hours=-1)).isoformat(),
    )
    cancelled = _row(id="cancelled", status="cancelled")
    late_confirmed = _row(
        id="late",
        start_at=(now - timedelta(days=1)).isoformat(),
        end_at=(now - timedelta(days=1, hours=-1)).isoformat(),
    )

    upcoming_rows, past_rows, cancelled_rows = bot._appointment_state(
        [upcoming, past, cancelled, late_confirmed]
    )

    assert [row["id"] for row in upcoming_rows] == ["upcoming"]
    assert {row["id"] for row in past_rows} == {"past", "late"}
    assert [row["id"] for row in cancelled_rows] == ["cancelled"]


def test_reschedule_target_uses_all_service_durations(monkeypatch) -> None:
    monkeypatch.setattr(bot, "_clinic_id", lambda: "clinic-1")
    target = bot._reschedule_target(_row())

    assert target["appointment_id"] == "appointment-1"
    assert target["duration_minutes"] == 75
    assert target["service_ids"] == ["service-1", "service-2"]
    assert target["service_names"] == "Scaling and polishing, Composite filling"
    assert target["reference"] == "MS-APT-20261109-000001"


def test_appointment_detail_lines_show_each_service_and_payment() -> None:
    lines = bot._appointment_detail_lines(_row(), "Asia/Dubai")
    text = "\n".join(lines)

    assert "MS-APT-20261109-000001" in text
    assert "Dr. Sara Haddad" in text
    assert "Scaling and polishing" in text
    assert "Composite filling" in text
    assert "AED 500.00" in text
    assert "cash · unpaid" in text
