"""Freeze a fresh, blinded Qwen7B reviewer holdout before any of its results are inspected."""

import json
import random
from collections import defaultdict

from backend.app.db.models import ReviewItem
from nlp.artifacts import file_hash, write_json
from scripts.review_data_stage import ROOT, STAGE, Database, Settings


def main():
    plan = json.loads((STAGE / "plan.json").read_text(encoding="utf-8"))
    calibration = json.loads((STAGE / "calibration-plan.json").read_text(encoding="utf-8"))
    old = {case["id"] for batch in calibration["batches"] for case in batch["cases"]}
    preview = STAGE / "local-classification-results.jsonl"
    if preview.exists():
        old.update(
            json.loads(line)[("id")] for line in preview.read_text(encoding="utf-8").splitlines()
        )
    pools = defaultdict(list)
    db = Database(Settings(_env_file=ROOT / ".env"))
    try:
        with db.sessions() as session:
            for batch in plan["batches"]:
                if batch["kind"] != "CLASSIFICATION":
                    continue
                for case in batch["cases"]:
                    if case.get("review_item_id", 0) > 282 and case["id"] not in old:
                        pools[
                            session.get(ReviewItem, case[("review_item_id")]).payload["label"]
                        ].append(case)
    finally:
        db.dispose()
    rng = random.Random(20261112)
    selected = []
    for label in range(6):
        rng.shuffle(pools[label])
        selected.extend(pools[label][:20])
    rng.shuffle(selected)
    write_json(
        STAGE / "reviewer-fresh-holdout.json",
        {
            "cases": selected,
            "seed": 20261112,
            "qa_rules_sha256": file_hash(ROOT / "nlp/data/translation_qa.py"),
            "reviewer_code_sha256": file_hash(ROOT / "scripts/review_classification_ollama.py"),
            "scope": (
                "Fresh AI reviewer audit, never classifier validation/test. A"
                "ll related groups pinned to classifier training."
            ),
            "gate": {
                "approved_checks_minimum": 50,
                "label_agreement_minimum": 0.95,
                "translation_pass_minimum": 0.90,
            },
        },
    )
    print({"frozen_blind_cases": len(selected), "paid_calls": 0})


if __name__ == "__main__":
    main()
