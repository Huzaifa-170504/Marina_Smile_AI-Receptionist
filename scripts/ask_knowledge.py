import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai.knowledge_answer import answer_knowledge_question
from app.core.config import get_settings


def main() -> int:
    question = input("Enter patient question: ").strip()
    if not question:
        print("A question is required.", file=sys.stderr)
        return 1

    try:
        settings = get_settings()
        result = answer_knowledge_question(
            str(settings.default_clinic_id),
            question,
        )
    except Exception as exc:
        print(f"Knowledge answer failed: {exc}", file=sys.stderr)
        return 1

    print("\nAnswer:")
    print(result.text)
    print("\nSources:")
    if not result.sources:
        print("None")
        return 0

    for index, source in enumerate(result.sources, start=1):
        page = source.page_number if source.page_number is not None else "unknown"
        print(
            f"{index}. {source.filename}, page {page}, "
            f"similarity {source.similarity:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
