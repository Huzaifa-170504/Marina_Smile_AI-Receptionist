from __future__ import annotations

import hmac
import re

from fastapi import Header, HTTPException, status

from app.core.config import get_settings
from app.database.supabase import get_supabase_client


_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def require_demo_access(
    demo_token: str | None = Header(default=None, alias="X-Demo-Token"),
) -> None:
    settings = get_settings()
    if not settings.demo_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The public demo is not enabled.",
        )
    expected = settings.demo_api_token
    if expected is None or demo_token is None or not hmac.compare_digest(
        demo_token,
        expected.get_secret_value(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Demo access is not authorized.",
        )


def consume_demo_quota(*, fingerprint_hash: str, query_type: str) -> None:
    if not _FINGERPRINT_PATTERN.fullmatch(fingerprint_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Demo visitor identity is invalid.",
        )
    settings = get_settings()
    response = get_supabase_client().rpc(
        "consume_demo_query_quota",
        {
            "p_clinic_id": str(settings.default_clinic_id),
            "p_fingerprint_hash": fingerprint_hash,
            "p_query_type": query_type,
            "p_limit": settings.demo_query_limit,
            "p_window_seconds": settings.demo_query_window_seconds,
        },
    ).execute()
    if response.data is not True:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demo query limit reached. Please try again later.",
        )
