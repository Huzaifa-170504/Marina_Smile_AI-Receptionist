import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from app.api.auth import CurrentStaff
from app.api.models import DoctorOnboardingRequest, StaffInviteRequest
from app.core.config import get_settings
from app.database.supabase import get_supabase_client
from app.integrations.supabase_auth import (
    AuthAccount,
    AuthOperationError,
    SupabaseAuthAdminService,
)


logger = logging.getLogger(__name__)
AccountSetupStatus = Literal["invited", "account_configured", "invitation_problem"]


class OnboardingError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OnboardingResult:
    staff_id: UUID
    email: str
    doctor_id: UUID | None
    invitation_sent: bool
    account_setup_status: AccountSetupStatus
    recovered: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "staff_id": str(self.staff_id),
            "email": self.email,
            "doctor_id": str(self.doctor_id) if self.doctor_id else None,
            "invitation_sent": self.invitation_sent,
            "account_setup_status": self.account_setup_status,
            "recovered": self.recovered,
        }


class StaffOnboardingService:
    def __init__(self, auth: SupabaseAuthAdminService | None = None) -> None:
        self.client = get_supabase_client()
        self.auth = auth or SupabaseAuthAdminService()
        self.dashboard_url = get_settings().dashboard_url

    def list_staff(self, actor: CurrentStaff) -> list[dict[str, object]]:
        response = (
            self.client.table("staff_users")
            .select(
                "id,clinic_id,auth_user_id,doctor_id,full_name,email,role,active,"
                "staff_permission_overrides(permission,allowed)"
            )
            .eq("clinic_id", str(actor.clinic_id))
            .order("full_name")
            .execute()
        )
        accounts = {account.id: account for account in self.auth.list_accounts()}
        rows: list[dict[str, object]] = []
        for row in response.data or []:
            account = accounts.get(UUID(str(row["auth_user_id"])))
            rows.append(
                {
                    **row,
                    "access_status": "active" if row["active"] else "inactive",
                    "account_setup_status": self._setup_status(account),
                }
            )
        return rows

    def invite_staff(self, actor: CurrentStaff, invitation: StaffInviteRequest) -> OnboardingResult:
        existing_staff = self._staff_by_email(actor.clinic_id, invitation.email)
        if existing_staff:
            if existing_staff["role"] != invitation.role or existing_staff.get("doctor_id"):
                raise OnboardingError("This email is already linked to a different staff account", status_code=409)
            account = self.auth.get_account(UUID(str(existing_staff["auth_user_id"])))
            return OnboardingResult(
                staff_id=UUID(str(existing_staff["id"])),
                email=invitation.email,
                doctor_id=None,
                invitation_sent=False,
                account_setup_status=self._setup_status(account),
                recovered=True,
            )

        metadata = self._metadata(actor.clinic_id, invitation.role, None)
        account, created = self._resolve_or_invite_account(
            invitation.email,
            invitation.full_name,
            metadata,
        )
        try:
            inserted = (
                self.client.table("staff_users")
                .insert(
                    {
                        "clinic_id": str(actor.clinic_id),
                        "auth_user_id": str(account.id),
                        "doctor_id": None,
                        "full_name": invitation.full_name,
                        "email": invitation.email,
                        "role": invitation.role,
                        "active": invitation.active,
                    }
                )
                .execute()
            )
            staff_id = UUID(str(inserted.data[0]["id"]))
        except Exception as exc:
            self._compensate_auth(account.id, created, actor.clinic_id, None)
            raise OnboardingError("Staff invitation could not be linked; it is safe to retry") from exc

        invitation_sent = created
        if not created and not account.account_configured:
            self.auth.send_recovery(account.email, self.dashboard_url)
            invitation_sent = True
        self._log("staff_invited", actor, staff_id, None)
        return OnboardingResult(
            staff_id=staff_id,
            email=invitation.email,
            doctor_id=None,
            invitation_sent=invitation_sent,
            account_setup_status=self._setup_status(account),
            recovered=not created,
        )

    def onboard_doctor(
        self,
        actor: CurrentStaff,
        request: DoctorOnboardingRequest,
    ) -> OnboardingResult:
        existing_staff = self._staff_by_email(actor.clinic_id, request.email)
        if existing_staff:
            if existing_staff["role"] != "doctor" or not existing_staff.get("doctor_id"):
                raise OnboardingError("This email is already linked to a different staff account", status_code=409)
            account = self.auth.get_account(UUID(str(existing_staff["auth_user_id"])))
            return OnboardingResult(
                staff_id=UUID(str(existing_staff["id"])),
                email=request.email,
                doctor_id=UUID(str(existing_staff["doctor_id"])),
                invitation_sent=False,
                account_setup_status=self._setup_status(account),
                recovered=True,
            )

        self._validate_services(actor.clinic_id, request.service_ids)
        existing_account = self.auth.find_by_email(request.email)
        doctor_id = self._recoverable_doctor_id(actor.clinic_id, request, existing_account)
        try:
            self._prepare_inactive_dentist(actor.clinic_id, doctor_id, request)
        except Exception as exc:
            raise OnboardingError("Doctor record could not be prepared safely; no invitation was sent") from exc

        metadata = self._metadata(actor.clinic_id, "doctor", doctor_id)
        try:
            self._replace_doctor_configuration(actor.clinic_id, doctor_id, request)
            account, created = self._resolve_or_invite_account(
                request.email,
                request.full_name,
                metadata,
            )
            inserted = (
                self.client.table("staff_users")
                .insert(
                    {
                        "clinic_id": str(actor.clinic_id),
                        "auth_user_id": str(account.id),
                        "doctor_id": str(doctor_id),
                        "full_name": request.full_name,
                        "email": request.email,
                        "role": "doctor",
                        "active": request.active,
                    }
                )
                .execute()
            )
            staff_id = UUID(str(inserted.data[0]["id"]))
            self._finalize_dentist(actor.clinic_id, doctor_id, request.active)
        except Exception as exc:
            account_id = locals().get("account").id if "account" in locals() else None
            was_created = bool(locals().get("created", False))
            linked_staff = self._staff_by_email(actor.clinic_id, request.email)
            if linked_staff:
                self._mark_staff_failed(actor.clinic_id, UUID(str(linked_staff["id"])))
            if account_id:
                self._compensate_auth(account_id, was_created, actor.clinic_id, doctor_id)
            self._mark_doctor_failed(actor.clinic_id, doctor_id)
            if isinstance(exc, OnboardingError):
                raise
            if isinstance(exc, AuthOperationError):
                raise OnboardingError("Doctor invitation failed; the doctor remains inactive and can be retried") from exc
            raise OnboardingError("Doctor onboarding failed; the doctor remains inactive and can be retried") from exc

        invitation_sent = created
        if not created and not account.account_configured:
            self.auth.send_recovery(account.email, self.dashboard_url)
            invitation_sent = True
        self._log("doctor_invited", actor, staff_id, doctor_id)
        return OnboardingResult(
            staff_id=staff_id,
            email=request.email,
            doctor_id=doctor_id,
            invitation_sent=invitation_sent,
            account_setup_status=self._setup_status(account),
            recovered=not created or doctor_id != request.onboarding_id,
        )

    def resend_invitation(self, actor: CurrentStaff, staff_id: UUID) -> dict[str, str]:
        staff = self._staff_by_id(actor.clinic_id, staff_id)
        if not staff:
            raise OnboardingError("Staff member not found", status_code=404)
        account = self.auth.get_account(UUID(str(staff["auth_user_id"])))
        if account is None:
            raise OnboardingError(
                "The staff profile has no matching Auth account. Invitation recovery is required.",
                status_code=409,
            )
        self.auth.send_recovery(account.email, self.dashboard_url)
        self._log("invitation_resent", actor, staff_id, UUID(str(staff["doctor_id"])) if staff.get("doctor_id") else None)
        return {"account_setup_status": self._setup_status(account), "message": "Invitation email sent"}

    def _resolve_or_invite_account(
        self,
        email: str,
        full_name: str,
        expected_metadata: dict[str, str],
    ) -> tuple[AuthAccount, bool]:
        existing = self.auth.find_by_email(email)
        if existing:
            if existing.app_metadata.get("marina_smile_onboarding") != expected_metadata:
                raise OnboardingError(
                    "An unlinked Auth account already uses this email and requires manual review",
                    status_code=409,
                )
            return existing, False
        account = self.auth.invite(
            email=email,
            full_name=full_name,
            redirect_to=self.dashboard_url,
            onboarding_metadata=expected_metadata,
        )
        return account, True

    def _recoverable_doctor_id(
        self,
        clinic_id: UUID,
        request: DoctorOnboardingRequest,
        account: AuthAccount | None,
    ) -> UUID:
        if account:
            metadata = account.app_metadata.get("marina_smile_onboarding")
            if not isinstance(metadata, dict):
                raise OnboardingError("Existing Auth account requires manual review", status_code=409)
            if metadata.get("clinic_id") != str(clinic_id) or metadata.get("role") != "doctor":
                raise OnboardingError("Existing Auth account belongs to another onboarding context", status_code=409)
            try:
                return UUID(str(metadata["doctor_id"]))
            except (KeyError, ValueError) as exc:
                raise OnboardingError("Existing Auth account has invalid onboarding metadata", status_code=409) from exc

        direct = (
            self.client.table("dentists")
            .select("id,name,active,deactivation_reason")
            .eq("clinic_id", str(clinic_id))
            .eq("id", str(request.onboarding_id))
            .limit(1)
            .execute()
        )
        if direct.data:
            return request.onboarding_id

        named = (
            self.client.table("dentists")
            .select("id,name,active,deactivation_reason")
            .eq("clinic_id", str(clinic_id))
            .eq("name", request.full_name)
            .eq("active", False)
            .limit(1)
            .execute()
        )
        if named.data and str(named.data[0].get("deactivation_reason") or "").startswith("Onboarding"):
            candidate = UUID(str(named.data[0]["id"]))
            linked = (
                self.client.table("staff_users")
                .select("id")
                .eq("clinic_id", str(clinic_id))
                .eq("doctor_id", str(candidate))
                .limit(1)
                .execute()
            )
            if not linked.data:
                return candidate
        return request.onboarding_id

    def _prepare_inactive_dentist(
        self,
        clinic_id: UUID,
        doctor_id: UUID,
        request: DoctorOnboardingRequest,
    ) -> None:
        existing = (
            self.client.table("dentists")
            .select("id")
            .eq("clinic_id", str(clinic_id))
            .eq("id", str(doctor_id))
            .limit(1)
            .execute()
        )
        payload = {
            "name": request.full_name,
            "specialty": request.specialty,
            "bio": request.bio,
            "active": False,
            "deactivated_at": datetime.now(timezone.utc).isoformat(),
            "deactivated_by_staff_id": None,
            "deactivation_reason": "Onboarding pending",
        }
        if existing.data:
            (
                self.client.table("dentists")
                .update(payload)
                .eq("clinic_id", str(clinic_id))
                .eq("id", str(doctor_id))
                .execute()
            )
        else:
            self.client.table("dentists").insert(
                {"id": str(doctor_id), "clinic_id": str(clinic_id), **payload}
            ).execute()

    def _replace_doctor_configuration(
        self,
        clinic_id: UUID,
        doctor_id: UUID,
        request: DoctorOnboardingRequest,
    ) -> None:
        (
            self.client.table("dentist_services")
            .delete()
            .eq("clinic_id", str(clinic_id))
            .eq("dentist_id", str(doctor_id))
            .execute()
        )
        (
            self.client.table("dentist_schedules")
            .delete()
            .eq("clinic_id", str(clinic_id))
            .eq("dentist_id", str(doctor_id))
            .execute()
        )
        self.client.table("dentist_services").insert(
            [
                {
                    "clinic_id": str(clinic_id),
                    "dentist_id": str(doctor_id),
                    "service_id": str(service_id),
                }
                for service_id in request.service_ids
            ]
        ).execute()
        self.client.table("dentist_schedules").insert(
            [
                {
                    "clinic_id": str(clinic_id),
                    "dentist_id": str(doctor_id),
                    "weekday": entry.weekday,
                    "start_time": entry.start_time.isoformat(),
                    "end_time": entry.end_time.isoformat(),
                    "active": True,
                }
                for entry in request.weekly_schedule
            ]
        ).execute()

    def _validate_services(self, clinic_id: UUID, service_ids: list[UUID]) -> None:
        response = (
            self.client.table("services")
            .select("id")
            .eq("clinic_id", str(clinic_id))
            .eq("active", True)
            .in_("id", [str(service_id) for service_id in service_ids])
            .execute()
        )
        if {UUID(str(row["id"])) for row in response.data or []} != set(service_ids):
            raise OnboardingError("One or more selected services are invalid or inactive", status_code=422)

    def _finalize_dentist(self, clinic_id: UUID, doctor_id: UUID, active: bool) -> None:
        payload: dict[str, object] = {"active": active}
        if active:
            payload.update(
                {"deactivated_at": None, "deactivated_by_staff_id": None, "deactivation_reason": None}
            )
        else:
            payload.update(
                {
                    "deactivated_at": datetime.now(timezone.utc).isoformat(),
                    "deactivated_by_staff_id": None,
                    "deactivation_reason": "Inactive at onboarding",
                }
            )
        response = (
            self.client.table("dentists")
            .update(payload)
            .eq("clinic_id", str(clinic_id))
            .eq("id", str(doctor_id))
            .execute()
        )
        if not response.data:
            raise OnboardingError("Doctor activation state could not be finalized; doctor remains inactive")

    def _mark_doctor_failed(self, clinic_id: UUID, doctor_id: UUID) -> None:
        try:
            (
                self.client.table("dentists")
                .update(
                    {
                        "active": False,
                        "deactivated_at": datetime.now(timezone.utc).isoformat(),
                        "deactivated_by_staff_id": None,
                        "deactivation_reason": "Onboarding failed; safe retry required",
                    }
                )
                .eq("clinic_id", str(clinic_id))
                .eq("id", str(doctor_id))
                .execute()
            )
        except Exception:
            logger.exception(
                "doctor_onboarding_compensation_failed",
                extra={"clinic_id": str(clinic_id), "doctor_id": str(doctor_id)},
            )

    def _mark_staff_failed(self, clinic_id: UUID, staff_id: UUID) -> None:
        try:
            (
                self.client.table("staff_users")
                .update({"active": False})
                .eq("clinic_id", str(clinic_id))
                .eq("id", str(staff_id))
                .execute()
            )
        except Exception:
            logger.exception(
                "staff_onboarding_local_compensation_failed",
                extra={"clinic_id": str(clinic_id), "target_staff_id": str(staff_id)},
            )

    def _compensate_auth(
        self,
        auth_user_id: UUID,
        created: bool,
        clinic_id: UUID,
        doctor_id: UUID | None,
    ) -> None:
        if not created:
            return
        try:
            self.auth.delete_account(auth_user_id)
        except AuthOperationError:
            logger.exception(
                "staff_onboarding_auth_compensation_failed",
                extra={
                    "clinic_id": str(clinic_id),
                    "doctor_id": str(doctor_id) if doctor_id else None,
                    "auth_user_id": str(auth_user_id),
                },
            )

    def _staff_by_email(self, clinic_id: UUID, email: str) -> dict[str, object] | None:
        response = (
            self.client.table("staff_users")
            .select("id,auth_user_id,doctor_id,email,role,active")
            .eq("clinic_id", str(clinic_id))
            .eq("email", email)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    def _staff_by_id(self, clinic_id: UUID, staff_id: UUID) -> dict[str, object] | None:
        response = (
            self.client.table("staff_users")
            .select("id,auth_user_id,doctor_id,email,role,active")
            .eq("clinic_id", str(clinic_id))
            .eq("id", str(staff_id))
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    @staticmethod
    def _metadata(clinic_id: UUID, role: str, doctor_id: UUID | None) -> dict[str, str]:
        metadata = {"clinic_id": str(clinic_id), "role": role}
        if doctor_id:
            metadata["doctor_id"] = str(doctor_id)
        return metadata

    @staticmethod
    def _setup_status(account: AuthAccount | None) -> AccountSetupStatus:
        if account is None or (not account.account_configured and account.invited_at is None):
            return "invitation_problem"
        return "account_configured" if account.account_configured else "invited"

    @staticmethod
    def _log(
        action: str,
        actor: CurrentStaff,
        target_staff_id: UUID,
        doctor_id: UUID | None,
    ) -> None:
        logger.info(
            action,
            extra={
                "clinic_id": str(actor.clinic_id),
                "actor_staff_id": str(actor.id),
                "target_staff_id": str(target_staff_id),
                "doctor_id": str(doctor_id) if doctor_id else None,
            },
        )
