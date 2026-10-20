from __future__ import annotations

import pytest

from app.operations.models import Service
from app.repositories.services import match_services


CLINIC_ID = "10000000-0000-4000-8000-000000000001"


def _service(name: str, index: int = 1) -> Service:
    return Service(
        id=f"20000000-0000-4000-8000-{index:012d}",
        clinic_id=CLINIC_ID,
        name=name,
        duration_minutes=30,
    )


@pytest.mark.parametrize(
    ("patient_text", "service_name"),
    [
        ("whitening", "In-clinic teeth whitening"),
        ("teeth whitening", "In-clinic teeth whitening"),
        ("in-clinic whitening", "In-clinic teeth whitening"),
        ("cleaning", "Scaling and polishing"),
        ("dental cleaning", "Scaling and polishing"),
        ("scaling", "Scaling and polishing"),
        ("scaling and polishing", "Scaling and polishing"),
        ("check-up", "Consultation and check-up"),
        ("checkup", "Consultation and check-up"),
        ("consultation", "Consultation and check-up"),
        ("dental consultation", "Consultation and check-up"),
        ("root canal", "Root canal treatment"),
        ("rct", "Root canal treatment"),
        ("invisalign", "Invisalign consultation and 3D scan"),
        ("invisalign consultation", "Invisalign consultation and 3D scan"),
        ("3d scan", "Invisalign consultation and 3D scan"),
        ("implant", "Dental implant consultation"),
        ("implant consultation", "Dental implant consultation"),
        ("dental implant", "Dental implant consultation"),
        ("filling", "Composite filling"),
        ("composite filling", "Composite filling"),
        ("emergency", "Emergency dental visit"),
        ("emergency visit", "Emergency dental visit"),
        ("dental emergency", "Emergency dental visit"),
    ],
)
def test_service_aliases_resolve_to_controlled_active_name(
    patient_text: str,
    service_name: str,
) -> None:
    expected = _service(service_name)

    assert match_services([expected], patient_text) == [expected]


def test_exact_service_name_takes_precedence_over_alias() -> None:
    exact = _service("Cleaning", 1)
    canonical = _service("Scaling and polishing", 2)

    assert match_services([exact, canonical], "  CLEANING! ") == [exact]


def test_unknown_service_does_not_fuzzy_match() -> None:
    service = _service("In-clinic teeth whitening")

    assert match_services([service], "white") == []


def test_duplicate_canonical_service_names_remain_ambiguous() -> None:
    first = _service("In-clinic teeth whitening", 1)
    second = _service("In clinic teeth whitening", 2)

    assert match_services([first, second], "whitening") == [first, second]
