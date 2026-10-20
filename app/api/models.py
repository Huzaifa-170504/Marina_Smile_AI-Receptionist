from datetime import datetime, time
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


StaffRole = Literal["owner", "doctor", "receptionist", "payment_verifier"]
QueueStatus = Literal[
    "scheduled", "checked_in", "waiting", "in_progress", "completed", "no_show"
]


class PermissionOverrideModel(BaseModel):
    permission: str
    allowed: bool


class StaffProfileResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    auth_user_id: UUID
    doctor_id: UUID | None
    full_name: str
    email: str
    role: StaffRole
    active: bool
    permissions: list[str]
    overrides: list[PermissionOverrideModel]


class OverviewResponse(BaseModel):
    todays_appointments: int = 0
    waiting_patients: int = 0
    in_progress_patients: int = 0
    completed_today: int = 0
    upcoming_appointments: int = 0
    pending_payments: int = 0
    todays_cancellations: int = 0
    no_shows_today: int = 0


class QueueTransitionRequest(BaseModel):
    expected_status: QueueStatus
    target_status: QueueStatus


class PaymentApprovalRequest(BaseModel):
    amount_verified: float = Field(ge=0)


class PaymentRejectionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason is required")
        return cleaned


class PermissionChangeRequest(BaseModel):
    permission: str = Field(min_length=1, max_length=100)
    allowed: bool | None


class DoctorDeactivationRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_deactivation_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason is required")
        return cleaned


class StaffInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=3, max_length=320)
    role: Literal["owner", "receptionist", "payment_verifier"]
    active: bool = True

    @field_validator("full_name", "email")
    @classmethod
    def clean_staff_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value is required")
        return cleaned

    @field_validator("email")
    @classmethod
    def validate_staff_email(cls, value: str) -> str:
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("valid email is required")
        return value.lower()


class WeeklyScheduleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def validate_interval(self) -> "WeeklyScheduleEntry":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class DoctorOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    onboarding_id: UUID = Field(default_factory=uuid4)
    full_name: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=3, max_length=320)
    specialty: str | None = Field(default=None, max_length=200)
    bio: str | None = Field(default=None, max_length=2000)
    service_ids: list[UUID] = Field(min_length=1, max_length=100)
    weekly_schedule: list[WeeklyScheduleEntry] = Field(min_length=1, max_length=50)
    active: bool = True

    @field_validator("full_name", "email")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value is required")
        return cleaned

    @field_validator("specialty", "bio")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("valid email is required")
        return value.lower()

    @field_validator("service_ids")
    @classmethod
    def unique_services(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("service_ids must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_schedule_overlaps(self) -> "DoctorOnboardingRequest":
        by_day: dict[int, list[WeeklyScheduleEntry]] = {}
        for entry in self.weekly_schedule:
            by_day.setdefault(entry.weekday, []).append(entry)
        for entries in by_day.values():
            ordered = sorted(entries, key=lambda item: item.start_time)
            for previous, current in zip(ordered, ordered[1:]):
                if current.start_time < previous.end_time:
                    raise ValueError("weekly_schedule entries must not overlap")
        return self


class StaffRoleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["owner", "receptionist", "payment_verifier"]


class StaffActiveRequest(BaseModel):
    active: bool


class ScheduleOverrideRequest(BaseModel):
    dentist_id: UUID
    start_local: datetime
    end_local: datetime
    override_type: Literal["unavailable", "extra_availability"]
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("start_local", "end_local")
    @classmethod
    def require_naive_clinic_time(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            raise ValueError("use clinic-local time without an offset")
        return value


class HandoffStatusRequest(BaseModel):
    status: Literal["open", "in_progress", "resolved"]
    assigned_to_staff_id: UUID | None = None


class DemoQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["knowledge", "availability"]
    query: str = Field(min_length=3, max_length=300)

    @field_validator("query")
    @classmethod
    def clean_demo_query(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 3:
            raise ValueError("query must contain at least 3 characters")
        return cleaned


class DemoSourceResponse(BaseModel):
    filename: str
    page_number: int | None
    similarity: float


class DemoSlotResponse(BaseModel):
    dentist_name: str
    service_name: str | None
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    calendar_verified: bool


class DemoQueryResponse(BaseModel):
    mode: Literal["knowledge", "availability"]
    status: str
    answer: str
    sources: list[DemoSourceResponse] = Field(default_factory=list)
    slots: list[DemoSlotResponse] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)


class DemoStatusResponse(BaseModel):
    api: Literal["online"] = "online"
    supabase: Literal["online"] = "online"
    knowledge: Literal["ready", "empty"]
    active_dentists: int = Field(ge=0)
    active_services: int = Field(ge=0)
    ready_documents: int = Field(ge=0)
