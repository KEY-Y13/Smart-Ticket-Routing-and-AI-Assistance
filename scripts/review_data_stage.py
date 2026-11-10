"""Freeze and run the explicitly authorized fictional-business AI review plan (shared budget)."""

import argparse
import json
import os
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import func, select

from backend.app.core.config import Settings
from backend.app.db.models import AIInvocation, ReviewCollection, ReviewItem, User
from backend.app.db.runtime import Database
from backend.app.schemas.reviews import CollectionCreate, Freeze
from backend.app.services import ai_review_import, data_review, reviews
from nlp.artifacts import write_json
from nlp.data.dataset import canonical_hash, normalize_for_leakage

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "artifacts/data-review-v2"
CORPUS = ROOT / "data/candidates/classification-v2-clean"
CODE = "licensed-classification-ai-v2"


def readlines(path):
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def source_groups(rows):
    """Link lexical source variants before translation. Does not prove semantic independence."""
    parents = list(range(len(rows)))
    grams = []
    for row in rows:
        text = re.sub(r"[^a-z0-9]", "", row["english"].lower())
        grams.append({text[i : i + 3] for i in range(len(text) - 2)})

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for i, row in enumerate(rows):
        for j in range(i):
            if row["source_intent"] != rows[j]["source_intent"]:
                continue
            a, b = grams[i], grams[j]
            if min(len(a), len(b)) / max(len(a), len(b)) < 0.8:
                continue
            if len(a & b) / len(a | b) >= 0.8:
                left, right = root(i), root(j)
                parents[max(left, right)] = min(left, right)
    return {row["id"]: "src-" + rows[root(i)]["id"] for i, row in enumerate(rows)}


def prepare(db, settings):
    STAGE.mkdir(parents=True, exist_ok=True)
    if (STAGE / "plan.json").exists():
        raise RuntimeError("Plan already frozen; run it without changing partitions")
    translated = readlines(CORPUS / "qwen-translated-candidates.jsonl")
    if len(translated) != len(readlines(CORPUS / "english-candidates.jsonl")):
        raise RuntimeError("Translation not complete")
    groups = source_groups(translated)
    normalized = defaultdict(list)
    for row in translated:
        normalized[normalize_for_leakage(row["chinese"])].append(row)
    selected, excluded = [], []
    for matches in normalized.values():
        for index, row in enumerate(matches):
            chinese = row["chinese"]
            han = len(re.findall(r"[\u4e00-\u9fff]", chinese))
            reason = None
            if len({r["proposed_label"] for r in matches}) > 1:
                reason = "duplicate_label_conflict"
            elif index:
                reason = "normalized_exact_duplicate"
            elif len(chinese) < 6 or len(chinese) > 250 or han < max(5, len(chinese) * 0.5):
                reason = "language_or_length"
            elif any(token in chinese for token in ("```", "</think>", '"english"', "译文：")):
                reason = "translation_artifact"
            if reason:
                excluded.append({"id": row["id"], "reason": reason})
            else:
                selected.append(row)
    # The plan includes 72 legacy Chinese checks and 20 new edge cases; max 4x700.
    rng = random.Random(20261110)
    rng.shuffle(selected)
    if len(selected) > 2708:
        excluded.extend(
            {"id": row["id"], "reason": "predeclared_review_budget_cap"} for row in selected[2708:]
        )
        selected = selected[:2708]
    lineage = {row["id"]: {**row, "source_group": groups[row["id"]]} for row in selected}
    candidates = [
        {
            "sample_id": row["id"],
            "text": row["chinese"],
            "label": row["proposed_label"],
            "group_id": groups[row["id"]],
            "source_kind": "synthetic",
            "source_ref": (
                f"Bitext-retail@{row['source_revision']}/{row['id']}; "
                f"en-sha256:{row['source_sha256']}; local-Qwen-translation; "
                "lineage:artifacts/data-review-v2/lineage.json"
            ),
            "source_license": "CDLA-Sharing-1.0",
            "rights_status": "licensed",
            "review_status": "unreviewed",
            "reviewer": None,
        }
        for row in selected
    ]
    with db.sessions() as session:
        actor = session.scalar(select(User).where(User.login == "admin"))
        collection = reviews.create_collection(
            session,
            actor,
            CollectionCreate(
                code=CODE,
                title=f"许可语料中文分类 · AI盲审 · {len(candidates)}条",
                kind="CLASSIFICATION",
                provenance=(
                    "Bitext English hybrid synthetic, pinned licensed source; "
                    "local Qwen translation; AI review only, NOT HUMAN GOLD"
                ),
                items=candidates,
            ),
            "data-stage-v2-prepare",
        )
        class_cases, business_cases, llm_cases = [], [], []
        for collection_row in session.scalars(
            select(ReviewCollection).order_by(ReviewCollection.id)
        ):
            if collection_row.code not in {
                CODE,
                "reference-classification-v1",
                "reference-knowledge-v1",
                "reference-retrieval-v1",
                "reference-llm-v1",
            }:
                continue
            for item in session.scalars(
                select(ReviewItem)
                .where(ReviewItem.collection_id == collection_row.id)
                .order_by(ReviewItem.id)
            ):
                case = {
                    "id": f"review-{item.id}",
                    "review_item_id": item.id,
                    "content_hash": item.content_hash,
                }
                if collection_row.kind == "CLASSIFICATION":
                    case.update(
                        chinese=item.payload["text"],
                        english=lineage[item.item_key]["english"]
                        if collection_row.code == CODE
                        else None,
                    )
                    class_cases.append(case)
                else:
                    original = dict(item.payload)
                    for key in (
                        "relevant_document_keys",
                        "answerable",
                        "suggested_document_key",
                        "expected_document_key",
                        "document_key",
                    ):
                        original.pop(key, None)
                    case.update(kind=collection_row.kind, original=original)
                    (llm_cases if collection_row.kind == "LLM" else business_cases).append(case)
        challenge = readlines(ROOT / "data/samples/classification-challenge-v2.jsonl")
        class_cases.extend(
            {"id": row["id"], "chinese": row["text"], "english": None, "benchmark_only": True}
            for row in challenge
        )
        rng.shuffle(class_cases)
        catalog = json.loads((ROOT / "data/knowledge/catalog.json").read_text(encoding="utf-8"))
        baseline = {
            "scope": (
                "FICTIONAL_3C_PROJECT_ONLY; canonical project policy plus consistency check, "
                "not external law/company certification"
            ),
            "rules": (
                "核验归属，禁止索要密码验证码，禁止自动退款付款，不能承诺固定期限/赔偿/保修结果；"
                "安全风险先停止使用并转人工。"
            ),
            "documents": [
                {"document_key": row[0], "title": row[1], "content": row[2]}
                for row in catalog["items"]
            ],
        }
        batches = [
            {
                "name": f"classification-{i // 700 + 1}",
                "kind": "CLASSIFICATION",
                "cases": class_cases[i : i + 700],
                "baseline": None,
            }
            for i in range(0, len(class_cases), 700)
        ]
        batches.extend(
            [
                {
                    "name": "knowledge-and-retrieval",
                    "kind": "BUSINESS",
                    "cases": business_cases,
                    "baseline": baseline,
                },
                {
                    "name": "historical-llm-outputs",
                    "kind": "BUSINESS",
                    "cases": llm_cases,
                    "baseline": baseline,
                },
            ]
        )
        used = session.scalar(select(func.count()).select_from(AIInvocation))
        if len(batches) > settings.llm_call_budget - used:
            raise RuntimeError("Frozen plan exceeds existing shared budget; no automatic increase")
    write_json(STAGE / "lineage.json", lineage)
    write_json(
        STAGE / "selection.json",
        {
            "translated": len(translated),
            "selected": len(candidates),
            "counts": dict(Counter(row["label"] for row in candidates)),
            "lexical_source_groups": len({row["group_id"] for row in candidates}),
            "excluded": excluded,
            "semantic_independence": "not_proven",
        },
    )
    write_json(
        STAGE / "challenge-truth-frozen.json",
        {
            "cases": challenge,
            "note": (
                "AI-authored before model training, provider blind-reviewed; never training data"
            ),
        },
    )
    write_json(
        STAGE / "plan.json",
        {
            "protocol": "fictional-ai-review-v1",
            "collection_id": str(collection.id),
            "budget_before": used,
            "budget_cap": settings.llm_call_budget,
            "batches": batches,
            "plan_hash": canonical_hash(batches),
        },
    )
    print(
        {
            "selected": len(candidates),
            "batches": [(b["name"], len(b["cases"])) for b in batches],
            "paid_calls": 0,
        }
    )


def run(db, settings, batch_name):
    plan = json.loads((STAGE / "plan.json").read_text(encoding="utf-8"))
    if batch_name.startswith("calibration-"):
        plan = json.loads((STAGE / "calibration-plan.json").read_text(encoding="utf-8"))
    if canonical_hash(plan["batches"]) != plan["plan_hash"]:
        raise RuntimeError("Plan modified after freeze")
    batch = next(row for row in plan["batches"] if row["name"] == batch_name)
    with db.sessions() as session:
        actor = session.scalar(select(User).where(User.login == "admin"))
        call = data_review.execute(
            session,
            actor,
            settings,
            kind=batch["kind"],
            cases=batch["cases"],
            baseline=batch["baseline"],
        )
        print({"batch": batch_name, "invocation_id": call.id, "status": call.status}, flush=True)
        report = STAGE / (batch_name + ".json")
        if not report.exists():
            write_json(
                report,
                {"invocation_id": call.id, "usage": call.usage, "review": call.review_result},
            )
        print(ai_review_import.apply_results(session, actor, call.id), flush=True)


def prepare_calibration(db):
    plan = json.loads((STAGE / "plan.json").read_text(encoding="utf-8"))
    # Only never-submitted cases. The first two failed batches are NOT retried.
    pending = [
        case
        for batch in plan["batches"]
        if batch["name"] in {"classification-3", "classification-4"}
        for case in batch["cases"]
    ]
    pools, extras = defaultdict(list), []
    with db.sessions() as session:
        for case in pending:
            if case.get("benchmark_only") or case["review_item_id"] <= 72:
                extras.append(case)
            else:
                item = session.get(ReviewItem, case["review_item_id"])
                pools[item.payload["label"]].append(case)
    rng = random.Random(20261111)
    for pool in [*pools.values(), extras]:
        rng.shuffle(pool)
    batches = []
    for index in range(2):
        cases = [case for label in range(6) for case in pools[label][index * 30 : (index + 1) * 30]]
        cases.extend(extras[index * 20 : (index + 1) * 20])
        rng.shuffle(cases)
        batches.append(
            {
                "name": f"calibration-{index + 1}",
                "kind": "CLASSIFICATION",
                "cases": cases,
                "baseline": None,
            }
        )
    write_json(
        STAGE / "calibration-plan.json",
        {
            "batches": batches,
            "plan_hash": canonical_hash(batches),
            "replaces_unsubmitted": ["classification-3", "classification-4"],
            "failed_batches_not_retried": ["classification-1", "classification-2"],
            "reason": (
                "Large remote batches truncated/timed out; separate local rev"
                "iewer plus small, fixed remote cross-checks"
            ),
            "gate": {
                "minimum_approved_checks": 100,
                "label_agreement": 0.95,
                "translation_pass": 0.90,
            },
            "note": (
                "AI cross-check agreement, NOT human gold accuracy. Freeze be"
                "fore local review/training."
            ),
        },
    )
    print({"calibration_cases": [len(batch["cases"]) for batch in batches], "paid_calls": 0})


def export(db, settings):
    with db.sessions() as session:
        actor = session.scalar(select(User).where(User.login == "admin"))
        collection = session.scalar(select(ReviewCollection).where(ReviewCollection.code == CODE))
        snapshot = reviews.freeze(
            session,
            actor,
            collection.id,
            Freeze(
                reason="虚构业务标准下AI盲审；非人工金标准，仅离线与影子验证", acknowledged=True
            ),
            "ai-data-stage-export",
        )
        target = ROOT / "data/processed/classification-ai-v2.jsonl"
        with target.open("x", encoding="utf-8") as handle:
            handle.write(reviews.download(session, actor, int(snapshot.id)))
        write_json(STAGE / "snapshot.json", snapshot.model_dump(mode="json"))
        print(snapshot.model_dump(mode="json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "export", "prepare-calibration"])
    parser.add_argument("--batch")
    args = parser.parse_args()
    # Reuse authorized local user environment key without printing it.
    if not os.getenv("DASHSCOPE_API_KEY"):
        import subprocess

        key = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Environment]::GetEnvironmentVariable('DASHSCOPE_API_KEY','User')",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if key:
            os.environ["DASHSCOPE_API_KEY"] = key
    settings = Settings(_env_file=ROOT / ".env")
    db = Database(settings)
    try:
        if args.action == "prepare":
            prepare(db, settings)
        elif args.action == "run":
            run(db, settings, args.batch)
        elif args.action == "prepare-calibration":
            prepare_calibration(db)
        else:
            export(db, settings)
    finally:
        db.dispose()


if __name__ == "__main__":
    main()
