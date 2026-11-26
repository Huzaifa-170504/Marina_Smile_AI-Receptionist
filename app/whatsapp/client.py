from __future__ import annotations

import httpx

from app.core.config import get_settings


async def send_text(recipient: str, text: str) -> None:
    settings = get_settings()
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id or not settings.whatsapp_graph_api_version:
        raise RuntimeError("WhatsApp Cloud API is not configured")
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_graph_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.whatsapp_access_token.get_secret_value()}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            },
        )
        response.raise_for_status()


async def send_notification(recipient: str, text: str) -> None:
    """Use an approved WhatsApp template for notifications outside the 24h window."""
    settings = get_settings()
    if not settings.whatsapp_notification_template:
        raise RuntimeError("WHATSAPP_NOTIFICATION_TEMPLATE is required for scheduled WhatsApp notifications")
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id or not settings.whatsapp_graph_api_version:
        raise RuntimeError("WhatsApp Cloud API is not configured")
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_graph_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.whatsapp_access_token.get_secret_value()}"},
            json={
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "template",
                "template": {
                    "name": settings.whatsapp_notification_template,
                    "language": {"code": settings.whatsapp_template_language},
                    "components": [{
                        "type": "body",
                        "parameters": [{"type": "text", "text": text}],
                    }],
                },
            },
        )
        response.raise_for_status()
