from __future__ import annotations

from enum import Enum


class AppointmentStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


_ALLOWED_TRANSITIONS: dict[AppointmentStatus, frozenset[AppointmentStatus]] = {
    AppointmentStatus.PENDING: frozenset(
        {AppointmentStatus.CONFIRMED, AppointmentStatus.CANCELLED}
    ),
    AppointmentStatus.CONFIRMED: frozenset(
        {
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.CANCELLED,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.NO_SHOW,
        }
    ),
    AppointmentStatus.CANCELLED: frozenset(),
    AppointmentStatus.COMPLETED: frozenset(),
    AppointmentStatus.NO_SHOW: frozenset(),
}


def can_transition(
    current_status: AppointmentStatus | str,
    target_status: AppointmentStatus | str,
) -> bool:
    try:
        current = AppointmentStatus(current_status)
        target = AppointmentStatus(target_status)
    except ValueError:
        return False
    return target in _ALLOWED_TRANSITIONS[current]


def validate_transition(
    current_status: AppointmentStatus | str,
    target_status: AppointmentStatus | str,
) -> None:
    if not can_transition(current_status, target_status):
        raise ValueError(
            f"Appointment status cannot transition from {current_status!s} "
            f"to {target_status!s}"
        )
