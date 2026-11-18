from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.operations.clinic_hours import get_clinic_holidays
from app.operations.models import Clinic
from app.repositories.clinics import get_active_clinic
from app.repositories.dentists import (
    filter_dentists_for_service,
    find_dentists_in_text,
    list_active_dentists,
)
from app.repositories.services import (
    filter_services_for_dentist,
    find_services_in_text,
    list_active_services,
)


DirectoryStatus = Literal["answered", "needs_clarification"]
DirectoryGrounding = Literal["conversation", "supabase"]


@dataclass(frozen=True)
class DirectoryAnswer:
    text: str
    status: DirectoryStatus
    grounding: DirectoryGrounding


_GREETINGS = {
    "hello",
    "hi",
    "hey",
    "hii",
    "good morning",
    "good afternoon",
    "good evening",
    "مرحبا",
    "مرحبا بك",
    "اهلا",
    "أهلا",
    "السلام عليكم",
}
_SERVICE_TERMS = {
    "service",
    "services",
    "servics",
    "srvices",
    "treatment",
    "treatments",
    "procedure",
    "procedures",
    "خدمات",
    "الخدمات",
    "علاجات",
    "العلاجات",
}
_DENTIST_TERMS = {
    "doctor",
    "doctors",
    "dentist",
    "dentists",
    "dr",
    "أطباء",
    "الاطباء",
    "الأطباء",
    "طبيب",
    "دكتور",
    "دكتورة",
}
_DIRECTORY_CUES = {
    "what",
    "which",
    "who",
    "list",
    "show",
    "offer",
    "offered",
    "offrered",
    "provide",
    "provided",
    "available",
    "have",
    "do",
    "does",
    "ما",
    "ماذا",
    "من",
    "متاح",
    "متاحة",
    "تقدمون",
    "يقدم",
}


_HOURS_QUESTION = re.compile(
    r"\b(?:what|which)\s+(?:are\s+)?(?:your\s+)?(?:working\s+|opening\s+|business\s+)?"
    r"(?:hours|timings?)\b"
    r"|\b(?:when|what\s+times?)\b.*\b(?:open|opens|opening|close|closes|closing)\b"
    r"|\b(?:are|is)\s+(?:you|the clinic)\s+(?:open|closed)\b"
    r"|\b(?:do|does)\s+(?:you|the clinic)\s+(?:open|close)\b"
    r"|\bopening hours\b|\bclosing time\b"
    r"|ساعات\s+العمل|متى\s+تفتحون|هل\s+أنتم\s+مفتوحون",
    re.IGNORECASE,
)

_WEEKDAYS = {
    1: ("monday", "mon"),
    2: ("tuesday", "tue", "tues"),
    3: ("wednesday", "wed"),
    4: ("thursday", "thu", "thur", "thurs"),
    5: ("friday", "fri"),
    6: ("saturday", "sat"),
    7: ("sunday", "sun"),
}
_DAY_LABELS = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}


def _format_time(value: time) -> str:
    formatted = value.strftime("%I:%M %p")
    return formatted[1:] if formatted.startswith("0") else formatted


def _day_phrase(days: list[int]) -> str:
    if days == [1, 2, 3, 4, 5]:
        return "Monday to Friday"
    return _join_names([_DAY_LABELS[day] for day in days])


def _open_hours_sentence(clinic: Clinic) -> str:
    open_days = [day for day in range(1, 8) if day not in clinic.closed_weekdays]
    if not open_days:
        return "There are no regular opening hours on file."
    text = (
        f"The clinic is open {_day_phrase(open_days)} from "
        f"{_format_time(clinic.open_time)} to {_format_time(clinic.close_time)}"
    )
    if clinic.break_start is not None and clinic.break_end is not None:
        text += (
            f", with a break from {_format_time(clinic.break_start)} "
            f"to {_format_time(clinic.break_end)}"
        )
    return text + "."


def _hours_summary(clinic: Clinic) -> str:
    closed_days = [day for day in range(1, 8) if day in clinic.closed_weekdays]
    text = _open_hours_sentence(clinic)
    if closed_days:
        labels = [_DAY_LABELS[d] for d in closed_days]
        closed_text = " and ".join(labels) if len(labels) == 2 else _join_names(labels)
        text += f" It is closed on {closed_text} and on public holidays."
    else:
        text += " It is closed on public holidays."
    return text


def _mentioned_weekday(normalized: str) -> int | None:
    tokens = set(normalized.split())
    for day, names in _WEEKDAYS.items():
        if any(name in tokens for name in names):
            return day
    return None


def _day_hours_answer(
    clinic: Clinic,
    day: int,
    *,
    arabic: bool,
) -> DirectoryAnswer:
    label = _DAY_LABELS[day]
    if day in clinic.closed_weekdays:
        text = (
            f"العيادة مغلقة يوم {label}."
            if arabic
            else f"The clinic is closed on {label}."
        )
        return DirectoryAnswer(
            f"{text} {_open_hours_sentence(clinic)}", "answered", "supabase"
        )
    break_note = ""
    if clinic.break_start is not None and clinic.break_end is not None:
        break_note = (
            ""
            if arabic
            else (
                f", with a break from {_format_time(clinic.break_start)} "
                f"to {_format_time(clinic.break_end)}"
            )
        )
    text = (
        f"في يوم {label}، تفتح العيادة من {_format_time(clinic.open_time)} "
        f"إلى {_format_time(clinic.close_time)}."
        if arabic
        else f"On {label}, the clinic is open from {_format_time(clinic.open_time)} "
        f"to {_format_time(clinic.close_time)}{break_note}."
    )
    return DirectoryAnswer(text, "answered", "supabase")


def _answer_hours_question(
    clinic_id: str,
    normalized: str,
    *,
    arabic: bool,
) -> DirectoryAnswer | None:
    if not _HOURS_QUESTION.search(normalized):
        return None
    try:
        clinic = get_active_clinic(clinic_id)
    except Exception:
        return None

    weekday = _mentioned_weekday(normalized)
    if weekday is not None:
        return _day_hours_answer(clinic, weekday, arabic=arabic)

    timezone = ZoneInfo(clinic.timezone)
    local_now = datetime.now(timezone)
    today = local_now.date()
    words = normalized.split()
    asks_tomorrow = "tomorrow" in words
    asks_today = "today" in words or "now" in words
    if asks_today or asks_tomorrow:
        target = today + timedelta(days=1) if asks_tomorrow else today
        try:
            holidays = get_clinic_holidays(clinic_id, target, target)
        except Exception:
            holidays = set()
        if target.isoweekday() in clinic.closed_weekdays or target in holidays:
            day_label = "tomorrow" if asks_tomorrow else "today"
            text = (
                f"العيادة مغلقة {day_label}."
                if arabic
                else f"The clinic is closed {day_label}."
            )
            return DirectoryAnswer(
                f"{text} {_open_hours_sentence(clinic)}", "answered", "supabase"
            )

    if "now" in words and not asks_tomorrow:
        current = local_now.time()
        is_break = (
            clinic.break_start is not None
            and clinic.break_end is not None
            and clinic.break_start <= current < clinic.break_end
        )
        is_open = clinic.open_time <= current < clinic.close_time and not is_break
        if is_open:
            text = (
                f"العيادة مفتوحة الآن وتغلق عند {_format_time(clinic.close_time)}."
                if arabic
                else f"The clinic is open right now and closes at "
                f"{_format_time(clinic.close_time)}."
            )
            return DirectoryAnswer(
                f"{text} {_open_hours_sentence(clinic)}", "answered", "supabase"
            )
        text = (
            "العيادة مغلقة الآن."
            if arabic
            else "The clinic is not open right now."
        )
        return DirectoryAnswer(
            f"{text} {_hours_summary(clinic)}", "answered", "supabase"
        )

    return DirectoryAnswer(_hours_summary(clinic), "answered", "supabase")


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return " ".join(normalized.split())


def _has_arabic(value: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", value))


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _has_term(tokens: set[str], terms: set[str]) -> bool:
    return bool(tokens & terms)


def answer_clinic_directory_question(
    clinic_id: str,
    question: str,
) -> DirectoryAnswer | None:
    normalized = _normalize(question)
    tokens = set(normalized.split())
    arabic = _has_arabic(question)
    has_service_term = _has_term(tokens, _SERVICE_TERMS)
    has_dentist_term = _has_term(tokens, _DENTIST_TERMS)
    has_directory_cue = _has_term(tokens, _DIRECTORY_CUES)

    # Opening hours are operational facts. Answer them from the clinic row, never
    # from the demo knowledge PDF, so the AI cannot contradict live booking.
    hours_answer = _answer_hours_question(clinic_id, normalized, arabic=arabic)
    if hours_answer is not None:
        return hours_answer

    if has_service_term and has_dentist_term and has_directory_cue:
        dentists = sorted(list_active_dentists(clinic_id), key=lambda item: item.name)
        matches = find_dentists_in_text(dentists, question)
        if len(matches) != 1:
            names = _join_names([dentist.name for dentist in dentists])
            text = (
                f"يرجى تحديد اسم الطبيب. الأطباء النشطون هم: {names}."
                if arabic
                else f"Please specify one doctor. The active doctors are {names}."
            )
            return DirectoryAnswer(text, "needs_clarification", "supabase")

        dentist = matches[0]
        services = sorted(list_active_services(clinic_id), key=lambda item: item.name)
        assigned = filter_services_for_dentist(clinic_id, services, dentist.id)
        if not assigned:
            text = (
                f"لا توجد خدمات نشطة مسندة حالياً إلى {dentist.name}."
                if arabic
                else f"No active services are currently assigned to {dentist.name}."
            )
        else:
            names = _join_names([service.name for service in assigned])
            text = (
                f"الخدمات النشطة المسندة إلى {dentist.name} هي: {names}."
                if arabic
                else f"{dentist.name} is currently assigned to {names}."
            )
        return DirectoryAnswer(text, "answered", "supabase")

    if has_service_term and has_directory_cue:
        services = sorted(list_active_services(clinic_id), key=lambda item: item.name)
        names = _join_names([service.name for service in services])
        if not services:
            text = (
                "لا توجد خدمات نشطة مدرجة حالياً."
                if arabic
                else "No active services are currently listed."
            )
        else:
            text = (
                f"تقدم العيادة حالياً {len(services)} خدمات نشطة: {names}."
                if arabic
                else f"The clinic currently lists {len(services)} active services: {names}."
            )
        return DirectoryAnswer(text, "answered", "supabase")

    if has_dentist_term and has_directory_cue:
        dentists = sorted(list_active_dentists(clinic_id), key=lambda item: item.name)
        services = sorted(list_active_services(clinic_id), key=lambda item: item.name)
        mentioned_services = find_services_in_text(services, question)
        if len(mentioned_services) == 1:
            service = mentioned_services[0]
            assigned = filter_dentists_for_service(
                clinic_id,
                dentists,
                service.id,
            )
            names = _join_names([dentist.name for dentist in assigned])
            if assigned:
                text = (
                    f"الأطباء المسند إليهم {service.name} هم: {names}."
                    if arabic
                    else f"{service.name} is currently assigned to {names}."
                )
                return DirectoryAnswer(text, "answered", "supabase")

            text = (
                f"لا يوجد أطباء نشطون مسند إليهم {service.name} حالياً."
                if arabic
                else f"No active doctors are currently assigned to {service.name}."
            )
            return DirectoryAnswer(text, "answered", "supabase")
        if len(mentioned_services) > 1:
            names = _join_names([service.name for service in mentioned_services])
            text = (
                f"يرجى اختيار خدمة واحدة: {names}."
                if arabic
                else f"Please choose one service: {names}."
            )
            return DirectoryAnswer(text, "needs_clarification", "supabase")

        descriptions = [
            f"{dentist.name} ({dentist.specialty})"
            if dentist.specialty
            else dentist.name
            for dentist in dentists
        ]
        names = _join_names(descriptions)
        if not dentists:
            text = (
                "لا يوجد أطباء نشطون مدرجون حالياً."
                if arabic
                else "No active doctors are currently listed."
            )
        else:
            prefix = (
                f"الأطباء النشطون المدرجون حالياً هم: {names}."
                if arabic
                else f"The clinic currently lists these active doctors: {names}."
            )
            suffix = (
                " استخدم قسم المواعيد المتاحة للتحقق من الأوقات الفعلية."
                if arabic
                else " Use Live availability to verify actual appointment times."
            )
            text = prefix + suffix
        return DirectoryAnswer(text, "answered", "supabase")

    if normalized in _GREETINGS:
        text = (
            "مرحباً! أنا مساعد مارينا سمايل. يمكنني شرح خدمات العيادة، عرض الأطباء، الإجابة من قاعدة المعرفة، أو التحقق من المواعيد المتاحة."
            if arabic
            else "Hello! I'm Marina Smile's AI receptionist. I can explain clinic services, list doctors, answer from the knowledge base, or check live availability."
        )
        return DirectoryAnswer(text, "answered", "conversation")

    return None
