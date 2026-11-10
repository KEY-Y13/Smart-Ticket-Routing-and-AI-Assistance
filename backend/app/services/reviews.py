"""Attributed review ledger, immutable exports and model gates. No training or LLM calls."""

import importlib.metadata
import json
import re
import shutil
from pathlib import PurePosixPath
from uuid import uuid4

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import func, select

from backend.app.core.errors import AppError
from backend.app.db.models import (
    KnowledgeDocument,
    ModelRelease,
    ReviewCollection,
    ReviewDecision,
    ReviewItem,
    ReviewSnapshot,
    User,
    utcnow,
)
from backend.app.schemas import reviews as s
from backend.app.schemas.business import Input
from backend.app.services.admin import audit
from backend.app.services.tickets import check_version, lock_dispatch, require_role
from nlp.artifacts import file_hash, write_json
from nlp.data.dataset import canonical_hash, verify_manifest
from nlp.data.schema import Identifier, TicketSample
from nlp.labels import label_map_hash


class ClassificationAnnotation(Input):
    label: int = Field(ge=0, le=5, strict=True)
    group_id: Identifier
    rights_status: str = Field(pattern=r"^(project_internal|licensed)$")
    corrected_text: str | None = Field(default=None, min_length=6, max_length=4000)


class KnowledgeAnnotation(Input):
    verdict: str = Field(pattern=r"^(DEMO_VALID|INVALID|UNDETERMINED)$")


class RetrievalAnnotation(Input):
    answerable: bool
    relevant_document_keys: list[str] = Field(max_length=10)

    @model_validator(mode="after")
    def consistent(self):
        if bool(self.relevant_document_keys) != self.answerable:
            raise ValueError("可回答必须标注依据；应拒答时依据列表应为空")
        if len(set(self.relevant_document_keys)) != len(self.relevant_document_keys):
            raise ValueError("依据不能重复")
        return self


class LLMAnnotation(Input):
    verdict: str = Field(pattern=r"^(ACCEPTABLE|INCORRECT|UNDETERMINED)$")
    refusal_correct: bool | None = None


ANNOTATIONS = {
    "CLASSIFICATION": ClassificationAnnotation,
    "KNOWLEDGE": KnowledgeAnnotation,
    "RETRIEVAL": RetrievalAnnotation,
    "LLM": LLMAnnotation,
}


def get_collection(session, collection_id):
    value = session.get(ReviewCollection, collection_id)
    if value is None:
        raise AppError(404, "REVIEW_COLLECTION_NOT_FOUND", "审核批次不存在")
    return value


def collections(session, user):
    require_role(user, "ADMIN")
    result = []
    for row in session.scalars(select(ReviewCollection).order_by(ReviewCollection.id.desc())):
        counts = dict(
            session.execute(
                select(ReviewItem.status, func.count())
                .where(
                    ReviewItem.collection_id == row.id,
                )
                .group_by(ReviewItem.status)
            ).all()
        )
        result.append(
            s.CollectionView(
                id=row.id,
                code=row.code,
                title=row.title,
                kind=row.kind,
                provenance=row.provenance,
                source_sha256=row.source_sha256,
                counts={key: counts.get(key, 0) for key in ("PENDING", "APPROVED", "REJECTED")},
            )
        )
    return result


def create_collection(session, user, body, request_id):
    require_role(user, "ADMIN")
    lock_dispatch(session)
    rows = []
    for payload in body.items:
        try:
            json.dumps(payload, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise AppError(422, "INVALID_REVIEW_ITEM", "样本必须是有限值JSON数据") from exc
        if body.kind == "CLASSIFICATION":
            try:
                payload = TicketSample.model_validate(payload).model_dump()
            except ValidationError as exc:
                raise AppError(422, "INVALID_REVIEW_ITEM", "分类样本不符合数据规范") from exc
            key = payload["sample_id"]
        else:
            key = str(payload.get("id", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", key):
                raise AppError(422, "INVALID_REVIEW_ITEM", "样本需要唯一、合法的id")
            needed = {"KNOWLEDGE": "content", "RETRIEVAL": "query", "LLM": "question"}[body.kind]
            if not isinstance(payload.get(needed), str) or not payload[needed].strip():
                raise AppError(422, "INVALID_REVIEW_ITEM", "样本缺少正文或问题")
        rows.append((key, payload))
    if len({key for key, _ in rows}) != len(rows):
        raise AppError(422, "DUPLICATE_REVIEW_ITEM", "批次内样本id不能重复")
    fingerprint = canonical_hash({"kind": body.kind, "provenance": body.provenance, "rows": rows})
    existing = session.scalar(select(ReviewCollection).where(ReviewCollection.code == body.code))
    if existing:
        if existing.source_sha256 != fingerprint:
            raise AppError(409, "REVIEW_SOURCE_CHANGED", "批次标识已有不同来源，请使用新版本标识")
        return next(row for row in collections(session, user) if int(row.id) == existing.id)
    value = ReviewCollection(
        code=body.code,
        title=body.title,
        kind=body.kind,
        provenance=body.provenance,
        source_sha256=fingerprint,
        imported_by=user.id,
    )
    session.add(value)
    session.flush()
    for key, payload in rows:
        session.add(
            ReviewItem(
                collection_id=value.id,
                item_key=key,
                payload=payload,
                content_hash=canonical_hash(payload),
                status="PENDING",
            )
        )
    audit(
        session,
        user,
        "REVIEW_SOURCE_IMPORTED",
        "review_collection",
        value.id,
        {"count": len(rows), "source_sha256": fingerprint},
        request_id,
    )
    session.commit()
    return next(row for row in collections(session, user) if int(row.id) == value.id)


def decision_view(session, value):
    result = s.DecisionView.model_validate(value)
    result.reviewer_login = session.get(User, value.reviewer_id).login
    result.review_method = "AI" if value.annotation.get("_ai") else "HUMAN"
    return result


def item_view(session, item, kind):
    decision = session.scalar(
        select(ReviewDecision)
        .where(
            ReviewDecision.item_id == item.id,
        )
        .order_by(ReviewDecision.id.desc())
        .limit(1)
    )
    return s.ItemView(
        id=item.id,
        collection_id=item.collection_id,
        kind=kind,
        item_key=item.item_key,
        payload=item.payload,
        content_hash=item.content_hash,
        version=item.version,
        status=item.status,
        latest_decision=decision_view(session, decision) if decision else None,
    )


def items(session, user, collection_id, status, page):
    require_role(user, "ADMIN")
    collection = get_collection(session, collection_id)
    where = [ReviewItem.collection_id == collection.id]
    if status:
        where.append(ReviewItem.status == status)
    records = session.scalars(
        select(ReviewItem).where(*where).order_by(ReviewItem.id).offset((page - 1) * 20).limit(20)
    )
    return s.ItemPage(
        items=[item_view(session, row, collection.kind) for row in records],
        total=session.scalar(select(func.count()).select_from(ReviewItem).where(*where)),
        page=page,
        page_size=20,
    )


def history(session, user, item_id):
    require_role(user, "ADMIN")
    if session.get(ReviewItem, item_id) is None:
        raise AppError(404, "REVIEW_ITEM_NOT_FOUND", "样本不存在")
    return [
        decision_view(session, row)
        for row in session.scalars(
            select(ReviewDecision)
            .where(
                ReviewDecision.item_id == item_id,
            )
            .order_by(ReviewDecision.id.desc())
        )
    ]


def decide(session, user, item_id, body, request_id):
    require_role(user, "ADMIN")
    lock_dispatch(session)
    item = session.scalar(
        select(ReviewItem)
        .where(ReviewItem.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise AppError(404, "REVIEW_ITEM_NOT_FOUND", "样本不存在")
    check_version(item, body.expected_version)
    collection = get_collection(session, item.collection_id)
    annotation = {}
    if body.decision == "APPROVED":
        try:
            annotation = ANNOTATIONS[collection.kind].model_validate(body.annotation).model_dump()
        except ValidationError as exc:
            raise AppError(422, "INVALID_REVIEW_ANNOTATION", "请填写完整且有效的人工标注") from exc
        if collection.kind == "CLASSIFICATION" and item.payload["rights_status"] == "unknown":
            raise AppError(422, "SOURCE_RIGHTS_UNKNOWN", "来源权利未知，请核实后以新版本导入")
        if collection.kind == "RETRIEVAL":
            known = set(session.scalars(select(KnowledgeDocument.document_key)))
            if set(annotation["relevant_document_keys"]) - known:
                raise AppError(422, "UNKNOWN_REVIEW_DOCUMENT", "标注了不存在的知识文档标识")
        if collection.kind == "LLM" and item.payload.get("refused"):
            correct = annotation["refusal_correct"]
            expected = {"ACCEPTABLE": True, "INCORRECT": False, "UNDETERMINED": None}
            if correct is not expected[annotation["verdict"]]:
                raise AppError(422, "INVALID_REFUSAL_REVIEW", "回复判定与拒答是否正确的结论不一致")
    value = ReviewDecision(
        item_id=item.id,
        item_version=item.version,
        content_hash=item.content_hash,
        reviewer_id=user.id,
        decision=body.decision,
        annotation=annotation,
        reason=body.reason,
    )
    session.add(value)
    item.status = body.decision
    # Even repeated confirmation must increment the version and append a distinct decision.
    item.version += 1
    audit(
        session,
        user,
        "REVIEW_DECISION",
        "review_item",
        item.id,
        {
            "decision": body.decision,
            "reviewed_version": body.expected_version,
            "content_hash": item.content_hash,
        },
        request_id,
    )
    session.commit()
    return item_view(session, item, collection.kind)


def snapshot_view(value):
    return s.SnapshotView(
        id=value.id,
        collection_id=value.collection_id,
        created_by=value.created_by,
        source_sha256=value.source_sha256,
        snapshot_sha256=value.snapshot_sha256,
        created_at=value.created_at,
        row_count=len(value.rows),
        reason=value.reason,
    )


def freeze(session, user, collection_id, body, request_id):
    require_role(user, "ADMIN")
    lock_dispatch(session)
    collection = get_collection(session, collection_id)
    records, ids = [], []
    for item in session.scalars(
        select(ReviewItem)
        .where(
            ReviewItem.collection_id == collection.id,
            ReviewItem.status == "APPROVED",
        )
        .order_by(ReviewItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ):
        decision = session.scalar(
            select(ReviewDecision)
            .where(
                ReviewDecision.item_id == item.id,
                ReviewDecision.item_version == item.version - 1,
            )
            .order_by(ReviewDecision.id.desc())
            .limit(1)
        )
        if (
            not decision
            or decision.decision != "APPROVED"
            or decision.content_hash != item.content_hash
        ):
            raise AppError(409, "REVIEW_EVIDENCE_MISMATCH", "复核记录与样本不一致")
        reviewer = session.get(User, decision.reviewer_id)
        if collection.kind == "CLASSIFICATION":
            data = dict(item.payload)
            edits = dict(decision.annotation)
            ai = edits.pop("_ai", None)
            corrected = edits.pop("corrected_text", None)
            data.update(edits)
            if corrected:
                data["text"] = corrected
            identity = (
                f"AI:{ai['model']}/{ai.get('evidence_ref', ai.get('invocation_id'))}"
                if ai
                else f"user:{reviewer.id}/{reviewer.login}"
            )
            data.update(review_status="approved", reviewer=identity)
            records.append(TicketSample.model_validate(data).model_dump())
        else:
            records.append(
                {
                    "id": item.item_key,
                    "original": item.payload,
                    "annotation": decision.annotation,
                    "reviewer": reviewer.login,
                    "reviewer_id": str(reviewer.id),
                    "decision_id": str(decision.id),
                }
            )
        ids.append(decision.id)
    if not records:
        raise AppError(409, "NO_REVIEWED_SAMPLES", "没有通过复核的样本，不能冻结空数据集")
    digest = canonical_hash(
        {"rows": records, "decision_ids": ids, "source_sha256": collection.source_sha256}
    )
    old = session.scalar(select(ReviewSnapshot).where(ReviewSnapshot.snapshot_sha256 == digest))
    if old:
        return snapshot_view(old)
    value = ReviewSnapshot(
        collection_id=collection.id,
        created_by=user.id,
        source_sha256=collection.source_sha256,
        snapshot_sha256=digest,
        rows=records,
        decision_ids=ids,
        reason=body.reason,
    )
    session.add(value)
    session.flush()
    audit(
        session,
        user,
        "REVIEW_DATASET_FROZEN",
        "review_snapshot",
        value.id,
        {"rows": len(records), "sha256": digest},
        request_id,
    )
    session.commit()
    return snapshot_view(value)


def snapshot_current(session, snapshot):
    decisions = list(
        session.scalars(
            select(ReviewDecision).where(
                ReviewDecision.id.in_(snapshot.decision_ids),
            )
        )
    )
    if len(decisions) != len(snapshot.decision_ids):
        return False
    # A locking read sees the latest committed rows even if authentication established
    # an older InnoDB REPEATABLE READ snapshot before the dispatch mutex was acquired.
    current = {
        row.id: row
        for row in session.scalars(
            select(ReviewItem)
            .where(ReviewItem.id.in_([row.item_id for row in decisions]))
            .order_by(ReviewItem.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    return all(
        row.item_id in current
        and current[row.item_id].status == "APPROVED"
        and current[row.item_id].version == row.item_version + 1
        and current[row.item_id].content_hash == row.content_hash
        for row in decisions
    )


def download(session, user, snapshot_id):
    require_role(user, "ADMIN")
    snapshot = session.get(ReviewSnapshot, snapshot_id)
    if snapshot is None:
        raise AppError(404, "REVIEW_SNAPSHOT_NOT_FOUND", "冻结版本不存在")
    return (
        "\n".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in snapshot.rows
        )
        + "\n"
    )


def artifact_path(settings, ref, prefix="runs"):
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", ref) or ".." in PurePosixPath(ref).parts:
        raise AppError(422, "INVALID_ARTIFACT_PATH", "产物引用必须是允许目录内的相对路径")
    root = settings.artifact_root.resolve()
    path = (root / ref).resolve()
    if not path.is_relative_to(root / prefix) or not path.is_file():
        raise AppError(422, "INVALID_ARTIFACT_PATH", "产物不在允许的目录或文件不存在")
    return path


def artifact_files(settings, ref):
    manifest = artifact_path(settings, ref)
    if manifest.name != "manifest.json":
        raise AppError(422, "INVALID_MODEL_MANIFEST", "请选择训练脚本生成的manifest.json")
    files = {
        "manifest": manifest,
        "metrics": manifest.parent / "metrics.json",
        "split": manifest.parent.parent / "split-manifest.json",
        "model": manifest.parent / "model.joblib",
    }
    root = (settings.artifact_root / "runs").resolve()
    if any(
        not path.resolve().is_relative_to(root) or not path.is_file() for path in files.values()
    ):
        raise AppError(422, "MODEL_FILES_MISSING", "仅支持完整、受信任的RF/LR训练目录")
    if any(
        path.stat().st_size > (256 if key == "model" else 20) * 1024 * 1024
        for key, path in files.items()
    ):
        raise AppError(422, "MODEL_TOO_LARGE", "模型产物超出本机发布限制")
    return files


def gates(session, settings, release):
    snapshot = session.get(ReviewSnapshot, release.snapshot_id)
    files = artifact_files(settings, release.manifest_ref)
    fingerprint = {key: file_hash(path) for key, path in files.items()}
    meta, metrics, split = (
        json.loads(files[key].read_text(encoding="utf-8"))
        for key in ("manifest", "metrics", "split")
    )
    if not all(isinstance(value, dict) for value in (meta, metrics, split)):
        raise AppError(409, "INVALID_MODEL_METADATA", "训练清单与报告必须是JSON对象")
    result = []

    def check(code, passed, message):
        result.append({"code": code, "passed": bool(passed), "message": message})

    check("artifact_unchanged", fingerprint == release.fingerprints, "训练产物与注册时指纹一致")
    check(
        "formal_training",
        meta.get("purpose") == metrics.get("purpose") == split.get("purpose") == "formal",
        "必须使用正式复核数据训练；smoke不能发布",
    )
    check("review_current", snapshot_current(session, snapshot), "冻结样本的复核版本仍有效")
    check("label_contract", meta.get("label_map_hash") == label_map_hash(), "六类标签映射一致")
    check(
        "model_hash",
        meta.get("model_file") == "model.joblib"
        and meta.get("model_sha256") == fingerprint["model"],
        "模型内容哈希与清单一致",
    )
    check(
        "supported_runtime",
        meta.get("name") in {"tfidf_rf", "tfidf_logreg"},
        "在线发布当前支持RF/LR",
    )
    try:
        samples = [TicketSample.model_validate(row) for row in snapshot.rows]
        verify_manifest(samples, split)
        data_bound = meta.get("dataset_hash") == canonical_hash(snapshot.rows)
        data_bound = data_bound and meta.get("split_manifest_hash") == canonical_hash(split)
    except (ValueError, KeyError, TypeError, AttributeError, ValidationError):
        data_bound = False
    check("data_binding", data_bound, "模型绑定此冻结数据集与固定切分，不能套用其他数据的报告")
    packages = meta.get("packages", {})
    if not isinstance(packages, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in packages.items()
    ):
        raise AppError(409, "INVALID_MODEL_METADATA", "依赖版本清单格式错误")
    try:
        compatible = all(
            importlib.metadata.version(name) == version for name, version in packages.items()
        )
    except importlib.metadata.PackageNotFoundError:
        compatible = False
    check(
        "runtime_versions",
        {"numpy", "scikit-learn", "scipy", "joblib"} <= set(packages) and compatible,
        "推理与训练依赖版本一致",
    )
    check(
        "minimum_samples",
        len(snapshot.rows) >= (1200 if release.scope == "LIVE" else 18),
        "正式自动分流至少1200条；影子验证至少18条且不能自动派单",
    )
    if release.scope == "LIVE":
        check(
            "human_gold_standard",
            all(
                isinstance(row.get("reviewer"), str) and row["reviewer"].startswith("user:")
                for row in snapshot.rows
            ),
            "自动业务分流还需人工金标准；AI复核数据只用于本项目离线与影子验证",
        )
        test = metrics.get("test", {})
        selected = metrics.get("test_selective", {})
        policy = meta.get("threshold_policy", {})
        if not all(isinstance(value, dict) for value in (test, selected, policy)):
            raise AppError(409, "INVALID_MODEL_METADATA", "测试指标与阈值策略格式错误")

        def at_least(value, minimum):
            return type(value) in (int, float) and minimum <= value < float("inf")

        check("heldout_size", at_least(test.get("sample_count"), 120), "独立测试至少120条")
        check("macro_f1", at_least(test.get("macro_f1"), 0.8), "测试Macro-F1至少0.80")
        check(
            "selective_quality",
            policy.get("enabled") is True
            and at_least(selected.get("accepted_accuracy"), 0.9)
            and at_least(selected.get("coverage"), 0.2),
            "阈值仅用验证集选择，测试接收准确率至少0.90且覆盖至少20%",
        )
    return result


def release_view(session, settings, value):
    try:
        checks = gates(session, settings, value)
    except (AppError, OSError, ValueError, KeyError, TypeError, AttributeError):
        checks = [
            {
                "code": "artifact_unavailable",
                "passed": False,
                "message": "产物缺失或已损坏，禁止发布",
            }
        ]
    return s.ReleaseView(
        id=value.id,
        snapshot_id=value.snapshot_id,
        manifest_ref=value.manifest_ref,
        scope=value.scope,
        status=value.status,
        version=value.version,
        gates=checks,
        package_ref=value.package_ref,
        reviewed_by=value.reviewed_by,
        review_reason=value.review_reason,
        created_at=value.created_at,
    )


def create_release(session, user, body, settings, request_id):
    require_role(user, "ADMIN")
    lock_dispatch(session)
    snapshot = session.get(ReviewSnapshot, body.snapshot_id)
    if snapshot is None or get_collection(session, snapshot.collection_id).kind != "CLASSIFICATION":
        raise AppError(422, "CLASSIFICATION_SNAPSHOT_REQUIRED", "模型发布需要分类冻结数据集")
    files = artifact_files(settings, body.manifest_ref)
    value = ModelRelease(
        snapshot_id=snapshot.id,
        created_by=user.id,
        manifest_ref=body.manifest_ref,
        scope=body.scope,
        fingerprints={k: file_hash(p) for k, p in files.items()},
    )
    session.add(value)
    session.flush()
    audit(
        session,
        user,
        "MODEL_CANDIDATE_REGISTERED",
        "model_release",
        value.id,
        {"snapshot_id": snapshot.id, "fingerprints": value.fingerprints},
        request_id,
    )
    session.commit()
    return release_view(session, settings, value)


def review_release(session, user, release_id, body, settings, request_id):
    require_role(user, "ADMIN")
    lock_dispatch(session)
    value = session.scalar(
        select(ModelRelease)
        .where(ModelRelease.id == release_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if value is None:
        raise AppError(404, "MODEL_RELEASE_NOT_FOUND", "候选模型不存在")
    check_version(value, body.expected_version)
    if body.action == "REVOKE":
        if value.status != "APPROVED":
            raise AppError(409, "INVALID_RELEASE_STATE", "只有已批准版本能撤销")
        value.status = "REVOKED"
    else:
        if value.status != "CANDIDATE":
            raise AppError(409, "INVALID_RELEASE_STATE", "该候选已经完成审批")
        if body.action == "APPROVE":
            checks = release_view(session, settings, value).gates
            if not all(check["passed"] for check in checks):
                raise AppError(409, "MODEL_RELEASE_BLOCKED", "发布门槛未满足，请查看各项检查")
            files = artifact_files(settings, value.manifest_ref)
            target = (
                settings.artifact_root / "governance/releases" / f"release-{value.id}-{uuid4().hex}"
            )
            if not target.resolve().is_relative_to(
                settings.artifact_root.resolve() / "governance/releases"
            ):
                raise AppError(422, "INVALID_ARTIFACT_PATH", "发布目录不能通过链接跳出产物根目录")
            target.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(files["model"], target / "model.joblib")
            if file_hash(target / "model.joblib") != value.fingerprints["model"]:
                raise AppError(409, "MODEL_CHANGED", "复制模型时内容发生变化，禁止发布")
            if {key: file_hash(path) for key, path in files.items()} != value.fingerprints:
                raise AppError(409, "MODEL_CHANGED", "审批期间源产物发生变化，禁止发布")
            meta = json.loads(files["manifest"].read_text(encoding="utf-8"))
            meta.update(
                deployment_approved=True,
                deployment_scope=value.scope,
                approval_record_id=value.id,
                approval={
                    "reviewer_id": user.id,
                    "reason": body.reason,
                    "at": utcnow().isoformat(),
                    "snapshot_id": value.snapshot_id,
                },
            )
            write_json(target / "manifest.json", meta)
            value.package_ref = (
                (target / "manifest.json").relative_to(settings.artifact_root).as_posix()
            )
            value.package_sha256 = file_hash(target / "manifest.json")
            value.status = "APPROVED"
        else:
            value.status = "REJECTED"
    value.reviewed_by, value.review_reason = user.id, body.reason
    audit(
        session,
        user,
        "MODEL_RELEASE_" + value.status,
        "model_release",
        value.id,
        {"scope": value.scope, "snapshot_id": value.snapshot_id},
        request_id,
    )
    session.commit()
    return release_view(session, settings, value)


def verify_managed_release(session, settings, manifest_path):
    """Validate the database ledger BEFORE deserializing a managed pickle bundle."""
    meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise AppError(409, "MODEL_APPROVAL_INVALID", "模型清单格式错误")
    release_id = meta.get("approval_record_id")
    if release_id is None:
        if manifest_path.resolve().is_relative_to(
            settings.artifact_root.resolve() / "governance/releases"
        ):
            raise AppError(409, "MODEL_APPROVAL_INVALID", "受管发布包缺少审批记录")
        return None  # Legacy operator-owned manifests retain the existing trust boundary.
    release = session.scalar(
        select(ModelRelease)
        .where(ModelRelease.id == release_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        release is None
        or release.status != "APPROVED"
        or not release.package_ref
        or artifact_path(settings, release.package_ref, "governance/releases")
        != manifest_path.resolve()
        or file_hash(manifest_path) != release.package_sha256
        or not snapshot_current(session, session.get(ReviewSnapshot, release.snapshot_id))
    ):
        raise AppError(409, "MODEL_APPROVAL_INVALID", "模型审批已失效，转人工处理")
    return release.id


def deployment_status(session, settings):
    if not settings.classifier_manifest:
        return "not_enabled"
    try:
        release_id = verify_managed_release(session, settings, settings.classifier_manifest)
        if release_id is None:
            return "configured_requires_validation"
        release = session.get(ModelRelease, release_id)
        model = settings.classifier_manifest.parent / "model.joblib"
        if file_hash(model) != release.fingerprints["model"]:
            return "invalid"
        return "shadow" if release.scope == "SHADOW" else "live"
    except (AppError, OSError, ValueError, TypeError, KeyError):
        return "invalid"
