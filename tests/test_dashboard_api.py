from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.auth import CurrentStaff, get_current_staff
from app.api.models import HandoffStatusRequest
from app.api.routes.dashboard import _load_overview
from app.api.routes.handoffs import _update_handoff
from app.main import app


OWNER = CurrentStaff(
    id=UUID("40000000-0000-4000-8000-000000000001"),
    clinic_id=UUID("10000000-0000-4000-8000-000000000001"),
    auth_user_id=UUID("50000000-0000-4000-8000-000000000001"),
    doctor_id=None,
    full_name="Clinic Owner",
    email="owner@example.com",
    role="owner",
    active=True,
    permissions=frozenset({"staff.manage", "payment.verify", "queue.update_all", "doctor.manage"}),
    overrides=(),
    access_token="test-user-token",
)

DOCTOR = CurrentStaff(
    id=UUID("40000000-0000-4000-8000-000000000002"),
    clinic_id=OWNER.clinic_id,
    auth_user_id=UUID("50000000-0000-4000-8000-000000000002"),
    doctor_id=UUID("30000000-0000-4000-8000-000000000001"),
    full_name="Clinic Doctor",
    email="doctor@example.com",
    role="doctor",
    active=True,
    permissions=frozenset({"appointment.view_own", "queue.view_own"}),
    overrides=(),
    access_token="test-doctor-token",
)


class FakeResponse:
    def __init__(self, data: list[dict[str, object]] | None = None, count: int = 0) -> None:
        self.data = data or []
        self.count = count


class RecordingQuery:
    def __init__(self, table: str, queries: list["RecordingQuery"]) -> None:
        self.table = table
        self.queries = queries
        self.filters: list[tuple[str, object]] = []
        self.selection = ""
        queries.append(self)

    def select(self, selection: str, **_kwargs: object) -> "RecordingQuery":
        self.selection = selection
        return self

    def eq(self, column: str, value: object) -> "RecordingQuery":
        self.filters.append((column, value))
        return self

    def in_(self, column: str, value: object) -> "RecordingQuery":
        self.filters.append((column, value))
        return self

    def gte(self, column: str, value: object) -> "RecordingQuery":
        self.filters.append((column, value))
        return self

    def lt(self, column: str, value: object) -> "RecordingQuery":
        self.filters.append((column, value))
        return self

    def limit(self, _value: int) -> "RecordingQuery":
        return self

    def execute(self) -> FakeResponse:
        if self.table == "clinics":
            return FakeResponse([{"timezone": "Asia/Dubai"}])
        return FakeResponse()


class RecordingClient:
    def __init__(self) -> None:
        self.queries: list[RecordingQuery] = []

    def table(self, name: str) -> RecordingQuery:
        return RecordingQuery(name, self.queries)


def test_staff_me_requires_authentication() -> None:
    response = TestClient(app).get("/api/staff/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_staff_me_returns_server_resolved_role() -> None:
    app.dependency_overrides[get_current_staff] = lambda: OWNER
    try:
        response = TestClient(app).get("/api/staff/me")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["role"] == "owner"
    assert response.json()["clinic_id"] == str(OWNER.clinic_id)


def test_payment_approval_dispatches_protected_rpc(monkeypatch: Any) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_rpc(staff: CurrentStaff, name: str, payload: dict[str, object]) -> dict[str, bool]:
        assert staff is OWNER
        calls.append((name, payload))
        return {"transition_applied": True}

    monkeypatch.setattr("app.api.routes.payments.call_user_rpc", fake_rpc)
    app.dependency_overrides[get_current_staff] = lambda: OWNER
    try:
        response = TestClient(app).post(
            "/api/payments/60000000-0000-4000-8000-000000000001/approve",
            json={"amount_verified": 250},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert calls[0][0] == "verify_payment"
    assert calls[0][1]["p_staff_user_id"] == str(OWNER.id)


def test_payment_rejection_requires_reason() -> None:
    app.dependency_overrides[get_current_staff] = lambda: OWNER
    try:
        response = TestClient(app).post(
            "/api/payments/60000000-0000-4000-8000-000000000001/reject",
            json={"reason": "   "},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_queue_transition_dispatches_expected_and_target_state(monkeypatch: Any) -> None:
    calls: list[dict[str, object]] = []

    async def fake_rpc(_staff: CurrentStaff, _name: str, payload: dict[str, object]) -> dict[str, str]:
        calls.append(payload)
        return {"queue_status": "completed"}

    monkeypatch.setattr("app.api.routes.queue.call_user_rpc", fake_rpc)
    app.dependency_overrides[get_current_staff] = lambda: OWNER
    try:
        response = TestClient(app).post(
            "/api/queue/70000000-0000-4000-8000-000000000001/transition",
            json={"expected_status": "in_progress", "target_status": "completed"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert calls[0]["p_expected_status"] == "in_progress"
    assert calls[0]["p_target_status"] == "completed"


def test_doctor_overview_scopes_every_appointment_query(monkeypatch: Any) -> None:
    client = RecordingClient()
    monkeypatch.setattr("app.api.routes.dashboard.get_supabase_client", lambda: client)

    _load_overview(DOCTOR)

    appointment_queries = [query for query in client.queries if query.table == "appointments"]
    assert appointment_queries
    assert all(("dentist_id", str(DOCTOR.doctor_id)) in query.filters for query in appointment_queries)


def test_handoff_rejects_cross_clinic_or_inactive_assignee(monkeypatch: Any) -> None:
    client = RecordingClient()
    monkeypatch.setattr("app.api.routes.handoffs.get_supabase_client", lambda: client)
    request = HandoffStatusRequest(
        status="in_progress",
        assigned_to_staff_id=UUID("40000000-0000-4000-8000-000000000099"),
    )

    with pytest.raises(HTTPException) as exc_info:
        _update_handoff(OWNER, UUID("80000000-0000-4000-8000-000000000001"), request)

    assert exc_info.value.status_code == 422
