from typing import Any

import httpx
from fastapi import HTTPException, status

from app.api.auth import CurrentStaff
from app.core.config import get_settings


EXPECTED_RPC_ERRORS: dict[str, tuple[int, str]] = {
    "42501": (status.HTTP_403_FORBIDDEN, "Permission denied"),
    "P0002": (status.HTTP_404_NOT_FOUND, "Requested record was not found"),
    "40001": (status.HTTP_409_CONFLICT, "The record changed concurrently; refresh and try again"),
    "23P01": (status.HTTP_409_CONFLICT, "The requested slot is no longer available"),
    "23514": (status.HTTP_422_UNPROCESSABLE_CONTENT, "The requested transition is not allowed"),
    "22023": (status.HTTP_422_UNPROCESSABLE_CONTENT, "The request contains an invalid value"),
}


async def call_user_rpc(staff: CurrentStaff, function_name: str, payload: dict[str, Any]) -> Any:
    settings = get_settings()
    headers = {
        "apikey": settings.supabase_secret_key.get_secret_value(),
        "Authorization": f"Bearer {staff.access_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                f"{str(settings.supabase_url).rstrip('/')}/rest/v1/rpc/{function_name}",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database operation is temporarily unavailable",
        ) from exc

    if response.is_success:
        return response.json() if response.content else None

    try:
        error_code = str(response.json().get("code", ""))
    except (TypeError, ValueError):
        error_code = ""
    http_status, detail = EXPECTED_RPC_ERRORS.get(
        error_code,
        (status.HTTP_500_INTERNAL_SERVER_ERROR, "The operation could not be completed"),
    )
    raise HTTPException(status_code=http_status, detail=detail)
