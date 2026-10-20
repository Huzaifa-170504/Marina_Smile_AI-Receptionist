import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.rag.retriever import search_knowledge


def main() -> int:
    question = input("Enter patient question: ").strip()
    if not question:
        print("A question is required.", file=sys.stderr)
        return 1

    try:
        settings = get_settings()
        results = search_knowledge(str(settings.default_clinic_id), question)
    except Exception as exc:
        print(f"Retrieval failed: {exc}", file=sys.stderr)
        return 1

    if not results:
        print("No matching knowledge chunks found.")
        return 0

    for index, result in enumerate(results, start=1):
        print(f"\nResult {index}")
        print(f"Similarity: {result.similarity:.4f}")
        print(f"Filename: {result.filename}")
        print(f"Page: {result.page_number if result.page_number is not None else 'unknown'}")
        print(f"Content: {result.content}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
