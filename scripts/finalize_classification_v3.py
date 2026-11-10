"""Freeze the AI-reviewed Chinese classification corpus and split before fitting any classifier."""

import json
from collections import Counter, defaultdict

from sqlalchemy import select

from backend.app.db.models import ReviewItem, User
from backend.app.schemas.reviews import CollectionCreate, Freeze
from backend.app.services import ai_review_import, reviews
from nlp.artifacts import write_json
from nlp.data.dataset import (
    canonical_hash,
    load_samples,
    normalize_for_leakage,
    prepare_manifest,
    split_samples,
    verify_manifest,
)
from scripts.review_data_stage import ROOT, STAGE, Database, Settings

CORPUS = ROOT / "data/candidates/classification-v3-qwen7b"
OUT = ROOT / "artifacts/data-review-v3"
CODE = "classification-ai-v3"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    plan = json.loads((CORPUS / "translation-plan.json").read_text(encoding="utf-8"))
    rows = {
        row["id"]: row
        for row in (
            json.loads(line)
            for line in (CORPUS / "translated.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    judgments = {
        row["id"]: row
        for row in (
            json.loads(line)
            for line in (CORPUS / "chinese-review.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    expected = {row["id"] for row in plan["rows"]}
    if set(rows) != expected or set(judgments) != expected:
        raise RuntimeError("Translation or per-row Chinese review is incomplete")
    audit = json.loads((STAGE / "codex-chinese-v3-audit.json").read_text(encoding="utf-8"))
    checks, seen = [], set()
    for truth in audit["decisions"]:
        row, judgment = rows[truth["id"]], judgments[truth["id"]]["decision"]
        if canonical_hash(row["chinese"]) != truth["text_hash"]:
            raise RuntimeError("Audited Chinese text changed")
        text_key = normalize_for_leakage(row["chinese"])
        if text_key in seen:
            continue
        seen.add(text_key)
        if judgment and judgment["natural"] and judgment["label"] == row["proposed_label"]:
            checks.append(
                {
                    "id": row["id"],
                    "label_match": judgment["label"] == truth["label"],
                    "natural": truth["natural"],
                }
            )
    measured = {
        "checked_unique_approved": len(checks),
        "label_agreement": sum(row["label_match"] for row in checks) / len(checks) if checks else 0,
        "natural_rate": sum(row["natural"] for row in checks) / len(checks) if checks else 0,
        "reference": "Codex AI Chinese-intent audit, not human gold accuracy",
        "checks": checks,
    }
    passed = (
        len(checks) >= 60
        and measured["label_agreement"] >= 0.95
        and measured["natural_rate"] >= 0.90
    )
    measured["passed"] = passed
    if not (OUT / "reviewer-audit.json").exists():
        write_json(OUT / "reviewer-audit.json", measured)
    if not passed:
        raise RuntimeError("Chinese-label review gate did not pass; no bulk approval or training")
    # Remove exact normalized duplicates and source-label conflicts BEFORE any split.
    by_text = defaultdict(list)
    excluded = []
    for row in rows.values():
        if not 6 <= len(row["chinese"].strip()) <= 4000:
            excluded.append({"id": row["id"], "reason": "sample_schema_length"})
            continue
        by_text[normalize_for_leakage(row["chinese"])].append(row)
    candidates = []
    for matches in by_text.values():
        matches.sort(key=lambda row: row["id"])
        if len({row["proposed_label"] for row in matches}) > 1:
            excluded.extend(
                {"id": row["id"], "reason": "normalized_label_conflict"} for row in matches
            )
            continue
        candidates.append(matches[0])
        excluded.extend(
            {"id": row["id"], "reason": "normalized_exact_duplicate"} for row in matches[1:]
        )
    candidates.sort(key=lambda row: row["id"])
    payloads = [
        {
            "sample_id": row["id"],
            "text": row["chinese"],
            "label": row["proposed_label"],
            "group_id": row["source_group"],
            "source_kind": "synthetic",
            "source_license": "CDLA-Sharing-1.0",
            "source_ref": (
                f"Bitext-retail@{row['source_revision']}/{row['id']}; "
                "local-Qwen7B translation/adaptation; "
                "English and hashes: artifacts/data-review-v3/lineage.json"
            ),
            "rights_status": "licensed",
            "review_status": "unreviewed",
            "reviewer": None,
        }
        for row in candidates
    ]
    settings = Settings(_env_file=ROOT / ".env")
    db = Database(settings)
    try:
        with db.sessions() as session:
            actor = session.scalar(select(User).where(User.login == "admin"))
            collection = reviews.create_collection(
                session,
                actor,
                CollectionCreate(
                    code=CODE,
                    title=f"中文分类正式候选 · AI复核 · {len(payloads)}条",
                    kind="CLASSIFICATION",
                    provenance=(
                        "Licensed Bitext hybrid synthetic -> local Qwen7B translation"
                        "/adaptation -> independent Chinese-only Qwen1.5B intent/natu"
                        "ralness review + Codex 120-case audit. NOT human gold or lit"
                        "eral parallel corpus."
                    ),
                    items=payloads,
                ),
                "v3-candidate-import",
            )
            cases, decisions = [], []
            for item in session.scalars(
                select(ReviewItem)
                .where(ReviewItem.collection_id == int(collection.id))
                .order_by(ReviewItem.id)
            ):
                judgment = judgments[item.item_key]["decision"]
                case = {
                    "id": f"v3-review-{item.id}",
                    "review_item_id": item.id,
                    "content_hash": item.content_hash,
                    "chinese": item.payload["text"],
                    "english": None,
                }
                cases.append(case)
                decisions.append(
                    {
                        "id": case["id"],
                        "label": judgment["label"] if judgment else -1,
                        "natural": judgment["natural"] if judgment else False,
                    }
                )
            path = OUT / "chinese-review-decisions.json"
            if not path.exists():
                write_json(
                    path,
                    {
                        "method": "AI_LOCAL_CLASSIFICATION_REVIEW",
                        "reviewer_model": "Qwen/Qwen2.5-1.5B-Instruct",
                        "audit": measured,
                        "review": {
                            "kind": "CLASSIFICATION",
                            "assessment_scope": "CHINESE_INTENT_AND_NATURALNESS",
                            "input": cases,
                            "decisions": decisions,
                            "protocol": "fictional-ai-review-v1",
                            "human_review": False,
                        },
                    },
                )
            print(ai_review_import.apply_offline(session, actor, settings, path), flush=True)
            frozen = reviews.freeze(
                session,
                actor,
                int(collection.id),
                Freeze(
                    reason="许可合成中文工单的主诉与可用性AI复核；源文本为翻译/改编，非逐字双语金标准或人工认证",
                    acknowledged=True,
                ),
                "v3-freeze",
            )
            target = ROOT / "data/processed/classification-ai-v3.jsonl"
            if not target.exists():
                with target.open("x", encoding="utf-8") as handle:
                    handle.write(reviews.download(session, actor, int(frozen.id)))
            if not (OUT / "snapshot.json").exists():
                write_json(OUT / "snapshot.json", frozen.model_dump(mode="json"))
    finally:
        db.dispose()
    samples = load_samples(target)
    write_json(
        OUT / "lineage.json", {sample.sample_id: rows[sample.sample_id] for sample in samples}
    )
    if len(samples) < 1200:
        raise RuntimeError(
            f"Only {len(samples)} approved distinct texts: below 1200; do not train as completed"
        )
    manifest = prepare_manifest(target, "formal", seed=20261114)
    calibration = json.loads((STAGE / "calibration-plan.json").read_text(encoding="utf-8"))
    calibration_english = {
        case["english"]
        for batch in calibration["batches"]
        for case in batch["cases"]
        if case["english"] is not None
    }
    observed_ids = {row["id"] for row in candidates if row["english"] in calibration_english} | set(
        plan["first_120"]
    )
    pinned_groups = {
        manifest["groups"][sample.sample_id]
        for sample in samples
        if sample.sample_id in observed_ids
    }
    pinned = [sample for sample in samples if manifest["groups"][sample.sample_id] in pinned_groups]
    remainder = [
        sample for sample in samples if manifest["groups"][sample.sample_id] not in pinned_groups
    ]
    parts = split_samples(remainder, manifest["groups"], seed=20261114)
    parts["train"].extend(pinned)
    manifest["splits"] = {
        name: sorted(sample.sample_id for sample in part) for name, part in parts.items()
    }
    manifest["distribution"] = {
        name: {str(label): sum(sample.label == label for sample in part) for label in range(6)}
        for name, part in parts.items()
    }
    manifest["train_only_ids"] = sorted(sample.sample_id for sample in pinned)
    manifest["review_scope"] = (
        "AI-reviewed Chinese primary-intent labels and usability; lic"
        "ensed synthetic translations/adaptations; not literal biling"
        "ual gold"
    )
    manifest["limitations"].append(
        "Reviewer calibration/audit groups are training-only; test re"
        "mains unseen by classifier fitting and threshold selection."
    )
    verify_manifest(samples, manifest)
    write_json(OUT / "split-manifest.json", manifest)
    write_json(
        OUT / "summary.json",
        {
            "source_selected": len(rows),
            "unique_candidates": len(candidates),
            "approved": len(samples),
            "class_counts": dict(Counter(sample.label for sample in samples)),
            "excluded_before_review": excluded,
            "reviewer_audit": measured,
            "split_counts": {name: len(part) for name, part in parts.items()},
            "group_audit": manifest["audit"],
            "human_review": False,
            "classifier_training_started": False,
            "source_snapshot_id": str(frozen.id),
            "source_license": "CDLA-Sharing-1.0",
            "production_scope": "excluded by user",
        },
    )
    print(
        {
            "approved": len(samples),
            "splits": {name: len(part) for name, part in parts.items()},
            "ready_for_formal_offline_training": True,
        },
        flush=True,
    )


if __name__ == "__main__":
    main()
