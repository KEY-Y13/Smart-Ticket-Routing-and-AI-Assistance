"""Actual Codex judgments of the 120 NEW Chinese texts; preserves separate bilingual caveats."""

import json

from nlp.artifacts import write_json
from nlp.data.dataset import canonical_hash
from scripts.review_data_stage import ROOT, STAGE

LABELS = """
5 5 2 2 0 5 5 3 0 3
2 1 5 1 1 3 5 -1 4 4
2 0 4 2 3 2 4 0 5 3
3 2 1 5 1 1 4 0 3 2
1 4 5 0 2 2 3 1 5 1
0 3 0 5 1 4 0 0 5 3
2 4 3 2 2 5 1 0 4 0
0 5 3 0 1 0 4 3 5 0
1 3 4 1 2 5 1 0 3 4
1 4 3 1 5 5 3 2 2 4
4 -1 2 3 1 0 5 5 1 4
4 2 3 3 4 4 4 2 0 1
"""


def main():
    corpus = ROOT / "data/candidates/classification-v3-qwen7b"
    plan = json.loads((corpus / "translation-plan.json").read_text(encoding="utf-8"))
    rows = {
        row["id"]: row
        for row in (
            json.loads(line)
            for line in (corpus / "translated.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    labels = [int(value) for value in LABELS.split()]
    cases = [rows[key] for key in plan["first_120"]]
    if len(cases) != len(labels) or len(labels) != 120:
        raise RuntimeError("Audit alignment mismatch")
    write_json(
        STAGE / "codex-chinese-v3-audit.json",
        {
            "method": "AI_CODEX_CHINESE_INTENT_AUDIT",
            "human_review": False,
            "purpose": (
                "Chinese classification fitness of synthetic translations/ada"
                "ptations; NOT a bilingual literal-translation gold corpus"
            ),
            "source_plan_hash": plan["source_hash"],
            "decisions": [
                {
                    "id": row["id"],
                    "text_hash": canonical_hash(row["chinese"]),
                    "label": label,
                    "natural": i != 96,
                }
                for i, (row, label) in enumerate(zip(cases, labels, strict=True))
            ],
            "bilingual_caveats": {
                "indices_not_certified_faithful": [
                    17,
                    18,
                    22,
                    24,
                    32,
                    36,
                    41,
                    49,
                    51,
                    59,
                    96,
                    103,
                    108,
                    109,
                ],
                "note": (
                    "The English is licensed provenance, not runtime transaction "
                    "evidence. Requested actions may change during adaptation; in"
                    "dependent Chinese review must agree with proposed route or e"
                    "xclude the case. Do not claim every retained sentence is a f"
                    "aithful literal translation."
                ),
            },
            "gate": {
                "approved_checks_minimum": 60,
                "label_agreement_minimum": 0.95,
                "natural_rate_minimum": 0.90,
            },
        },
    )
    print(
        {
            "new_chinese_texts_read": len(cases),
            "scope": "Chinese intent and usability",
            "paid_calls": 0,
        }
    )


if __name__ == "__main__":
    main()
