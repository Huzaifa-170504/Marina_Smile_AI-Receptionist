from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "0015_live_reconciliation.sql"
PATIENT_OPERATIONS = ROOT / "supabase" / "migrations" / "0016_patient_operations_foundation.sql"


def sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_reconciliation_preflights_verified_base_tables_and_is_transactional() -> None:
    text = sql()
    assert text.startswith("begin;")
    assert text.rstrip().endswith("commit;")
    assert "live reconciliation prerequisite is missing" in text
    assert "doctor_status_change_requests" in text
    assert "knowledge_chunks" in text


def test_reconciliation_is_data_preserving() -> None:
    text = sql()
    assert "drop table" not in text
    assert "truncate " not in text
    assert "delete from" not in text
    assert "create table" not in text
    assert "drop schema" not in text


def test_legacy_privileged_functions_are_revoked() -> None:
    text = sql()
    for signature in (
        "public.claim_next_notification()",
        "public.deactivate_doctor(uuid,text)",
        "public.set_payment_exemption(uuid,text)",
        "public.transition_visit_status(uuid,text)",
        "public.verify_payment(uuid,numeric,text)",
    ):
        assert signature in text
    assert "revoke all on function public.claim_outbound_notification() from public, anon, authenticated, service_role" in text
    assert "grant execute on function public.claim_outbound_notification() to service_role" in text


def test_security_definer_functions_pin_empty_search_path() -> None:
    text = sql()
    assert text.count("security definer") >= 14
    assert text.count("set search_path = ''") >= text.count("security definer")


def test_existing_has_permission_signature_preserves_live_parameter_name() -> None:
    text = sql()
    assert "function private.has_permission(p_permission text)" in text
    assert "function private.has_permission(permission_text text)" not in text


def test_authenticated_table_privileges_are_opt_in() -> None:
    text = sql()
    assert "revoke all on all tables in schema public from public, anon, authenticated, service_role" in text
    assert "grant all" not in text
    assert "grant truncate" not in text
    assert "grant references" not in text
    assert "grant trigger" not in text


def test_reviewed_rls_policies_are_tenant_and_relationship_scoped() -> None:
    text = sql()
    for policy in (
        "schedules_manage_own",
        "schedule_overrides_manage_own",
        "contacts_authorized_select",
        "patients_authorized_select",
        "conversations_authorized_select",
        "messages_authorized_select",
        "booking_requests_receptionist_update",
        "doctor_requests_doctor_insert",
        "change_options_authorized_select",
        "handoffs_receptionist_all",
        "reminder_consents_receptionist_all",
    ):
        assert f"create policy {policy}" in text
    assert "dentist_id = private.current_doctor_id()" in text
    assert "requested_by_staff_id = private.current_staff_id()" in text
    assert "private.can_view_contact(contact_id)" in text


def test_existing_storage_buckets_remain_private_and_tenant_scoped() -> None:
    text = sql()
    assert "('payment-proofs', 'payment-proofs', false" in text
    assert "('clinic-knowledge', 'clinic-knowledge', false" in text
    assert "(storage.foldername(name))[1] = private.current_clinic_id()::text" in text
    assert "refund-proofs" not in text


def test_realtime_replica_identity_is_restored_without_publication_expansion() -> None:
    text = sql()
    for table in (
        "appointments",
        "appointment_visits",
        "booking_requests",
        "payments",
        "schedule_overrides",
        "appointment_change_requests",
        "handoffs",
    ):
        assert f"alter table public.{table} replica identity full" in text
    assert "alter publication" not in text


def test_only_reviewed_index_set_is_added_and_special_indexes_are_untouched() -> None:
    text = sql()
    for index in (
        "patients_clinic_name_idx",
        "booking_requests_contact_idx",
        "payments_status_idx",
        "appointments_status_idx",
        "doctor_status_requests_one_open_idx",
        "handoffs_assignee_idx",
        "knowledge_chunks_clinic_document_idx",
    ):
        assert index in text
    assert "appointments_no_active_overlap" not in text
    assert "knowledge_chunks_embedding_hnsw_idx" not in text


def test_canonical_operational_rpcs_are_installed_with_least_privilege() -> None:
    text = sql()
    for function in (
        "verify_payment",
        "authorize_payment_exemption",
        "reject_payment",
        "transition_booking_request",
        "reserve_appointment",
        "confirm_appointment_reservation",
        "transition_queue_state",
        "claim_outbound_notification",
        "deactivate_doctor",
    ):
        assert f"function public.{function}" in text
    assert "appointment reservation requires the trusted server role" in text
    assert "notification claiming requires the trusted server role" in text


def test_patient_operations_migration_was_renumbered_without_duplicate_0015() -> None:
    assert PATIENT_OPERATIONS.exists()
    assert not (ROOT / "supabase" / "migrations" / "0015_patient_operations_foundation.sql").exists()
    assert MIGRATION.exists()
