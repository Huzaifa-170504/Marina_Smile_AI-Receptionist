from types import SimpleNamespace

import pytest

from app.rag import retriever


class FakeRpc:
    def __init__(self, data: list[dict[str, object]]) -> None:
        self.data = data

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self.data)


class FakeClient:
    def __init__(self, data: list[dict[str, object]]) -> None:
        self.data = data
        self.name = ""
        self.params: dict[str, object] = {}

    def rpc(self, name: str, params: dict[str, object]) -> FakeRpc:
        self.name = name
        self.params = params
        return FakeRpc(self.data)


def test_search_calls_rpc_and_returns_structured_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "content": "Whitening costs 100.",
                "page_number": 2,
                "similarity": 0.91,
                "metadata": {"source_filename": "clinic.pdf"},
            }
        ]
    )
    monkeypatch.setattr(retriever, "embed_query", lambda query: [0.1, 0.2])
    monkeypatch.setattr(
        retriever,
        "get_settings",
        lambda: SimpleNamespace(rag_match_count=5, rag_match_threshold=0.7),
    )

    results = retriever.search_knowledge(
        "10000000-0000-4000-8000-000000000001",
        "How much is whitening?",
        client=client,
    )

    assert client.name == "match_knowledge_chunks"
    assert client.params["p_match_count"] == 5
    assert results[0].filename == "clinic.pdf"
    assert results[0].page_number == 2
    assert results[0].similarity == 0.91
