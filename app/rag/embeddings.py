from google.genai import types

from app.ai.gemini_client import get_gemini_client as get_genai_client
from app.core.config import get_settings


class EmbeddingError(RuntimeError):
    """Raised when Gemini does not return a usable embedding."""


def _embed(prepared_text: str) -> list[float]:
    settings = get_settings()
    try:
        response = get_genai_client().models.embed_content(
            model=settings.gemini_embedding_model,
            contents=prepared_text,
            config=types.EmbedContentConfig(
                output_dimensionality=settings.gemini_embedding_dimensions,
            ),
        )
    except Exception as exc:
        raise EmbeddingError("Gemini embedding request failed") from exc

    if not response.embeddings or response.embeddings[0].values is None:
        raise EmbeddingError("Gemini returned no embedding values")

    vector = [float(value) for value in response.embeddings[0].values]
    expected = settings.gemini_embedding_dimensions
    if len(vector) != expected:
        raise EmbeddingError(
            f"Gemini returned {len(vector)} dimensions; expected {expected}"
        )
    return vector


def embed_document(text: str) -> list[float]:
    content = text.strip()
    if not content:
        raise ValueError("Document text must not be empty")
    return _embed(f"title: none | text: {content}")


def embed_query(text: str) -> list[float]:
    query = text.strip()
    if not query:
        raise ValueError("Query text must not be empty")
    return _embed(f"task: question answering | query: {query}")
