from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPATIBILITY = ROOT / "supabase" / "migrations" / "20261020083343_patient_operations_coalesce_compatibility.sql"
DEMO_QUOTA = ROOT / "supabase" / "migrations" / "20261020083413_demo_query_rate_limit.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_compatibility_migration_replaces_every_affected_function() -> None:
    sql = _read(COMPATIBILITY)
    assert sql.startswith("begin;")
    assert sql.rstrip().endswith("commit;")
    assert "pg_catalog.coalesce(" not in sql
    for signature in (
        "private.audit_appointment_write()",
        "private.manage_appointment_reference()",
        "private.validate_refund_request()",
        "public.request_refund(",
        "public.check_in_appointment(",
    ):
        assert f"create or replace function {signature}" in sql


def test_demo_quota_is_service_only_and_database_serialized() -> None:
    sql = _read(DEMO_QUOTA)
    assert "alter table public.demo_query_events enable row level security" in sql
    assert "revoke all on table public.demo_query_events from public, anon, authenticated" in sql
    assert "security definer" in sql
    assert "set search_path = ''" in sql
    assert "private.is_service_role()" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "grant execute on function public.consume_demo_query_quota" in sql
    assert "to service_role" in sql
    assert " to anon" not in sql
    assert " to authenticated" not in sql
