from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.auth import CurrentStaff, get_current_staff
from app.api.models import DoctorOnboardingRequest, StaffInviteRequest
from app.integrations.supabase_auth import AuthAccount, AuthOperationError
from app.main import app
from app.services.staff_onboarding import OnboardingError, StaffOnboardingService


CLINIC_ID = UUID("10000000-0000-4000-8000-000000000001")
OWNER = CurrentStaff(
    id=UUID("40000000-0000-4000-8000-000000000001"), clinic_id=CLINIC_ID,
    auth_user_id=UUID("50000000-0000-4000-8000-000000000001"), doctor_id=None,
    full_name="Owner", email="owner@example.com", role="owner", active=True,
    permissions=frozenset({"doctor.manage", "staff.manage"}), overrides=(), access_token="unused",
)
UNAUTHORIZED = CurrentStaff(
    id=UUID("40000000-0000-4000-8000-000000000002"), clinic_id=CLINIC_ID,
    auth_user_id=UUID("50000000-0000-4000-8000-000000000002"), doctor_id=uuid4(),
    full_name="Doctor", email="doctor@example.com", role="doctor", active=True,
    permissions=frozenset({"appointment.view_own"}), overrides=(), access_token="unused",
)
SERVICE_ID = UUID("20000000-0000-4000-8000-000000000001")


class Response:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data


class MemoryQuery:
    def __init__(self, client: "MemoryClient", table: str) -> None:
        self.client = client; self.table = table; self.filters: list[tuple[str, Any]] = []
        self.operation = "select"; self.payload: Any = None; self.max_rows: int | None = None

    def select(self, *_args: Any, **_kwargs: Any) -> "MemoryQuery": return self
    def eq(self, column: str, value: Any) -> "MemoryQuery": self.filters.append((column, value)); return self
    def in_(self, column: str, values: list[Any]) -> "MemoryQuery": self.filters.append((column, set(values))); return self
    def limit(self, value: int) -> "MemoryQuery": self.max_rows = value; return self
    def order(self, *_args: Any, **_kwargs: Any) -> "MemoryQuery": return self
    def insert(self, payload: Any) -> "MemoryQuery": self.operation = "insert"; self.payload = payload; return self
    def update(self, payload: Any) -> "MemoryQuery": self.operation = "update"; self.payload = payload; return self
    def delete(self) -> "MemoryQuery": self.operation = "delete"; return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for column, expected in self.filters:
            actual = row.get(column)
            if isinstance(expected, set):
                if str(actual) not in {str(item) for item in expected}: return False
            elif str(actual).lower() != str(expected).lower(): return False
        return True

    def execute(self) -> Response:
        rows = self.client.tables.setdefault(self.table, [])
        if self.operation == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            created = []
            for item in items:
                row = {**item}; row.setdefault("id", str(uuid4())); rows.append(row); created.append(row)
            return Response(created)
        matched = [row for row in rows if self._matches(row)]
        if self.operation == "update":
            for row in matched: row.update(self.payload)
            return Response(matched)
        if self.operation == "delete":
            self.client.tables[self.table] = [row for row in rows if not self._matches(row)]
            return Response(matched)
        return Response(matched[: self.max_rows] if self.max_rows else matched)


class MemoryClient:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "services": [{"id": str(SERVICE_ID), "clinic_id": str(CLINIC_ID), "active": True}],
            "staff_users": [], "dentists": [], "dentist_services": [], "dentist_schedules": [],
        }

    def table(self, name: str) -> MemoryQuery: return MemoryQuery(self, name)


class FailFinalizeQuery(MemoryQuery):
    def execute(self) -> Response:
        if self.table == "dentists" and self.operation == "update" and self.payload.get("active") is True:
            raise RuntimeError("finalization failed")
        return super().execute()


class FailFinalizeClient(MemoryClient):
    def table(self, name: str) -> MemoryQuery: return FailFinalizeQuery(self, name)


class FakeAuth:
    def __init__(self) -> None:
        self.accounts: dict[UUID, AuthAccount] = {}; self.invites: list[str] = []; self.recoveries: list[str] = []

    def list_accounts(self) -> list[AuthAccount]: return list(self.accounts.values())
    def find_by_email(self, email: str) -> AuthAccount | None:
        return next((account for account in self.accounts.values() if account.email == email), None)
    def get_account(self, auth_user_id: UUID) -> AuthAccount | None: return self.accounts.get(auth_user_id)
    def invite(self, *, email: str, onboarding_metadata: dict[str, str], **_kwargs: Any) -> AuthAccount:
        self.invites.append(email); account = AuthAccount(uuid4(), email, datetime.now(timezone.utc), None, {"marina_smile_onboarding": onboarding_metadata}); self.accounts[account.id] = account; return account
    def send_recovery(self, email: str, _redirect_to: str) -> None: self.recoveries.append(email)
    def delete_account(self, auth_user_id: UUID) -> None: self.accounts.pop(auth_user_id, None)


class FailingInviteAuth(FakeAuth):
    def invite(self, **_kwargs: Any) -> AuthAccount: raise AuthOperationError("invite unavailable")


class FailingDeleteAuth(FakeAuth):
    def delete_account(self, _auth_user_id: UUID) -> None: raise AuthOperationError("delete unavailable")


def service_with(client: MemoryClient, auth: FakeAuth) -> StaffOnboardingService:
    service = StaffOnboardingService.__new__(StaffOnboardingService)
    service.client = client; service.auth = auth; service.dashboard_url = "http://localhost:3000"
    return service


def doctor_request(**changes: Any) -> DoctorOnboardingRequest:
    payload = {
        "full_name": "Dr. New", "email": "new.doctor@example.com", "specialty": "Dentistry",
        "bio": "Clinical bio", "service_ids": [SERVICE_ID],
        "weekly_schedule": [{"weekday": 0, "start_time": "09:00", "end_time": "17:00"}],
        "active": True,
    }
    payload.update(changes)
    return DoctorOnboardingRequest(**payload)


def test_owner_adds_new_doctor_with_dentist_staff_and_invitation() -> None:
    client, auth = MemoryClient(), FakeAuth()
    result = service_with(client, auth).onboard_doctor(OWNER, doctor_request())
    assert auth.invites == ["new.doctor@example.com"]
    dentist = client.tables["dentists"][0]; staff = client.tables["staff_users"][0]
    assert dentist["active"] is True
    assert staff["doctor_id"] == dentist["id"] == str(result.doctor_id)
    assert staff["auth_user_id"] == str(next(iter(auth.accounts)))
    assert client.tables["dentist_services"][0]["dentist_id"] == dentist["id"]
    assert client.tables["dentist_schedules"][0]["dentist_id"] == dentist["id"]


def test_doctor_form_contract_rejects_password_and_has_no_existing_doctor_field() -> None:
    with pytest.raises(ValidationError): doctor_request(password="owner-chosen")
    assert "doctor_id" not in DoctorOnboardingRequest.model_fields


@pytest.mark.parametrize("role", ["receptionist", "payment_verifier"])
def test_non_doctor_invitation_uses_null_doctor(role: str) -> None:
    client, auth = MemoryClient(), FakeAuth()
    result = service_with(client, auth).invite_staff(
        OWNER, StaffInviteRequest(full_name="Employee", email=f"{role}@example.com", role=role),  # type: ignore[arg-type]
    )
    assert result.doctor_id is None
    assert client.tables["staff_users"][0]["doctor_id"] is None


def test_invitation_failure_leaves_doctor_inactive_and_reports_failure() -> None:
    client = MemoryClient()
    with pytest.raises(OnboardingError, match="remains inactive"):
        service_with(client, FailingInviteAuth()).onboard_doctor(OWNER, doctor_request())
    assert client.tables["dentists"][0]["active"] is False
    assert str(client.tables["dentists"][0]["deactivation_reason"]).startswith("Onboarding failed")
    assert client.tables["staff_users"] == []


def test_failed_finalization_forces_linked_staff_and_doctor_inactive() -> None:
    client, auth = FailFinalizeClient(), FailingDeleteAuth()
    with pytest.raises(OnboardingError, match="remains inactive"):
        service_with(client, auth).onboard_doctor(OWNER, doctor_request())
    assert client.tables["staff_users"][0]["active"] is False
    assert client.tables["dentists"][0]["active"] is False


def test_resend_reuses_existing_records() -> None:
    client, auth = MemoryClient(), FakeAuth(); service = service_with(client, auth)
    created = service.invite_staff(OWNER, StaffInviteRequest(full_name="Reception", email="reception@example.com", role="receptionist"))
    counts = {name: len(rows) for name, rows in client.tables.items()}
    service.resend_invitation(OWNER, created.staff_id)
    assert {name: len(rows) for name, rows in client.tables.items()} == counts
    assert auth.recoveries == ["reception@example.com"]


def test_access_and_account_setup_statuses_are_independent() -> None:
    client, auth = MemoryClient(), FakeAuth(); service = service_with(client, auth)
    created = service.invite_staff(
        OWNER,
        StaffInviteRequest(full_name="Future Employee", email="future@example.com", role="receptionist", active=False),
    )
    rows = service.list_staff(OWNER)
    assert rows[0]["id"] == str(created.staff_id)
    assert rows[0]["access_status"] == "inactive"
    assert rows[0]["account_setup_status"] == "invited"


def test_onboarding_logs_identifiers_but_not_credentials(caplog: pytest.LogCaptureFixture) -> None:
    client, auth = MemoryClient(), FakeAuth()
    caplog.set_level(logging.INFO, logger="app.services.staff_onboarding")
    service_with(client, auth).invite_staff(
        OWNER, StaffInviteRequest(full_name="Employee", email="employee@example.com", role="receptionist"),
    )
    output = caplog.text.lower()
    assert "staff_invited" in output
    assert "password" not in output
    assert "access_token" not in output
    assert "refresh_token" not in output


def test_staff_schema_has_no_password_column() -> None:
    schema = Path("supabase/migrations/0002_clinic_staff_auth.sql").read_text(encoding="utf-8").lower()
    staff_definition = schema.split("create table public.staff_users", 1)[1].split(");", 1)[0]
    assert "password" not in staff_definition


def test_password_is_rejected_from_generic_owner_invitation() -> None:
    with pytest.raises(ValidationError):
        StaffInviteRequest(full_name="Employee", email="employee@example.com", role="receptionist", password="secret")  # type: ignore[call-arg]


def test_unauthorized_staff_cannot_invite_or_add_doctor() -> None:
    app.dependency_overrides[get_current_staff] = lambda: UNAUTHORIZED
    try:
        invite = TestClient(app).post("/api/staff/invite", json={"full_name": "New", "email": "new@example.com", "role": "receptionist"})
        doctor = TestClient(app).post("/api/doctors", json=doctor_request().model_dump(mode="json"))
    finally:
        app.dependency_overrides.clear()
    assert invite.status_code == 403
    assert doctor.status_code == 403
