import pytest

from app.telegram.privacy import PatientSafeSupabase


class _StubClient:
    def table(self, name):
        return name


def test_patient_safe_client_allows_patient_tables() -> None:
    client = PatientSafeSupabase(_StubClient())
    assert client.table("patients") == "patients"
    assert client.table("appointments") == "appointments"


def test_patient_safe_client_blocks_staff_and_payroll_tables() -> None:
    client = PatientSafeSupabase(_StubClient())
    for table in ("staff_users", "staff_salaries", "payroll", "refund_requests"):
        with pytest.raises(PermissionError):
            client.table(table)


def test_patient_safe_client_blocks_internal_rpcs() -> None:
    client = PatientSafeSupabase(_StubClient())
    with pytest.raises(PermissionError):
        client.rpc("verify_payment")
