from typing import Any

from fastapi.testclient import TestClient

from app.ai.knowledge_answer import KnowledgeAnswer, KnowledgeSource
from app.main import app
from app.services.demo_access import require_demo_access


FINGERPRINT = "a" * 64


def test_demo_query_requires_server_proxy_token() -> None:
    response = TestClient(app).post(
        "/api/demo/query",
        headers={"X-Demo-Visitor": FINGERPRINT},
        json={"mode": "knowledge", "query": "How much is whitening?"},
    )
    assert response.status_code in {401, 503}


def test_demo_knowledge_query_returns_grounded_sources(monkeypatch: Any) -> None:
    quota_calls: list[tuple[str, str]] = []
    app.dependency_overrides[require_demo_access] = lambda: None
    monkeypatch.setattr(
        "app.api.routes.demo.consume_demo_quota",
        lambda *, fingerprint_hash, query_type: quota_calls.append(
            (fingerprint_hash, query_type)
        ),
    )
    monkeypatch.setattr(
        "app.api.routes.demo.answer_knowledge_question",
        lambda _clinic_id, _query: KnowledgeAnswer(
            text="Whitening pricing starts from the approved clinic amount.",
            sources=[KnowledgeSource(filename="clinic.pdf", page_number=2, similarity=0.91)],
        ),
    )
    try:
        response = TestClient(app).post(
            "/api/demo/query",
            headers={"X-Demo-Visitor": FINGERPRINT},
            json={"mode": "knowledge", "query": "How much is whitening?"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "knowledge"
    assert payload["sources"] == [
        {"filename": "clinic.pdf", "page_number": 2, "similarity": 0.91}
    ]
    assert quota_calls == [(FINGERPRINT, "knowledge")]


def test_demo_query_rejects_unbounded_or_unknown_input() -> None:
    app.dependency_overrides[require_demo_access] = lambda: None
    try:
        invalid_mode = TestClient(app).post(
            "/api/demo/query",
            headers={"X-Demo-Visitor": FINGERPRINT},
            json={"mode": "book", "query": "Book an appointment"},
        )
        oversized = TestClient(app).post(
            "/api/demo/query",
            headers={"X-Demo-Visitor": FINGERPRINT},
            json={"mode": "knowledge", "query": "x" * 301},
        )
    finally:
        app.dependency_overrides.clear()

    assert invalid_mode.status_code == 422
    assert oversized.status_code == 422


def test_demo_status_exposes_only_aggregate_counts(monkeypatch: Any) -> None:
    app.dependency_overrides[require_demo_access] = lambda: None
    counts = {"dentists": 3, "services": 8, "knowledge_documents": 1}
    monkeypatch.setattr(
        "app.api.routes.demo._count",
        lambda table, _clinic_id, **_kwargs: counts[table],
    )
    try:
        response = TestClient(app).get("/api/demo/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "api": "online",
        "supabase": "online",
        "knowledge": "ready",
        "active_dentists": 3,
        "active_services": 8,
        "ready_documents": 1,
    }
