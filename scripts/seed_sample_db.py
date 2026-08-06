"""One-off: seeds the sample_metric table the db_query tool reads from."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlmodel import select  # noqa: E402

from agentsys.db.models import SampleMetric  # noqa: E402
from agentsys.db.session import get_session, init_db  # noqa: E402

ROWS = [
    {"company": "Acme Robotics", "quarter": "2026-Q1", "revenue_usd": 4_200_000, "headcount": 85},
    {"company": "Acme Robotics", "quarter": "2026-Q2", "revenue_usd": 4_950_000, "headcount": 91},
    {"company": "Blue Harbor Logistics", "quarter": "2026-Q1", "revenue_usd": 1_800_000, "headcount": 40},
    {"company": "Blue Harbor Logistics", "quarter": "2026-Q2", "revenue_usd": 2_100_000, "headcount": 44},
    {"company": "Cedar Analytics", "quarter": "2026-Q1", "revenue_usd": 9_600_000, "headcount": 210},
    {"company": "Cedar Analytics", "quarter": "2026-Q2", "revenue_usd": 10_450_000, "headcount": 228},
]


def main() -> None:
    init_db()
    with get_session() as session:
        existing = session.exec(select(SampleMetric)).first()
        if existing:
            print("sample_metric already seeded, skipping")
            return
        for row in ROWS:
            session.add(SampleMetric(**row))
        session.commit()
    print(f"Seeded {len(ROWS)} rows into sample_metric")


if __name__ == "__main__":
    main()
