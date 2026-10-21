from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

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
