from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rag import ingest
from app.rag.ingest import PageText, chunk_pages, normalize_text


class FakeTable:
    def __init__(self, client: "FakeClient", name: str) -> None:
        self.client = client
        self.name = name
        self.operation = ""
        self.payload: object = None

    def select(self, _columns: str) -> "FakeTable":
        self.operation = "select"
        return self

    def insert(self, payload: object) -> "FakeTable":
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload: object) -> "FakeTable":
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self) -> "FakeTable":
        self.operation = "delete"
        return self

    def eq(self, column: str, value: object) -> "FakeTable":
        self.client.filters.append((self.name, column, value))
        return self

    def limit(self, _count: int) -> "FakeTable":
        return self

    def execute(self) -> SimpleNamespace:
        if self.operation == "select":
            return SimpleNamespace(data=self.client.documents)
        if self.operation == "insert" and self.name == "knowledge_documents":
            self.client.document_inserts.append(self.payload)
            return SimpleNamespace(data=[{"id": "document-1"}])
        if self.operation == "insert":
            self.client.chunk_inserts.append(self.payload)
            return SimpleNamespace(data=self.payload)
        if self.operation == "update":
            self.client.status_updates.append(self.payload)
            return SimpleNamespace(data=[{"id": "document-1"}])
        if self.operation == "delete" and self.name == "knowledge_chunks":
            self.client.chunk_deletes += 1
            return SimpleNamespace(data=[])
        raise AssertionError("Unexpected fake Supabase operation")


class FakeClient:
    def __init__(self, documents: list[dict[str, object]] | None = None) -> None:
        self.documents = documents or []
        self.document_inserts: list[object] = []
        self.chunk_inserts: list[object] = []
        self.status_updates: list[object] = []
        self.chunk_deletes = 0
        self.filters: list[tuple[str, str, object]] = []

    def table(self, name: str) -> FakeTable:
        return FakeTable(self, name)


def test_normalize_text_preserves_paragraphs() -> None:
    raw = "  First   line.\r\n\r\n\r\n Second\tline.  "

    assert normalize_text(raw) == "First line.\n\nSecond line."


def test_chunking_is_deterministic_and_page_aware() -> None:
    pages = [
        PageText(1, "alpha bravo charlie delta echo foxtrot"),
        PageText(3, "golf hotel india"),
    ]

    first = chunk_pages(pages, chunk_size=20, chunk_overlap=6)
    second = chunk_pages(pages, chunk_size=20, chunk_overlap=6)

    assert first == second
    assert [chunk.chunk_index for chunk in first] == list(range(len(first)))
    assert [chunk.page_number for chunk in first][-1] == 3
    assert all(not chunk.content.startswith(" ") for chunk in first)
    assert all(not chunk.content.endswith(" ") for chunk in first)


@pytest.mark.parametrize(
    ("size", "overlap"),
    [(0, 0), (10, -1), (10, 10)],
)
def test_chunking_rejects_invalid_configuration(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_pages([PageText(1, "text")], size, overlap)


def test_ingestion_persists_chunks_and_ready_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "clinic.pdf"
    pdf_path.write_bytes(b"pdf-content")
    client = FakeClient()
    monkeypatch.setattr(
        ingest,
        "get_settings",
        lambda: SimpleNamespace(
            rag_chunk_size=900,
            rag_chunk_overlap=150,
            gemini_embedding_dimensions=768,
        ),
    )
    monkeypatch.setattr(
        ingest,
        "extract_pdf_pages",
        lambda _path: (1, [PageText(1, "Whitening costs 100.")]),
    )
    monkeypatch.setattr(ingest, "embed_document", lambda _text: [0.1] * 768)

    result = ingest.ingest_pdf(
        "10000000-0000-4000-8000-000000000001",
        pdf_path,
        client=client,
    )

    assert result.status == "ready"
    assert result.chunk_count == 1
    assert client.document_inserts[0]["status"] == "processing"
    assert client.chunk_inserts[0][0]["metadata"] == {
        "source_filename": "clinic.pdf"
    }
    assert client.status_updates == [{"status": "ready"}]


def test_ingestion_marks_document_failed_when_embedding_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "clinic.pdf"
    pdf_path.write_bytes(b"pdf-content")
    client = FakeClient()
    monkeypatch.setattr(
        ingest,
        "get_settings",
        lambda: SimpleNamespace(
            rag_chunk_size=900,
            rag_chunk_overlap=150,
            gemini_embedding_dimensions=768,
        ),
    )
    monkeypatch.setattr(
        ingest,
        "extract_pdf_pages",
        lambda _path: (1, [PageText(1, "Clinic knowledge")]),
    )

    def fail_embedding(_text: str) -> list[float]:
        raise RuntimeError("embedding unavailable")

    monkeypatch.setattr(ingest, "embed_document", fail_embedding)

    with pytest.raises(RuntimeError, match="embedding unavailable"):
        ingest.ingest_pdf(
            "10000000-0000-4000-8000-000000000001",
            pdf_path,
            client=client,
        )

    assert client.status_updates == [{"status": "failed"}]


def test_ready_checksum_is_not_ingested_twice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "clinic.pdf"
    pdf_path.write_bytes(b"same-pdf-content")
    client = FakeClient([{"id": "existing-document", "status": "ready"}])
    monkeypatch.setattr(
        ingest,
        "get_settings",
        lambda: SimpleNamespace(
            rag_chunk_size=900,
            rag_chunk_overlap=150,
            gemini_embedding_dimensions=768,
        ),
    )
    monkeypatch.setattr(
        ingest,
        "extract_pdf_pages",
        lambda _path: (1, [PageText(1, "Clinic knowledge")]),
    )

    result = ingest.ingest_pdf(
        "10000000-0000-4000-8000-000000000001",
        pdf_path,
        client=client,
    )

    assert result.already_ingested is True
    assert result.document_id == "existing-document"
    assert client.document_inserts == []
    assert client.chunk_inserts == []
    assert not any(
        table == "knowledge_documents" and column == "status"
        for table, column, _value in client.filters
    )


def test_failed_document_is_reused_for_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "clinic.pdf"
    pdf_path.write_bytes(b"same-pdf-content")
    client = FakeClient([{"id": "failed-document", "status": "failed"}])
    monkeypatch.setattr(
        ingest,
        "get_settings",
        lambda: SimpleNamespace(
            rag_chunk_size=900,
            rag_chunk_overlap=150,
            gemini_embedding_dimensions=768,
        ),
    )
    monkeypatch.setattr(
        ingest,
        "extract_pdf_pages",
        lambda _path: (1, [PageText(1, "Clinic knowledge")]),
    )
    monkeypatch.setattr(ingest, "embed_document", lambda _text: [0.1] * 768)

    result = ingest.ingest_pdf(
        "10000000-0000-4000-8000-000000000001",
        pdf_path,
        client=client,
    )

    assert result.status == "ready"
    assert result.document_id == "failed-document"
    assert result.already_ingested is False
    assert client.document_inserts == []
    assert client.chunk_deletes == 1
    assert (
        "knowledge_chunks",
        "document_id",
        "failed-document",
    ) in client.filters
    assert client.status_updates == [
        {"status": "processing"},
        {"status": "ready"},
    ]
    assert client.chunk_inserts[0][0]["document_id"] == "failed-document"


def test_processing_document_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "clinic.pdf"
    pdf_path.write_bytes(b"same-pdf-content")
    client = FakeClient(
        [{"id": "processing-document", "status": "processing"}]
    )
    monkeypatch.setattr(
        ingest,
        "get_settings",
        lambda: SimpleNamespace(
            rag_chunk_size=900,
            rag_chunk_overlap=150,
            gemini_embedding_dimensions=768,
        ),
    )

    with pytest.raises(RuntimeError, match="already being processed"):
        ingest.ingest_pdf(
            "10000000-0000-4000-8000-000000000001",
            pdf_path,
            client=client,
        )

    assert client.document_inserts == []
    assert client.chunk_inserts == []
    assert client.chunk_deletes == 0
    assert client.status_updates == []
