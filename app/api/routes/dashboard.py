from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from app.api.auth import AuthenticatedStaff, CurrentStaff
from app.api.models import OverviewResponse
from app.database.supabase import get_supabase_client


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _count(query: object) -> int:
    response = query.execute()  # type: ignore[attr-defined]
    return int(response.count or 0)


def _load_overview(staff: CurrentStaff) -> OverviewResponse:
    client = get_supabase_client()
    clinic = (
        client.table("clinics")
        .select("timezone")
        .eq("id", str(staff.clinic_id))
        .limit(1)
        .execute()
    )
    timezone_name = clinic.data[0]["timezone"] if clinic.data else "Asia/Dubai"
    local_now = datetime.now(ZoneInfo(timezone_name))
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    end = (local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)

    can_view_appointments = bool(
        {"appointment.view_all", "appointment.view_own"} & staff.permissions
    )
    metrics = OverviewResponse()
    if can_view_appointments:
        base = (
            client.table("appointments")
            .select("id", count="exact")
            .eq("clinic_id", str(staff.clinic_id))
        )
        if staff.role == "doctor" and staff.doctor_id:
            base = base.eq("dentist_id", str(staff.doctor_id))
        metrics.todays_appointments = _count(base.gte("start_at", start.isoformat()).lt("start_at", end.isoformat()))

        upcoming = (
            client.table("appointments")
            .select("id", count="exact")
            .eq("clinic_id", str(staff.clinic_id))
            .in_("status", ["pending", "confirmed"])
            .gte("start_at", local_now.astimezone(timezone.utc).isoformat())
        )
        if staff.role == "doctor" and staff.doctor_id:
            upcoming = upcoming.eq("dentist_id", str(staff.doctor_id))
        metrics.upcoming_appointments = _count(upcoming)
        completed = (
            client.table("appointments").select("id", count="exact")
            .eq("clinic_id", str(staff.clinic_id)).eq("status", "completed")
            .gte("completed_at", start.isoformat()).lt("completed_at", end.isoformat())
        )
        cancelled = (
            client.table("appointments").select("id", count="exact")
            .eq("clinic_id", str(staff.clinic_id)).eq("status", "cancelled")
            .gte("cancelled_at", start.isoformat()).lt("cancelled_at", end.isoformat())
        )
        no_shows = (
            client.table("appointments").select("id", count="exact")
            .eq("clinic_id", str(staff.clinic_id)).eq("status", "no_show")
            .gte("start_at", start.isoformat()).lt("start_at", end.isoformat())
        )
        if staff.role == "doctor" and staff.doctor_id:
            doctor_id = str(staff.doctor_id)
            completed = completed.eq("dentist_id", doctor_id)
            cancelled = cancelled.eq("dentist_id", doctor_id)
            no_shows = no_shows.eq("dentist_id", doctor_id)
        metrics.completed_today = _count(completed)
        metrics.todays_cancellations = _count(cancelled)
        metrics.no_shows_today = _count(no_shows)

        appointment_ids_query = (
            client.table("appointments").select("id")
            .eq("clinic_id", str(staff.clinic_id))
            .gte("start_at", start.isoformat()).lt("start_at", end.isoformat())
        )
        if staff.role == "doctor" and staff.doctor_id:
            appointment_ids_query = appointment_ids_query.eq("dentist_id", str(staff.doctor_id))
        appointment_ids = [row["id"] for row in appointment_ids_query.execute().data or []]
        if appointment_ids:
            metrics.waiting_patients = _count(
                client.table("appointment_visits").select("id", count="exact")
                .in_("appointment_id", appointment_ids).eq("queue_status", "waiting")
            )
            metrics.in_progress_patients = _count(
                client.table("appointment_visits").select("id", count="exact")
                .in_("appointment_id", appointment_ids).eq("queue_status", "in_progress")
            )

    if "payment.verify" in staff.permissions:
        metrics.pending_payments = _count(
            client.table("payments").select("id", count="exact")
            .eq("clinic_id", str(staff.clinic_id)).eq("status", "submitted")
        )
    return metrics


@router.get("/overview", response_model=OverviewResponse)
async def overview(staff: AuthenticatedStaff) -> OverviewResponse:
    return await run_in_threadpool(_load_overview, staff)
