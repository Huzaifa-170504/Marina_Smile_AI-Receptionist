from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings


class GeminiGenerationError(RuntimeError):
    """Raised when Gemini does not return a usable grounded answer."""


class _GroundedResponse(BaseModel):
    supported: bool
    answer: str = Field(min_length=1)


@dataclass(frozen=True)
class GroundedGeneration:
    text: str
    supported: bool


@lru_cache(maxsize=1)
def get_gemini_client() -> genai.Client:
    settings = get_settings()
    return genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(timeout=120_000),
    )


def generate_grounded_answer(
    *,
    system_prompt: str,
    user_prompt: str,
) -> GroundedGeneration:
    settings = get_settings()
    try:
        response = get_gemini_client().models.generate_content(
            model=settings.gemini_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
                max_output_tokens=128,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW,
                ),
                response_mime_type="application/json",
                response_schema=_GroundedResponse,
            ),
        )
    except Exception as exc:
        raise GeminiGenerationError("Gemini answer generation failed") from exc

    try:
        if isinstance(response.parsed, _GroundedResponse):
            parsed = response.parsed
        elif response.parsed is not None:
            parsed = _GroundedResponse.model_validate(response.parsed)
        else:
            parsed = _GroundedResponse.model_validate_json(response.text or "")
    except (TypeError, ValueError, ValidationError) as exc:
        raise GeminiGenerationError("Gemini returned an invalid answer") from exc

    answer = parsed.answer.strip()
    if not answer:
        raise GeminiGenerationError("Gemini returned an empty answer")
    return GroundedGeneration(text=answer, supported=parsed.supported)
