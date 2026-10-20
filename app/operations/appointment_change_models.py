from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.operations.booking_models import AppointmentRecord


class AppointmentActorType(str, Enum):
    PATIENT = "patient"
    DOCTOR = "doctor"
    OWNER = "owner"
    RECEPTIONIST = "receptionist"
    SYSTEM = "system"


class AppointmentActor(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor_type: AppointmentActorType
    actor_id: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)


class RescheduleStatus(str, Enum):
    RESCHEDULED = "RESCHEDULED"
    APPOINTMENT_NOT_FOUND = "APPOINTMENT_NOT_FOUND"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    NOT_RESCHEDULABLE = "NOT_RESCHEDULABLE"
    DENTIST_NOT_FOUND = "DENTIST_NOT_FOUND"
    DENTIST_SERVICE_MISMATCH = "DENTIST_SERVICE_MISMATCH"
    CHANGE_SERVICE_UNSUPPORTED = "CHANGE_SERVICE_UNSUPPORTED"
    SLOT_TAKEN = "SLOT_TAKEN"
    OUTSIDE_WORKING_HOURS = "OUTSIDE_WORKING_HOURS"
    CALENDAR_CONFLICT = "CALENDAR_CONFLICT"
    CALENDAR_ERROR = "CALENDAR_ERROR"
    INVALID_TIME = "INVALID_TIME"
    CONSISTENCY_ERROR = "CONSISTENCY_ERROR"
    ERROR = "ERROR"


class CancellationStatus(str, Enum):
    CANCELLED = "CANCELLED"
    ALREADY_CANCELLED = "ALREADY_CANCELLED"
    APPOINTMENT_NOT_FOUND = "APPOINTMENT_NOT_FOUND"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    NOT_CANCELLABLE = "NOT_CANCELLABLE"
    CALENDAR_ERROR = "CALENDAR_ERROR"
    CONSISTENCY_ERROR = "CONSISTENCY_ERROR"
    ERROR = "ERROR"


class AppointmentLookupStatus(str, Enum):
    FOUND = "FOUND"
    APPOINTMENT_NOT_FOUND = "APPOINTMENT_NOT_FOUND"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


class AppointmentLookupResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: AppointmentLookupStatus
    appointment: AppointmentRecord | None = None
    matches: list[AppointmentRecord] = Field(default_factory=list)
    reason: str | None = None


class RescheduleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: RescheduleStatus
    clinic_id: str
    contact_id: str
    actor: AppointmentActor
    appointment_id: str | None = None
    dentist_id: str | None = None
    dentist_name: str | None = None
    service_id: str | None = None
    service_name: str | None = None
    old_start_at: datetime | None = None
    old_end_at: datetime | None = None
    new_start_at: datetime | None = None
    new_end_at: datetime | None = None
    calendar_event_id: str | None = None
    compensation_succeeded: bool | None = None
    reason: str | None = None


class CancellationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CancellationStatus
    clinic_id: str
    contact_id: str
    actor: AppointmentActor
    appointment_id: str | None = None
    dentist_id: str | None = None
    dentist_name: str | None = None
    service_id: str | None = None
    service_name: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    calendar_event_id: str | None = None
    compensation_succeeded: bool | None = None
    reason: str | None = None
