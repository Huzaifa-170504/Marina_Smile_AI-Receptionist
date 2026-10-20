from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    settings = get_settings()
    return create_client(
        str(settings.supabase_url),
        settings.supabase_secret_key.get_secret_value(),
    )
