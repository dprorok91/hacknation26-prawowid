"""Refresh checked-in schemas from the current Pydantic API definitions."""

import json
from pathlib import Path

from prawdowid.api.schemas import AnalysisReport, AnalysisResponse

ROOT = Path(__file__).resolve().parents[1] / "specs" / "001-core" / "contracts"


def main() -> None:
    for name, model in (
        ("analysis-report.schema.json", AnalysisReport),
        ("analysis-response.schema.json", AnalysisResponse),
    ):
        path = ROOT / name
        path.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Updated {path.relative_to(ROOT.parents[2])}")


if __name__ == "__main__":
    main()
