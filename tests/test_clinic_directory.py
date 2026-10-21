import pytest

from app.operations.models import Dentist, Service
from app.services import clinic_directory


CLINIC_ID = "10000000-0000-4000-8000-000000000001"


def _dentist(
    name: str,
    index: int,
    specialty: str | None = None,
) -> Dentist:
    return Dentist(
        id=f"20000000-0000-4000-8000-{index:012d}",
        clinic_id=CLINIC_ID,
        name=name,
        specialty=specialty,
    )


def _service(name: str, index: int) -> Service:
    return Service(
        id=f"30000000-0000-4000-8000-{index:012d}",
        clinic_id=CLINIC_ID,
        name=name,
        duration_minutes=30,
    )


def test_greeting_is_answered_without_database_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clinic_directory,
        "list_active_dentists",
        lambda _clinic_id: pytest.fail("A greeting must not query dentists"),
    )

    result = clinic_directory.answer_clinic_directory_question(CLINIC_ID, "Hi!")

    assert result is not None
    assert result.status == "answered"
    assert result.grounding == "conversation"
    assert "AI receptionist" in result.text


def test_service_catalog_handles_common_demo_misspellings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = [
        _service("Scaling and polishing", 1),
        _service("In-clinic teeth whitening", 2),
    ]
    monkeypatch.setattr(
        clinic_directory,
        "list_active_services",
        lambda _clinic_id: services,
    )

    result = clinic_directory.answer_clinic_directory_question(
        CLINIC_ID,
        "Wich srvices do you offrered?",
    )

    assert result is not None
    assert result.status == "answered"
    assert result.grounding == "supabase"
    assert "2 active services" in result.text
    assert "Scaling and polishing" in result.text


def test_active_doctors_include_specialties_and_availability_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dentists = [
        _dentist("Dr. Sara Haddad", 1, "General and Cosmetic Dentistry"),
        _dentist("Dr. Omar Khalil", 2, "Orthodontics"),
    ]
    monkeypatch.setattr(
        clinic_directory,
        "list_active_dentists",
        lambda _clinic_id: dentists,
    )
    monkeypatch.setattr(
        clinic_directory,
        "list_active_services",
        lambda _clinic_id: [],
    )

    result = clinic_directory.answer_clinic_directory_question(
        CLINIC_ID,
        "Which doctors are available?",
    )

    assert result is not None
    assert "Dr. Omar Khalil (Orthodontics)" in result.text
    assert "Dr. Sara Haddad (General and Cosmetic Dentistry)" in result.text
    assert "Use Live availability" in result.text


def test_service_provider_question_uses_live_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dentist = _dentist("Dr. Omar Khalil", 1, "Orthodontics")
    service = _service("Invisalign consultation and 3D scan", 1)
    monkeypatch.setattr(
        clinic_directory,
        "list_active_dentists",
        lambda _clinic_id: [dentist],
    )
    monkeypatch.setattr(
        clinic_directory,
        "list_active_services",
        lambda _clinic_id: [service],
    )
    monkeypatch.setattr(
        clinic_directory,
        "filter_dentists_for_service",
        lambda _clinic_id, _dentists, _service_id: [dentist],
    )

    result = clinic_directory.answer_clinic_directory_question(
        CLINIC_ID,
        "Which dentist handles Invisalign?",
    )

    assert result is not None
    assert result.status == "answered"
    assert result.text == (
        "Invisalign consultation and 3D scan is currently assigned to "
        "Dr. Omar Khalil."
    )


def test_doctor_service_question_uses_live_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dentist = _dentist("Dr. Sara Haddad", 1)
    services = [
        _service("Composite filling", 1),
        _service("In-clinic teeth whitening", 2),
    ]
    monkeypatch.setattr(
        clinic_directory,
        "list_active_dentists",
        lambda _clinic_id: [dentist],
    )
    monkeypatch.setattr(
        clinic_directory,
        "list_active_services",
        lambda _clinic_id: services,
    )
    monkeypatch.setattr(
        clinic_directory,
        "filter_services_for_dentist",
        lambda _clinic_id, _services, _dentist_id: [services[1]],
    )

    result = clinic_directory.answer_clinic_directory_question(
        CLINIC_ID,
        "Which services are offered by Dr. Sara Haddad?",
    )

    assert result is not None
    assert result.status == "answered"
    assert result.grounding == "supabase"
    assert result.text == (
        "Dr. Sara Haddad is currently assigned to In-clinic teeth whitening."
    )


def test_unknown_doctor_requests_clarification_from_active_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dentist = _dentist("Dr. Sara Haddad", 1)
    monkeypatch.setattr(
        clinic_directory,
        "list_active_dentists",
        lambda _clinic_id: [dentist],
    )

    result = clinic_directory.answer_clinic_directory_question(
        CLINIC_ID,
        "What services does Dr. Unknown offer?",
    )

    assert result is not None
    assert result.status == "needs_clarification"
    assert "Dr. Sara Haddad" in result.text


def test_unrelated_question_continues_to_rag() -> None:
    assert clinic_directory.answer_clinic_directory_question(
        CLINIC_ID,
        "How much is teeth whitening?",
    ) is None
