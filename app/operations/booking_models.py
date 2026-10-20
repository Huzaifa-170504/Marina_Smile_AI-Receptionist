from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class BookingStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    SLOT_TAKEN = "SLOT_TAKEN"
    CONTACT_NOT_FOUND = "CONTACT_NOT_FOUND"
    DENTIST_NOT_FOUND = "DENTIST_NOT_FOUND"
    SERVICE_NOT_FOUND = "SERVICE_NOT_FOUND"
    DENTIST_SERVICE_MISMATCH = "DENTIST_SERVICE_MISMATCH"
    OUTSIDE_WORKING_HOURS = "OUTSIDE_WORKING_HOURS"
    CALENDAR_CONFLICT = "CALENDAR_CONFLICT"
    CALENDAR_ERROR = "CALENDAR_ERROR"
    INVALID_TIME = "INVALID_TIME"
    NEEDS_SERVICE = "NEEDS_SERVICE"
    CONSISTENCY_ERROR = "CONSISTENCY_ERROR"
    ERROR = "ERROR"


class Contact(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    clinic_id: str
    name: str | None = None


class AppointmentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    clinic_id: str
    contact_id: str
    dentist_id: str
    service_id: str
    start_at: datetime
    end_at: datetime
    status: str
    google_calendar_event_id: str | None = None
    booking_request_id: str | None = None


class BookingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: BookingStatus
    appointment_id: str | None = None
    calendar_event_id: str | None = None
    clinic_id: str
    contact_id: str | None = None
    dentist_id: str | None = None
    dentist_name: str | None = None
    service_id: str | None = None
    service_name: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    reason: str | None = None
