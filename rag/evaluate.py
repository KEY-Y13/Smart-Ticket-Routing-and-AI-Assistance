"""Four real retrievers, saved per-query rankings and measured wall latency."""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.db.models import KnowledgeDocument, ReviewCollection, ReviewSnapshot
from backend.app.db.runtime import Database
from backend.app.schemas.intelligence import Search
from backend.app.services.knowledge import search
from backend.app.services.reviews import snapshot_current


def reviewed_cases(session, snapshot_id):
    snapshot = session.get(ReviewSnapshot, snapshot_id)
    if (
        snapshot is None
        or session.get(ReviewCollection, snapshot.collection_id).kind != "RETRIEVAL"
    ):
        raise AppError(422, "RETRIEVAL_SNAPSHOT_REQUIRED", "需要检索真值的冻结版本")
    if not snapshot_current(session, snapshot):
        raise AppError(409, "REVIEW_SUPERSEDED", "复核已变化，请冻结新版本后重新评测")
    return [
        {
            "id": row["id"],
            "query": row["original"]["query"],
            **{key: value for key, value in row["annotation"].items() if key != "_ai"},
        }
        for row in snapshot.rows
    ], {
        "snapshot_id": str(snapshot.id),
        "snapshot_sha256": snapshot.snapshot_sha256,
        "review_method": "AI"
        if any(row["annotation"].get("_ai") for row in snapshot.rows)
        else "MANUAL_LEDGER",
        "human_gold_certified": False,
        "scope": "review ledger bound; policy validity and production approval are separate",
    }


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-snapshot", type=int)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.review_snapshot and (not args.out or args.out.exists()):
        parser.error("复核评测需要显式指定尚不存在的--out，不能覆盖历史报告")
    settings = Settings(_env_file=root / ".env")
    db = Database(settings)
    cases = [
        json.loads(line)
        for line in (root / "data/samples/retrieval-demo.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    review = None
    if args.review_snapshot:
        with db.sessions() as session:
            cases, review = reviewed_cases(session, args.review_snapshot)
    report = {
        "at": datetime.now(UTC).isoformat(),
        "dataset": "Frozen AI-reviewed fictional relevance decisions"
        if review
        else "110 AI-authored fictional relevance cases, not independently reviewed",
        "review": review,
        "deployment_approved": False,
        "strategies": {},
    }
    for strategy in ("bm25", "vector", "hybrid", "rerank"):
        results = []
        with db.sessions() as session:
            keys = {row.id: row.document_key for row in session.scalars(select(KnowledgeDocument))}
            for case in cases:
                start = time.perf_counter()
                result = search(session, settings, Search(query=case["query"], strategy=strategy))
                latency = (time.perf_counter() - start) * 1000
                ranked = list(dict.fromkeys(keys[row.document_id] for row in result.items))[:5]
                relevant = set(case["relevant_document_keys"])
                ranks = [i + 1 for i, key in enumerate(ranked) if key in relevant]
                results.append(
                    {
                        **case,
                        "ranked_document_keys": ranked,
                        "recall_at_5": len(set(ranked) & relevant) / len(relevant)
                        if relevant
                        else None,
                        "reciprocal_rank_at_5": 1 / min(ranks)
                        if ranks
                        else 0
                        if relevant
                        else None,
                        "latency_ms": latency,
                        "sufficient": result.sufficient,
                    }
                )
                if len(results) % 10 == 0:
                    print(strategy, len(results), flush=True)
        answerable = [r for r in results if r["answerable"]]
        unanswerable = [r for r in results if not r["answerable"]]
        report["strategies"][strategy] = {
            "answerable_count": len(answerable),
            "unanswerable_count": len(unanswerable),
            "recall_at_5": float(np.mean([r["recall_at_5"] for r in answerable]))
            if answerable
            else None,
            "mrr_at_5": float(np.mean([r["reciprocal_rank_at_5"] for r in answerable]))
            if answerable
            else None,
            "p50_ms": float(np.percentile([r["latency_ms"] for r in results], 50)),
            "p95_ms": float(np.percentile([r["latency_ms"] for r in results], 95)),
            "unanswerable_evidence_gate_pass_count": sum(r["sufficient"] for r in unanswerable),
            "results": results,
        }
        if review:
            with db.sessions() as session:
                reviewed_cases(session, args.review_snapshot)  # Fail if corrected during the run.
        output = args.out or root / "artifacts/evaluation/retrieval-demo.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    db.dispose()
    print("Saved real retrieval evaluation", output)


if __name__ == "__main__":
    main()
