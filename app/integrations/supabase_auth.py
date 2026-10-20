import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.database.supabase import get_supabase_client


logger = logging.getLogger(__name__)


class AuthOperationError(RuntimeError):
    """Safe boundary error for Supabase Auth administrative operations."""


@dataclass(frozen=True)
class AuthAccount:
    id: UUID
    email: str
    invited_at: datetime | None
    last_sign_in_at: datetime | None
    app_metadata: dict[str, Any]

    @property
    def account_configured(self) -> bool:
        return self.last_sign_in_at is not None


class SupabaseAuthAdminService:
    """Keeps secret-key Auth operations out of request handlers."""

    def __init__(self) -> None:
        self._client = get_supabase_client()

    def list_accounts(self) -> list[AuthAccount]:
        accounts: list[AuthAccount] = []
        page = 1
        per_page = 1000
        try:
            while True:
                users = self._client.auth.admin.list_users(page=page, per_page=per_page)
                accounts.extend(self._to_account(user) for user in users)
                if len(users) < per_page:
                    break
                page += 1
        except Exception as exc:
            raise AuthOperationError("Supabase Auth accounts could not be loaded") from exc
        return accounts

    def find_by_email(self, email: str) -> AuthAccount | None:
        normalized = email.strip().casefold()
        return next(
            (account for account in self.list_accounts() if account.email.casefold() == normalized),
            None,
        )

    def get_account(self, auth_user_id: UUID) -> AuthAccount | None:
        try:
            response = self._client.auth.admin.get_user_by_id(str(auth_user_id))
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return None
            raise AuthOperationError("Supabase Auth account could not be loaded") from exc
        return self._to_account(response.user) if response.user else None

    def invite(
        self,
        *,
        email: str,
        full_name: str,
        redirect_to: str,
        onboarding_metadata: dict[str, str],
    ) -> AuthAccount:
        auth_user_id: UUID | None = None
        try:
            response = self._client.auth.admin.invite_user_by_email(
                email,
                {"redirect_to": redirect_to, "data": {"full_name": full_name}},
            )
            if response.user is None:
                raise AuthOperationError("Supabase Auth did not return an invited account")
            auth_user_id = UUID(str(response.user.id))
            app_metadata = dict(response.user.app_metadata or {})
            app_metadata["marina_smile_onboarding"] = onboarding_metadata
            updated = self._client.auth.admin.update_user_by_id(
                str(auth_user_id),
                {"app_metadata": app_metadata},
            )
            if updated.user is None:
                raise AuthOperationError("Supabase Auth did not persist onboarding metadata")
            return self._to_account(updated.user)
        except AuthOperationError:
            if auth_user_id is not None:
                self._best_effort_delete(auth_user_id)
            raise
        except Exception as exc:
            if auth_user_id is not None:
                self._best_effort_delete(auth_user_id)
            raise AuthOperationError("Supabase Auth invitation failed") from exc

    def send_recovery(self, email: str, redirect_to: str) -> None:
        try:
            self._client.auth.reset_password_for_email(
                email,
                {"redirect_to": redirect_to},
            )
        except Exception as exc:
            raise AuthOperationError("Supabase Auth could not send a recovery email") from exc

    def delete_account(self, auth_user_id: UUID) -> None:
        try:
            self._client.auth.admin.delete_user(str(auth_user_id))
        except Exception as exc:
            raise AuthOperationError("Supabase Auth compensation failed") from exc

    def _best_effort_delete(self, auth_user_id: UUID) -> None:
        try:
            self._client.auth.admin.delete_user(str(auth_user_id))
        except Exception:
            logger.exception(
                "auth_invitation_compensation_failed",
                extra={"auth_user_id": str(auth_user_id)},
            )

    @staticmethod
    def _to_account(user: Any) -> AuthAccount:
        return AuthAccount(
            id=UUID(str(user.id)),
            email=str(user.email or ""),
            invited_at=user.invited_at,
            last_sign_in_at=user.last_sign_in_at,
            app_metadata=dict(user.app_metadata or {}),
        )
