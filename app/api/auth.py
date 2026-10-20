from dataclasses import dataclass
from typing import Annotated, Callable
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.database.supabase import get_supabase_client


KNOWN_PERMISSIONS = frozenset(
    {
        "appointment.view_all", "appointment.view_own", "appointment.create",
        "appointment.reschedule_any", "appointment.reschedule_own",
        "appointment.cancel_any", "appointment.cancel_own", "queue.view_all",
        "queue.view_own", "queue.update_all", "queue.update_own",
        "schedule.manage_any", "schedule.manage_own", "payment.verify",
        "payment.view_proof", "patient.view_all", "patient.view_assigned",
        "doctor.manage", "service.manage", "staff.manage", "clinic.manage",
    }
)

ROLE_DEFAULTS: dict[str, frozenset[str]] = {
    "owner": KNOWN_PERMISSIONS,
    "doctor": frozenset(
        {
            "appointment.view_own", "appointment.reschedule_own",
            "appointment.cancel_own", "queue.view_own", "queue.update_own",
            "schedule.manage_own", "patient.view_assigned",
        }
    ),
    "receptionist": frozenset(
        {
            "appointment.view_all", "appointment.create", "appointment.reschedule_any",
            "appointment.cancel_any", "queue.view_all", "queue.update_all",
            "schedule.manage_any", "patient.view_all",
        }
    ),
    "payment_verifier": frozenset({"payment.verify", "payment.view_proof"}),
}

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    access_token: str


@dataclass(frozen=True)
class CurrentStaff:
    id: UUID
    clinic_id: UUID
    auth_user_id: UUID
    doctor_id: UUID | None
    full_name: str
    email: str
    role: str
    active: bool
    permissions: frozenset[str]
    overrides: tuple[dict[str, object], ...]
    access_token: str


async def get_authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    settings = get_settings()
    headers = {
        "apikey": settings.supabase_secret_key.get_secret_value(),
        "Authorization": f"Bearer {credentials.credentials}",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                f"{str(settings.supabase_url).rstrip('/')}/auth/v1/user",
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is unavailable",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid or expired")
    try:
        user_id = UUID(response.json()["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication response") from exc
    return AuthenticatedUser(id=user_id, access_token=credentials.credentials)


def _load_staff(auth_user: AuthenticatedUser) -> CurrentStaff:
    client = get_supabase_client()
    staff_response = (
        client.table("staff_users")
        .select("id,clinic_id,auth_user_id,doctor_id,full_name,email,role,active")
        .eq("auth_user_id", str(auth_user.id))
        .limit(1)
        .execute()
    )
    if not staff_response.data:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No staff profile is linked to this account")
    row = staff_response.data[0]
    if not row.get("active"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff account is inactive")
    role = str(row["role"])
    if role not in ROLE_DEFAULTS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff role is not supported")

    override_response = (
        client.table("staff_permission_overrides")
        .select("permission,allowed")
        .eq("staff_user_id", str(row["id"]))
        .execute()
    )
    overrides = tuple(override_response.data or [])
    permissions = set(ROLE_DEFAULTS[role])
    for override in overrides:
        permission = str(override["permission"])
        if override["allowed"]:
            permissions.add(permission)
        else:
            permissions.discard(permission)

    return CurrentStaff(
        id=UUID(row["id"]),
        clinic_id=UUID(row["clinic_id"]),
        auth_user_id=UUID(row["auth_user_id"]),
        doctor_id=UUID(row["doctor_id"]) if row.get("doctor_id") else None,
        full_name=str(row["full_name"]),
        email=str(row["email"]),
        role=role,
        active=True,
        permissions=frozenset(permissions),
        overrides=overrides,
        access_token=auth_user.access_token,
    )


async def get_current_staff(
    auth_user: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
) -> CurrentStaff:
    return await run_in_threadpool(_load_staff, auth_user)


def require_permission(permission: str) -> Callable[..., CurrentStaff]:
    async def dependency(
        staff: Annotated[CurrentStaff, Depends(get_current_staff)],
    ) -> CurrentStaff:
        if permission not in staff.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
        return staff

    return dependency


def require_any_permission(*permissions: str) -> Callable[..., CurrentStaff]:
    async def dependency(
        staff: Annotated[CurrentStaff, Depends(get_current_staff)],
    ) -> CurrentStaff:
        if not staff.permissions.intersection(permissions):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
        return staff

    return dependency


AuthenticatedStaff = Annotated[CurrentStaff, Depends(get_current_staff)]
