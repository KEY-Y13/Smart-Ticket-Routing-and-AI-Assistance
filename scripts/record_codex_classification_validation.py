"""Codex's blind bilingual judgments of the frozen first 200 calibration cases, actually read."""

import json

from nlp.artifacts import write_json
from scripts.review_data_stage import STAGE

# Indexed to the unchanged calibration-1 list, not to source intent labels.
LABELS = """
4 5 2 1 5 4 2 3 3 0
2 1 0 0 5 4 5 4 5 3
4 -1 3 2 0 0 5 3 -1 4
2 -1 4 0 3 4 1 5 1 1
3 2 4 4 0 3 0 3 3 2
4 2 3 0 0 2 -1 1 -1 0
2 5 4 4 2 5 0 4 0 3
4 1 0 0 1 4 5 -1 0 -1
2 2 1 4 0 4 4 5 5 2
5 0 2 4 4 2 1 1 2 3
3 3 0 0 1 4 1 3 3 -1
0 2 4 3 0 0 0 3 4 3
5 2 2 1 0 2 1 5 2 2
1 0 3 2 5 4 3 0 4 2
-1 1 0 2 3 2 -1 1 1 1
5 2 0 1 -1 5 0 3 3 4
4 4 2 4 -1 5 5 1 5 5
-1 5 0 5 1 5 5 1 0 5
5 0 4 4 0 2 1 1 3 2
0 0 5 1 0 0 1 2 1 5
"""
INVALID = {
    7,
    11,
    13,
    14,
    15,
    16,
    18,
    22,
    25,
    26,
    27,
    28,
    29,
    33,
    36,
    44,
    47,
    48,
    50,
    51,
    56,
    58,
    61,
    67,
    68,
    72,
    74,
    77,
    78,
    79,
    84,
    85,
    88,
    89,
    93,
    95,
    98,
    99,
    106,
    107,
    109,
    115,
    121,
    122,
    123,
    125,
    127,
    128,
    130,
    133,
    135,
    136,
    137,
    138,
    140,
    141,
    142,
    145,
    146,
    151,
    153,
    154,
    155,
    156,
    160,
    163,
    165,
    169,
    170,
    182,
    184,
    185,
    189,
    194,
    198,
    199,
}


def main():
    plan = json.loads((STAGE / "calibration-plan.json").read_text(encoding="utf-8"))
    cases = plan["batches"][0]["cases"]
    labels = [int(value) for value in LABELS.split()]
    if len(labels) != len(cases) or len(labels) != 200:
        raise ValueError("Manual AI judgment index mismatch")
    write_json(
        STAGE / "codex-classification-validation.json",
        {
            "method": "AI_CODEX_BLIND_BILINGUAL_REVIEW",
            "human_review": False,
            "role": "Reviewer calibration/development ONLY, not classifier train/test data",
            "source_plan_hash": plan["plan_hash"],
            "decisions": [
                {"id": case["id"], "label": label, "translation_ok": i not in INVALID}
                for i, (case, label) in enumerate(zip(cases, labels, strict=True))
            ],
            "criteria": (
                "Chinese main intent; preserve English facts, negation, payer"
                "/card type, refund versus return, shipment versus arrival, s"
                "peaker roles and pre-dispatch conditions; unnatural or mater"
                "ially changed translations rejected"
            ),
            "limitations": (
                "Codex AI judgment, not human certification; overlaps the cla"
                "ssification corpus solely for reviewer QA, classifier splits"
                " remain separate and frozen before fitting"
            ),
        },
    )
    print({"read_and_reviewed": len(labels), "translation_rejected": len(INVALID), "paid_calls": 0})


if __name__ == "__main__":
    main()
