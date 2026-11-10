"""Actual Codex blind review of calibration-2, after translation QA rules were frozen."""

import json

from nlp.artifacts import file_hash, write_json
from scripts.review_data_stage import ROOT, STAGE

LABELS = """
1 1 3 5 0 2 5 4 5 5
-1 1 4 2 5 4 4 1 0 0
3 0 1 5 3 0 1 5 2 3
3 4 3 0 1 3 4 5 2 1
1 1 3 1 5 0 1 5 0 0
5 2 2 5 4 2 4 0 4 3
2 2 5 4 -1 3 4 4 3 5
1 5 1 -1 3 0 0 2 5 0
0 0 5 0 5 4 2 5 2 4
0 4 5 1 5 1 3 5 4 3
5 0 3 2 5 0 5 4 1 0
3 4 0 2 0 1 1 5 4 2
3 0 2 4 1 3 2 0 5 2
0 0 1 0 3 0 1 5 3 1
1 4 4 0 0 1 2 2 -1 2
5 2 -1 4 3 4 2 4 0 4
3 2 1 4 0 2 2 4 0 0
1 0 5 3 1 3 0 5 0 2
5 2 5 0 3 3 3 2 5 4
0 2 4 0 0 1 2 3 -1 1
"""
INVALID = {
    2,
    3,
    4,
    5,
    7,
    10,
    12,
    13,
    15,
    17,
    19,
    24,
    29,
    34,
    35,
    37,
    39,
    42,
    49,
    51,
    52,
    55,
    56,
    57,
    58,
    62,
    65,
    73,
    74,
    78,
    79,
    80,
    81,
    84,
    85,
    87,
    90,
    95,
    97,
    100,
    103,
    105,
    106,
    107,
    110,
    115,
    120,
    122,
    124,
    125,
    127,
    128,
    129,
    132,
    135,
    136,
    141,
    142,
    144,
    148,
    150,
    153,
    156,
    161,
    165,
    166,
    168,
    176,
    179,
    180,
    181,
    184,
    188,
    189,
    190,
    192,
    194,
    195,
    197,
    198,
}


def main():
    plan = json.loads((STAGE / "calibration-plan.json").read_text(encoding="utf-8"))
    frozen = json.loads((STAGE / "qa-rules-frozen-before-holdout.json").read_text())
    if file_hash(ROOT / "nlp/data/translation_qa.py") != frozen["sha256"]:
        raise RuntimeError("QA rules changed after holdout was opened")
    cases = plan["batches"][1]["cases"]
    labels = [int(value) for value in LABELS.split()]
    if len(labels) != len(cases) or len(labels) != 200:
        raise ValueError("Holdout index mismatch")
    write_json(
        STAGE / "codex-classification-holdout.json",
        {
            "method": "AI_CODEX_BLIND_BILINGUAL_REVIEW",
            "human_review": False,
            "role": "Fixed reviewer QA holdout; no changes to filters based on these results",
            "source_plan_hash": plan["plan_hash"],
            "qa_rules_sha256": frozen["sha256"],
            "decisions": [
                {"id": case["id"], "label": label, "translation_ok": i not in INVALID}
                for i, (case, label) in enumerate(zip(cases, labels, strict=True))
            ],
            "limitations": (
                "AI reference judgment, NOT human accuracy; calibration texts"
                " never serve as classifier test examples"
            ),
        },
    )
    print({"reviewed": len(labels), "translation_rejected": len(INVALID), "paid_calls": 0})


if __name__ == "__main__":
    main()
