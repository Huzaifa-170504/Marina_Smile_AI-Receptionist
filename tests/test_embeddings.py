from types import SimpleNamespace

import pytest

from app.rag import embeddings


class FakeModels:
    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.calls: list[dict[str, object]] = []

    def embed_content(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=self.values)],
        )


def configure_fakes(
    monkeypatch: pytest.MonkeyPatch,
    values: list[float],
) -> FakeModels:
    models = FakeModels(values)
    monkeypatch.setattr(
        embeddings,
        "get_settings",
        lambda: SimpleNamespace(
            gemini_embedding_model="gemini-embedding-2",
            gemini_embedding_dimensions=3,
        ),
    )
    monkeypatch.setattr(
        embeddings,
        "get_genai_client",
        lambda: SimpleNamespace(models=models),
    )
    return models


def test_document_and_query_use_retrieval_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = configure_fakes(monkeypatch, [0.1, 0.2, 0.3])

    assert embeddings.embed_document("Clinic prices") == [0.1, 0.2, 0.3]
    assert embeddings.embed_query("How much?") == [0.1, 0.2, 0.3]

    assert models.calls[0]["contents"] == "title: none | text: Clinic prices"
    assert models.calls[1]["contents"] == (
        "task: question answering | query: How much?"
    )


def test_embedding_dimension_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_fakes(monkeypatch, [0.1, 0.2])

    with pytest.raises(embeddings.EmbeddingError, match="expected 3"):
        embeddings.embed_query("question")
