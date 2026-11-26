from __future__ import annotations

from typing import Any

from supabase import Client

from app.telegram.privacy import PATIENT_SAFE_RPCS, PATIENT_SAFE_TABLES


WHATSAPP_SAFE_TABLES = PATIENT_SAFE_TABLES | frozenset({"conversations", "messages"})


class WhatsAppSafeSupabase:
    """Keep the WhatsApp webhook's service-role access on patient workflows."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def table(self, name: str):
        if name not in WHATSAPP_SAFE_TABLES:
            raise PermissionError(f"WhatsApp patient access to table '{name}' is blocked.")
        return self._client.table(name)

    def rpc(self, function_name: str, params: dict[str, Any] | None = None):
        if function_name not in PATIENT_SAFE_RPCS:
            raise PermissionError(f"WhatsApp patient access to RPC '{function_name}' is blocked.")
        return self._client.rpc(function_name, params or {})
