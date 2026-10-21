from __future__ import annotations

from datetime import datetime, time
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class OperationalIntent(str, Enum):
    KNOWLEDGE = "KNOWLEDGE"
    CHECK_AVAILABILITY = "CHECK_AVAILABILITY"
    FIND_SLOTS = "FIND_SLOTS"
    BOOK = "BOOK"
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"
    UNKNOWN = "UNKNOWN"


class TimePeriod(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    ANYTIME = "anytime"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    DENTIST_NOT_FOUND = "DENTIST_NOT_FOUND"
    SERVICE_NOT_FOUND = "SERVICE_NOT_FOUND"
    CALENDAR_UNAVAILABLE = "CALENDAR_UNAVAILABLE"
    UNSUPPORTED_INTENT = "UNSUPPORTED_INTENT"


class ParsedOperationalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: OperationalIntent
    appointment_id: str | None = None
    dentist_text: str | None = None
    service_text: str | None = None
    any_dentist: bool = False
    requested_date_text: str | None = None
    requested_time_text: str | None = None
    period: TimePeriod | None = None
    after_time: time | None = None
    before_time: time | None = None
    wants_earliest: bool = False
    wants_next_available: bool = False
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_reason: str | None = None
    requested_start: datetime | None = None
    search_window_start: datetime | None = None
    search_window_end: datetime | None = None


class Clinic(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    timezone: str
    google_calendar_id: str | None = None


class Dentist(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    clinic_id: str
    name: str
    specialty: str | None = None


class Service(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    clinic_id: str
    name: str
    duration_minutes: int | None = None


class DentistSchedule(BaseModel):
    model_config = ConfigDict(frozen=True)

    dentist_id: str
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time


class BusyInterval(BaseModel):
    model_config = ConfigDict(frozen=True)

    dentist_id: str
    start_at: datetime
    end_at: datetime


class AvailableSlot(BaseModel):
    model_config = ConfigDict(frozen=True)

    dentist_id: str
    dentist_name: str
    service_id: str | None = None
    service_name: str | None = None
    start_at: datetime
    end_at: datetime
    duration_minutes: int
    calendar_verified: bool


class AvailabilitySearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: AvailabilityStatus
    slots: list[AvailableSlot] = Field(default_factory=list)
    resolved_dentist: str | None = None
    resolved_service: str | None = None
    reason: str | None = None
    parsed_request: ParsedOperationalRequest | None = None
