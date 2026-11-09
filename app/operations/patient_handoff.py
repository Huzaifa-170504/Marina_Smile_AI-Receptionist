from __future__ import annotations

from app.database.supabase import get_supabase_client


def create_reception_handoff(
    clinic_id: str,
    contact_id: str,
    summary: str,
) -> None:
    """Create a staff-facing handoff for a patient requesting human help."""
    get_supabase_client().table("handoffs").insert(
        {
            "clinic_id": clinic_id,
            "contact_id": contact_id,
            "reason": "Patient requested reception assistance",
            "summary": summary,
            "priority": "normal",
            "status": "open",
        }
    ).execute()
