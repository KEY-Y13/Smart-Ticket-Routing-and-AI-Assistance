"""Import provider decisions with explicit AI attribution; never impersonate human review."""

import hashlib
import json
import secrets

from sqlalchemy import select

from backend.app.core.errors import AppError
from backend.app.core.security import hash_password
from backend.app.db.models import (
    AIInvocation,
    KnowledgeDocument,
    ReviewCollection,
    ReviewDecision,
    ReviewItem,
    User,
)
from backend.app.services.admin import audit
from backend.app.services.reviews import ANNOTATIONS
from backend.app.services.tickets import lock_dispatch, require_role


def apply_results(session, actor, invocation_id):
    require_role(actor, "ADMIN")
    lock_dispatch(session)
    invocation = session.get(AIInvocation, invocation_id)
    if not invocation or invocation.purpose != "DATA_REVIEW" or invocation.status != "SUCCEEDED":
        raise AppError(409, "AI_REVIEW_NOT_SUCCEEDED", "只能导入账本中已成功的AI评审")
    evidence = invocation.review_result
    if (
        evidence.get("human_review") is not False
        or evidence.get("protocol") != "fictional-ai-review-v1"
    ):
        raise AppError(409, "AI_REVIEW_PROTOCOL", "AI评审协议不匹配")
    return _apply(
        session,
        actor,
        evidence,
        invocation.model,
        {"invocation_id": invocation.id, "evidence_ref": f"invocation:{invocation.id}"},
    )


def apply_offline(session, actor, settings, report_path):
    """Operator-run AI report, distinct from a paid provider invocation and human confirmation."""
    require_role(actor, "ADMIN")
    path = report_path.resolve()
    roots = [
        (settings.artifact_root / name).resolve() for name in ("data-review-v2", "data-review-v3")
    ]
    if (
        not any(path.is_relative_to(root) for root in roots)
        or path.is_symlink()
        or path.stat().st_size > 20_000_000
    ):
        raise AppError(422, "INVALID_AI_REVIEW_REPORT", "AI离线报告必须位于本轮受控目录")
    raw = path.read_bytes()
    report = json.loads(raw)
    evidence = report["review"]
    if evidence["human_review"] is not False or evidence["protocol"] != "fictional-ai-review-v1":
        raise AppError(422, "INVALID_AI_REVIEW_REPORT", "离线报告须明确AI身份及协议")
    if report["method"] not in {
        "AI_CODEX_BUSINESS_REVIEW",
        "AI_CODEX_CLASSIFICATION_REVIEW",
        "AI_LOCAL_CLASSIFICATION_REVIEW",
    }:
        raise AppError(422, "INVALID_AI_REVIEW_METHOD", "无效的AI复核方式")
    from backend.app.services.data_review import _validate

    decisions = evidence["decisions"]
    if evidence["kind"] == "BUSINESS":
        _validate("BUSINESS", evidence["input"], {"decisions": decisions})
    else:
        quality_key = (
            "natural"
            if evidence.get("assessment_scope") == "CHINESE_INTENT_AND_NATURALNESS"
            else "translation_ok"
        )
        _validate(
            "CLASSIFICATION",
            evidence["input"],
            {
                "labels": [row["label"] for row in decisions],
                "translation_ok": [row[quality_key] for row in decisions],
            },
        )
    lock_dispatch(session)
    return _apply(
        session,
        actor,
        evidence,
        report["reviewer_model"],
        {
            "run_ref": path.relative_to(settings.artifact_root.resolve()).as_posix(),
            "evidence_ref": "local-sha256:" + hashlib.sha256(raw).hexdigest(),
            "method": report["method"],
        },
    )


def _apply(session, actor, evidence, model, attribution):
    reviewer = session.scalar(select(User).where(User.login == "system-ai-review"))
    if reviewer is None:
        reviewer = User(
            login="system-ai-review",
            role="ADMIN",
            enabled=False,
            password_hash=hash_password(secrets.token_urlsafe(48)),
        )
        session.add(reviewer)
        session.flush()
    if reviewer.enabled:
        raise AppError(409, "AI_REVIEW_IDENTITY", "AI身份必须禁止登录")
    inputs = {row["id"]: row for row in evidence["input"]}
    if len(evidence["decisions"]) != len(inputs) or {
        row["id"] for row in evidence["decisions"]
    } != set(inputs):
        raise AppError(409, "AI_REVIEW_INCOMPLETE", "AI评审缺少样本")
    applied = skipped = 0
    known = set(session.scalars(select(KnowledgeDocument.document_key)))
    for result in evidence["decisions"]:
        case = inputs[result["id"]]
        if case.get("benchmark_only") is True:
            skipped += 1
            continue
        item = session.scalar(
            select(ReviewItem)
            .where(ReviewItem.id == case["review_item_id"])
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if item is None or case["content_hash"] != item.content_hash:
            raise AppError(409, "AI_REVIEW_SOURCE_CHANGED", "评审正文已变更，不能套用旧结论")
        old = session.scalar(
            select(ReviewDecision)
            .where(ReviewDecision.item_id == item.id)
            .order_by(ReviewDecision.id.desc())
            .limit(1)
        )
        if old:
            if old.annotation.get("_ai", {}).get("evidence_ref") == attribution["evidence_ref"]:
                skipped += 1
                continue
            raise AppError(409, "AI_REVIEW_ALREADY_DECIDED", "已有其他复核结论，不能批量覆盖")
        kind = session.get(ReviewCollection, item.collection_id).kind
        if evidence["kind"] == "CLASSIFICATION":
            if kind != "CLASSIFICATION" or case["chinese"] != item.payload["text"]:
                raise AppError(409, "AI_REVIEW_SOURCE_CHANGED", "分类评审文本不匹配")
            chinese_only = evidence.get("assessment_scope") == "CHINESE_INTENT_AND_NATURALNESS"
            quality_key = "natural" if chinese_only else "translation_ok"
            approved = result[quality_key] and result["label"] == item.payload["label"]
            annotation = {key: item.payload[key] for key in ("label", "group_id", "rights_status")}
            quality_name = "中文可用" if chinese_only else "译文合格"
            reason = result.get("reason") or (
                f"AI分类复核：判定类别{result['label']}，{quality_name}={result[quality_key]}；"
                "仅质量合格且标签一致时纳入。"
            )
        else:
            if case["kind"] != kind:
                raise AppError(409, "AI_REVIEW_KIND_CHANGED", "业务评审类型不匹配")
            approved, annotation, reason = True, result["annotation"], result["reason"]
            if kind == "RETRIEVAL" and set(annotation["relevant_document_keys"]) - known:
                raise AppError(422, "UNKNOWN_REVIEW_DOCUMENT", "AI引用了不存在的知识文档")
            if kind == "LLM" and item.payload.get("refused"):
                expected = {"ACCEPTABLE": True, "INCORRECT": False, "UNDETERMINED": None}
                if annotation["refusal_correct"] is not expected[annotation["verdict"]]:
                    raise AppError(422, "INVALID_REFUSAL_REVIEW", "AI回复与拒答结论不一致")
        annotation = ANNOTATIONS[kind].model_validate(annotation).model_dump()
        annotation["_ai"] = {
            **attribution,
            "model": model,
            "protocol": evidence["protocol"],
            "human_review": False,
            "assessment_scope": evidence.get("assessment_scope", "BILINGUAL_OR_BUSINESS"),
            "raw_decision": result,
        }
        decision = ReviewDecision(
            item_id=item.id,
            item_version=item.version,
            content_hash=item.content_hash,
            reviewer_id=reviewer.id,
            decision="APPROVED" if approved else "REJECTED",
            annotation=annotation,
            reason=reason,
        )
        session.add(decision)
        item.status = decision.decision
        item.version += 1
        applied += 1
    audit(
        session,
        actor,
        "AI_REVIEW_IMPORTED",
        "ai_review_identity",
        reviewer.id,
        {
            "applied": applied,
            "skipped": skipped,
            "human_review": False,
            "evidence_ref": attribution["evidence_ref"],
            "model": model,
        },
        "ai-data-review-import",
    )
    session.commit()
    return {
        "evidence_ref": attribution["evidence_ref"],
        "applied": applied,
        "skipped": skipped,
        "human_review": False,
    }
