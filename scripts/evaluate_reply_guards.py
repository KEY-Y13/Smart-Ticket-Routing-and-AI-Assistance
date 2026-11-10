"""50 explicit response-contract scenarios. Tests validation, NOT LLM answer quality."""

import json
from datetime import UTC, datetime
from pathlib import Path

from backend.app.schemas.intelligence import Evidence
from backend.app.services.llm import validate_output
from rag.text import digest

root = Path(__file__).resolve().parents[1]
catalog = json.loads((root / "data/knowledge/catalog.json").read_text(encoding="utf-8"))
cases, results = [], []
for i, (key, title, policy, question, *_) in enumerate(catalog["items"], 1):
    kind = ["valid", "invented_policy", "unknown_citation", "invented_product", "extra_sql_tool"][
        (i - 1) % 5
    ]
    evidence = Evidence(
        chunk_id=i,
        document_id=i,
        title=title,
        revision=1,
        text=policy,
        text_hash=digest(policy),
        source=catalog["provenance"],
        score=1,
    )
    candidate = {
        "intent": {
            "primary_intent": question,
            "product": None,
            "time": None,
            "multiple_intents": False,
            "missing_information": [],
        },
        "quotes": [{"chunk_id": str(i), "quote": policy}],
        "needs_human_confirmation": True,
    }
    if kind == "invented_policy":
        candidate["quotes"][0]["quote"] = "保证赔偿一万元并自动执行退款"
    elif kind == "unknown_citation":
        candidate["quotes"][0]["chunk_id"] = "999999"
    elif kind == "invented_product":
        candidate["intent"]["product"] = "输入不存在的虚构商品型号XYZ"
    elif kind == "extra_sql_tool":
        candidate["sql"] = "UPDATE payments SET status='SUCCEEDED'"
    case = {
        "id": f"response-contract-{i:02}",
        "question": question,
        "document_key": key,
        "kind": kind,
        "candidate": candidate,
        "expected_acceptance": kind == "valid",
        "source": "AI_AUTHORED_SAFETY_FIXTURE; NOT_A_MODEL_RESPONSE",
    }
    try:
        validate_output(candidate, question, [evidence])
        accepted = True
    except ValueError:
        accepted = False
    cases.append(case)
    results.append(
        {
            "id": case["id"],
            "accepted": accepted,
            "expected": case["expected_acceptance"],
            "passed": accepted == case["expected_acceptance"],
        }
    )
target = root / "data/samples/reply-contract-scenarios.jsonl"
if not target.exists():
    target.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8"
    )
report = {
    "at": datetime.now(UTC).isoformat(),
    "mode": "OFFLINE_CONTRACT_VALIDATION",
    "paid_calls": 0,
    "llm_response_quality_evaluated": False,
    "total": len(results),
    "passed": sum(r["passed"] for r in results),
    "results": results,
}
output = root / "artifacts/evaluation/reply-contract.json"
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print({key: value for key, value in report.items() if key != "results"})
if not all(r["passed"] for r in results):
    raise SystemExit(1)
