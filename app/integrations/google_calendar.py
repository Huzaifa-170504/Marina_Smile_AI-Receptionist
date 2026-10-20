from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from app.operations.models import BusyInterval


_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"


class CalendarUnavailableError(RuntimeError):
    """Raised when Calendar conflicts cannot be verified."""


class CalendarWriteError(RuntimeError):
    """Raised when a Calendar event cannot be created or deleted."""


class CalendarReader(Protocol):
    def list_busy_intervals(
        self,
        *,
        calendar_id: str,
        clinic_id: str,
        dentist_id: str,
        window_start: datetime,
        window_end: datetime,
        exclude_event_id: str | None = None,
    ) -> list[BusyInterval]: ...


class CalendarBookingClient(CalendarReader, Protocol):
    def create_appointment_event(
        self,
        *,
        calendar_id: str,
        clinic_id: str,
        clinic_name: str,
        dentist_id: str,
        appointment_id: str,
        contact_id: str,
        service_id: str,
        service_name: str,
        start_at: datetime,
        end_at: datetime,
        timezone_name: str,
    ) -> str: ...

    def delete_appointment_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
    ) -> None: ...


class CalendarAppointmentChangeClient(CalendarBookingClient, Protocol):
    def update_appointment_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        clinic_id: str,
        clinic_name: str,
        dentist_id: str,
        appointment_id: str,
        contact_id: str,
        service_id: str,
        service_name: str,
        start_at: datetime,
        end_at: datetime,
        timezone_name: str,
    ) -> None: ...


class GoogleCalendarClient:
    def __init__(self, session: AuthorizedSession, timeout_seconds: float) -> None:
        self._session = session
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_default_credentials(
        cls,
        *,
        timeout_seconds: float,
        credentials_file: str | Path | None = None,
    ) -> "GoogleCalendarClient":
        try:
            if credentials_file is not None:
                credentials = service_account.Credentials.from_service_account_file(
                    str(credentials_file),
                    scopes=[_CALENDAR_SCOPE],
                )
            else:
                credentials, _project = google.auth.default(
                    scopes=[_CALENDAR_SCOPE]
                )
        except Exception as exc:
            raise CalendarUnavailableError(
                "Google Calendar credentials are unavailable"
            ) from exc
        return cls(AuthorizedSession(credentials), timeout_seconds)

    def list_busy_intervals(
        self,
        *,
        calendar_id: str,
        clinic_id: str,
        dentist_id: str,
        window_start: datetime,
        window_end: datetime,
        exclude_event_id: str | None = None,
    ) -> list[BusyInterval]:
        if not calendar_id:
            raise CalendarUnavailableError("Clinic Calendar ID is not configured")
        if window_start.tzinfo is None or window_end.tzinfo is None:
            raise ValueError("Calendar window must be timezone-aware")

        url = (
            "https://www.googleapis.com/calendar/v3/calendars/"
            f"{quote(calendar_id, safe='')}/events"
        )
        base_params = [
            ("timeMin", window_start.isoformat()),
            ("timeMax", window_end.isoformat()),
            ("singleEvents", "true"),
            ("orderBy", "startTime"),
            ("privateExtendedProperty", f"clinic_id={clinic_id}"),
            ("privateExtendedProperty", f"dentist_id={dentist_id}"),
        ]
        events: list[dict[str, object]] = []
        page_token: str | None = None
        try:
            while True:
                params = list(base_params)
                if page_token:
                    params.append(("pageToken", page_token))
                response = self._session.get(
                    url,
                    params=params,
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                events.extend(payload.get("items") or [])
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break
        except Exception as exc:
            raise CalendarUnavailableError(
                "Google Calendar availability check failed"
            ) from exc

        intervals: list[BusyInterval] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if exclude_event_id is not None and event.get("id") == exclude_event_id:
                continue
            if event.get("status") == "cancelled":
                continue
            extended = event.get("extendedProperties")
            if not isinstance(extended, dict):
                continue
            private = extended.get("private")
            if not isinstance(private, dict):
                continue
            if (
                private.get("clinic_id") != clinic_id
                or private.get("dentist_id") != dentist_id
                or not private.get("appointment_id")
            ):
                continue
            start_at = _parse_event_boundary(event.get("start"), window_start)
            end_at = _parse_event_boundary(event.get("end"), window_start)
            if start_at is None or end_at is None or end_at <= start_at:
                continue
            intervals.append(
                BusyInterval(
                    dentist_id=dentist_id,
                    start_at=start_at,
                    end_at=end_at,
                )
            )
        return intervals

    def create_appointment_event(
        self,
        *,
        calendar_id: str,
        clinic_id: str,
        clinic_name: str,
        dentist_id: str,
        appointment_id: str,
        contact_id: str,
        service_id: str,
        service_name: str,
        start_at: datetime,
        end_at: datetime,
        timezone_name: str,
    ) -> str:
        if not calendar_id:
            raise CalendarWriteError("Clinic Calendar ID is not configured")
        if start_at.tzinfo is None or end_at.tzinfo is None or end_at <= start_at:
            raise ValueError("Calendar event timestamps must be a valid aware interval")

        url = (
            "https://www.googleapis.com/calendar/v3/calendars/"
            f"{quote(calendar_id, safe='')}/events"
        )
        body = {
            "summary": f"{clinic_name} - {service_name}",
            "start": {
                "dateTime": start_at.isoformat(),
                "timeZone": timezone_name,
            },
            "end": {
                "dateTime": end_at.isoformat(),
                "timeZone": timezone_name,
            },
            "extendedProperties": {
                "private": {
                    "clinic_id": clinic_id,
                    "dentist_id": dentist_id,
                    "appointment_id": appointment_id,
                    "service_id": service_id,
                    "contact_id": contact_id,
                }
            },
        }
        try:
            response = self._session.post(
                url,
                json=body,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            event_id = response.json().get("id")
        except Exception as exc:
            raise CalendarWriteError("Google Calendar event creation failed") from exc
        if not isinstance(event_id, str) or not event_id:
            raise CalendarWriteError("Google Calendar did not return an event ID")
        return event_id

    def delete_appointment_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
    ) -> None:
        if not calendar_id or not event_id:
            raise ValueError("calendar_id and event_id are required")
        url = (
            "https://www.googleapis.com/calendar/v3/calendars/"
            f"{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        )
        try:
            response = self._session.delete(url, timeout=self._timeout_seconds)
            if response.status_code not in {404, 410}:
                response.raise_for_status()
        except Exception as exc:
            raise CalendarWriteError("Google Calendar event deletion failed") from exc

    def update_appointment_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        clinic_id: str,
        clinic_name: str,
        dentist_id: str,
        appointment_id: str,
        contact_id: str,
        service_id: str,
        service_name: str,
        start_at: datetime,
        end_at: datetime,
        timezone_name: str,
    ) -> None:
        if not calendar_id or not event_id:
            raise ValueError("calendar_id and event_id are required")
        if start_at.tzinfo is None or end_at.tzinfo is None or end_at <= start_at:
            raise ValueError("Calendar event timestamps must be a valid aware interval")

        url = (
            "https://www.googleapis.com/calendar/v3/calendars/"
            f"{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        )
        expected_metadata = {
            "clinic_id": clinic_id,
            "appointment_id": appointment_id,
            "contact_id": contact_id,
            "service_id": service_id,
        }
        try:
            current_response = self._session.get(
                url,
                timeout=self._timeout_seconds,
            )
            current_response.raise_for_status()
            current = current_response.json()
            private = (current.get("extendedProperties") or {}).get("private") or {}
            if any(private.get(key) != value for key, value in expected_metadata.items()):
                raise CalendarWriteError(
                    "Google Calendar event ownership metadata does not match"
                )
            etag = current.get("etag")
            body = {
                "summary": f"{clinic_name} - {service_name}",
                "status": "confirmed",
                "start": {
                    "dateTime": start_at.isoformat(),
                    "timeZone": timezone_name,
                },
                "end": {
                    "dateTime": end_at.isoformat(),
                    "timeZone": timezone_name,
                },
                "extendedProperties": {
                    "private": {
                        **expected_metadata,
                        "dentist_id": dentist_id,
                    }
                },
            }
            headers = {"If-Match": etag} if isinstance(etag, str) and etag else None
            response = self._session.put(
                url,
                json=body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except CalendarWriteError:
            raise
        except Exception as exc:
            raise CalendarWriteError("Google Calendar event update failed") from exc


def _parse_event_boundary(
    value: object,
    reference: datetime,
) -> datetime | None:
    if not isinstance(value, dict):
        return None
    date_time_value = value.get("dateTime")
    if isinstance(date_time_value, str):
        return datetime.fromisoformat(date_time_value.replace("Z", "+00:00"))
    date_value = value.get("date")
    if isinstance(date_value, str):
        parsed_date = date.fromisoformat(date_value)
        return datetime.combine(parsed_date, time.min, tzinfo=reference.tzinfo)
    return None
