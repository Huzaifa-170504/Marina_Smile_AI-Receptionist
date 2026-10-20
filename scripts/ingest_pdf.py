import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.ingest import ingest_pdf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a clinic knowledge PDF")
    parser.add_argument("--clinic-id", required=True, help="Clinic UUID")
    parser.add_argument("--pdf", required=True, help="Path to the source PDF")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = ingest_pdf(args.clinic_id, args.pdf)
    except Exception as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1

    print(f"PDF filename: {result.filename}")
    print(f"Page count: {result.page_count}")
    print(f"Number of chunks: {result.chunk_count}")
    print(f"Document ID: {result.document_id}")
    print(f"Embedding dimensions: {result.embedding_dimensions}")
    print(f"Final status: {result.status}")
    if result.already_ingested:
        print("Already ingested: yes (existing ready document reused)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
