"""Record actual Codex bilingual judgments for the fresh 120-case reviewer audit."""

import json

from nlp.artifacts import file_hash, write_json
from scripts.review_data_stage import ROOT, STAGE

LABELS = """
5 5 2 2 0 5 5 3 0 3
2 1 5 1 1 3 5 2 4 4
2 0 4 2 3 2 4 0 5 3
3 2 1 5 1 1 4 0 -1 2
1 4 5 0 2 2 3 1 5 1
0 -1 0 1 1 4 0 0 5 0
2 4 -1 2 2 5 1 0 4 0
0 5 -1 0 1 0 4 3 5 0
1 -1 4 2 2 5 1 0 3 4
1 4 3 1 5 5 3 2 2 4
4 -1 2 3 1 0 5 5 1 4
4 2 -1 3 4 4 4 2 0 1
"""
INVALID = {
    2,
    6,
    7,
    12,
    14,
    19,
    29,
    32,
    33,
    38,
    41,
    42,
    43,
    47,
    48,
    49,
    51,
    52,
    53,
    58,
    62,
    66,
    68,
    72,
    75,
    77,
    80,
    81,
    83,
    84,
    89,
    91,
    92,
    94,
    95,
    96,
    98,
    101,
    104,
    105,
    106,
    107,
    108,
    110,
    111,
    112,
    113,
    116,
    119,
}


def main():
    plan = json.loads((STAGE / "reviewer-fresh-holdout.json").read_text(encoding="utf-8"))
    if (
        file_hash(ROOT / ("nlp/data/translation_qa.py")) != plan["qa_rules_sha256"]
        or file_hash(ROOT / "scripts/review_classification_ollama.py")
        != plan["reviewer_code_sha256"]
    ):
        raise RuntimeError("Reviewer or filters changed after holdout freeze")
    cases = plan["cases"]
    labels = [int(value) for value in LABELS.split()]
    if len(cases) != len(labels) or len(cases) != 120:
        raise ValueError("Fresh holdout alignment mismatch")
    write_json(
        STAGE / "codex-reviewer-fresh-holdout.json",
        {
            "method": "AI_CODEX_BLIND_BILINGUAL_REVIEW",
            "human_review": False,
            "scope": "Fresh reviewer-only audit, not human gold and not classifier test results",
            "qa_rules_sha256": plan["qa_rules_sha256"],
            "decisions": [
                {"id": case["id"], "label": label, "translation_ok": i not in INVALID}
                for i, (case, label) in enumerate(zip(cases, labels, strict=True))
            ],
        },
    )
    print({"blind_cases_read": len(labels), "not_qualified": len(INVALID), "paid_calls": 0})


if __name__ == "__main__":
    main()
