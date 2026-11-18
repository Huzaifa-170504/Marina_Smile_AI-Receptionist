import pytest

from app.telegram.privacy import PatientSafeSupabase


class _StubStorage:
    def from_(self, bucket):
        return bucket


class _StubClient:
    storage = _StubStorage()

    def table(self, name):
        return name

    def rpc(self, function_name, params=None):
        return function_name


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


def test_patient_safe_client_allows_booking_rpcs() -> None:
    client = PatientSafeSupabase(_StubClient())
    assert client.rpc("reserve_appointment") == "reserve_appointment"
    assert (
        client.rpc("confirm_appointment_reservation")
        == "confirm_appointment_reservation"
    )


def test_patient_safe_client_restricts_storage_buckets() -> None:
    client = PatientSafeSupabase(_StubClient())
    assert client.storage.from_("payment-proofs") == "payment-proofs"
    for bucket in ("refund-proofs", "clinic-knowledge"):
        with pytest.raises(PermissionError):
            client.storage.from_(bucket)
