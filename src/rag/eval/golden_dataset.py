import json

from pydantic import BaseModel

from rag.config import settings


class GoldenCase(BaseModel):
    id: str
    question: str
    category: str
    difficulty: str  # "easy" | "multi_hop" | "no_answer"
    expected_source_files: list[str]
    expected_facts: list[str]


def load_golden_dataset() -> list[GoldenCase]:
    path = settings.processed_data_dir.parent / "eval" / "golden_qa.json"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [GoldenCase(**item) for item in raw]
