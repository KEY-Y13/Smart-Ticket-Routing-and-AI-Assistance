import json
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.core.errors import AppError
from backend.app.db.models import ReviewDecision, ReviewSnapshot
from backend.app.services import jobs, reviews
from nlp.artifacts import write_json
from nlp.baselines.train import train_baselines
from nlp.data.dataset import prepare_manifest
from nlp.inference.classifier import ArtifactClassifier

SOURCE = Path(__file__).resolve().parents[2] / "data/samples/classification-smoke.jsonl"


def candidates(per_class=1):
    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines()]
    return [
        row for label in range(6) for row in [r for r in rows if r["label"] == label][:per_class]
    ]


async def import_classification(api, rows=None):
    body = {
        "code": "explicit-isolated-test",
        "title": "隔离自动化测试，不是真实人工复核",
        "kind": "CLASSIFICATION",
        "provenance": "EXPLICIT_SIMULATED_REVIEW_FIXTURE",
        "items": rows or candidates(),
    }
    response = await api.client.post(
        "/api/v1/reviews/collections", json=body, headers=api.auth("admin")
    )
    assert response.status_code == 201, response.text
    return response.json(), body


async def list_items(api, collection):
    response = await api.client.get(
        "/api/v1/reviews/items",
        params={"collection_id": collection["id"]},
        headers=api.auth("admin"),
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def decision(item, label=None):
    return {
        "expected_version": item["version"],
        "decision": "APPROVED",
        "annotation": {
            "label": item["payload"]["label"] if label is None else label,
            "group_id": item["payload"]["group_id"],
            "rights_status": "project_internal",
        },
        "reason": "隔离自动化测试中的模拟复核，不是实际人工评审",
        "personally_reviewed": True,
    }


@pytest.mark.anyio
async def test_review_import_is_admin_only_pending_and_idempotent(api):
    collection, body = await import_classification(api)
    assert collection["counts"] == {"PENDING": 6, "APPROVED": 0, "REJECTED": 0}
    for role in ("customer", "agent"):
        assert (
            await api.client.get("/api/v1/reviews/collections", headers=api.auth(role))
        ).status_code == 403
        assert (
            await api.client.post("/api/v1/reviews/collections", json=body, headers=api.auth(role))
        ).status_code == 403
    replay = await api.client.post(
        "/api/v1/reviews/collections", json=body, headers=api.auth("admin")
    )
    assert replay.json()["id"] == collection["id"]
    body["items"][0]["text"] += "更改来源内容"
    changed = await api.client.post(
        "/api/v1/reviews/collections", json=body, headers=api.auth("admin")
    )
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "REVIEW_SOURCE_CHANGED"


@pytest.mark.anyio
async def test_review_confirmation_identity_conflict_and_immutable_snapshot(api):
    collection, _ = await import_classification(api)
    freeze = {"reason": "明确的隔离测试冻结", "acknowledged": True}
    assert (
        await api.client.post(
            f"/api/v1/reviews/collections/{collection['id']}/freeze",
            json=freeze,
            headers=api.auth("admin"),
        )
    ).status_code == 409
    item = (await list_items(api, collection))[0]
    body = decision(item)
    missing = dict(body)
    missing.pop("personally_reviewed")
    assert (
        await api.client.post(
            f"/api/v1/reviews/items/{item['id']}/decisions", json=missing, headers=api.auth("admin")
        )
    ).status_code == 422
    spoofed = {**body, "reviewer_id": api.users["customer"]}
    assert (
        await api.client.post(
            f"/api/v1/reviews/items/{item['id']}/decisions", json=spoofed, headers=api.auth("admin")
        )
    ).status_code == 422
    saved = await api.client.post(
        f"/api/v1/reviews/items/{item['id']}/decisions", json=body, headers=api.auth("admin")
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["payload"]["review_status"] == "unreviewed"
    assert saved.json()["latest_decision"]["reviewer_id"] == str(api.users["admin"])
    assert (
        await api.client.post(
            f"/api/v1/reviews/items/{item['id']}/decisions", json=body, headers=api.auth("admin")
        )
    ).status_code == 409
    frozen = await api.client.post(
        f"/api/v1/reviews/collections/{collection['id']}/freeze",
        json=freeze,
        headers=api.auth("admin"),
    )
    assert frozen.status_code == 201, frozen.text
    snap = frozen.json()
    assert snap["row_count"] == 1
    original = await api.client.get(
        f"/api/v1/reviews/snapshots/{snap['id']}/download", headers=api.auth("admin")
    )
    exported = json.loads(original.text)
    assert (
        exported["review_status"] == "approved" and str(api.users["admin"]) in exported["reviewer"]
    )
    update = decision(saved.json(), label=1)
    assert (
        await api.client.post(
            f"/api/v1/reviews/items/{item['id']}/decisions", json=update, headers=api.auth("admin")
        )
    ).status_code == 200

    assert (
        await api.client.get(
            f"/api/v1/reviews/snapshots/{snap['id']}/download", headers=api.auth("admin")
        )
    ).text == original.text
    with api.app.state.database.sessions() as session:
        assert not reviews.snapshot_current(session, session.get(ReviewSnapshot, int(snap["id"])))
        assert len(list(session.scalars(select(ReviewDecision)))) == 2


@pytest.mark.anyio
async def test_unknown_rights_cannot_be_laundered_by_annotation(api):
    rows = candidates()
    rows[0]["rights_status"] = "unknown"
    collection, _ = await import_classification(api, rows)
    item = (await list_items(api, collection))[0]
    response = await api.client.post(
        f"/api/v1/reviews/items/{item['id']}/decisions",
        json=decision(item),
        headers=api.auth("admin"),
    )
    assert (
        response.status_code == 422 and response.json()["error"]["code"] == "SOURCE_RIGHTS_UNKNOWN"
    )


@pytest.mark.anyio
async def test_retrieval_annotation_requires_consistent_known_document_keys(api):
    body = {
        "code": "retrieval-test",
        "title": "检索隔离测试",
        "kind": "RETRIEVAL",
        "provenance": "EXPLICIT_SIMULATED_RETRIEVAL_FIXTURE",
        "items": [{"id": "q-1", "query": "没有业务依据的问题应该拒答"}],
    }
    result = await api.client.post(
        "/api/v1/reviews/collections", json=body, headers=api.auth("admin")
    )
    item = (await list_items(api, result.json()))[0]
    base = {
        "expected_version": 1,
        "decision": "APPROVED",
        "personally_reviewed": True,
        "reason": "明确的自动化拒答样本标注",
    }
    for annotation in (
        {"answerable": False, "relevant_document_keys": ["missing"]},
        {"answerable": True, "relevant_document_keys": ["missing"]},
    ):
        response = await api.client.post(
            f"/api/v1/reviews/items/{item['id']}/decisions",
            json={**base, "annotation": annotation},
            headers=api.auth("admin"),
        )
        assert response.status_code == 422
    base["annotation"] = {"answerable": False, "relevant_document_keys": []}
    assert (
        await api.client.post(
            f"/api/v1/reviews/items/{item['id']}/decisions", json=base, headers=api.auth("admin")
        )
    ).status_code == 200

    frozen = await api.client.post(
        f"/api/v1/reviews/collections/{result.json()['id']}/freeze",
        json={"reason": "隔离检索真值评测冻结", "acknowledged": True},
        headers=api.auth("admin"),
    )
    from rag.evaluate import reviewed_cases

    with api.app.state.database.sessions() as session:
        cases, binding = reviewed_cases(session, int(frozen.json()["id"]))
        assert cases == [
            {
                "id": "q-1",
                "query": body["items"][0]["query"],
                "answerable": False,
                "relevant_document_keys": [],
            }
        ]
        assert binding["snapshot_sha256"] == frozen.json()["snapshot_sha256"]


@pytest.mark.anyio
async def test_real_formal_training_shadow_package_and_revocation(api, tmp_path):
    api.settings.artifact_root = tmp_path / "artifacts"
    collection, _ = await import_classification(api, candidates(3))
    for item in await list_items(api, collection):
        response = await api.client.post(
            f"/api/v1/reviews/items/{item['id']}/decisions",
            json=decision(item),
            headers=api.auth("admin"),
        )
        assert response.status_code == 200, response.text
    frozen = await api.client.post(
        f"/api/v1/reviews/collections/{collection['id']}/freeze",
        json={"reason": "模拟复核的隔离训练数据，禁止当作生产审批", "acknowledged": True},
        headers=api.auth("admin"),
    )
    assert frozen.status_code == 201, frozen.text
    snapshot = frozen.json()
    response = await api.client.get(
        f"/api/v1/reviews/snapshots/{snapshot['id']}/download", headers=api.auth("admin")
    )
    data = tmp_path / "reviewed-fixture.jsonl"
    data.write_text(response.text, encoding="utf-8")
    manifest = tmp_path / "split.json"
    write_json(manifest, prepare_manifest(data, "formal"))
    out = api.settings.artifact_root / "runs/fixture/baselines"
    train_baselines(data, manifest, out)
    ref = "runs/fixture/baselines/tfidf_logreg/manifest.json"
    create = {"snapshot_id": snapshot["id"], "manifest_ref": ref, "scope": "SHADOW"}
    for bad in ("../.env", "/etc/passwd", "runs/../../.env"):
        response = await api.client.post(
            "/api/v1/reviews/releases",
            json={**create, "manifest_ref": bad},
            headers=api.auth("admin"),
        )
        assert response.status_code == 422
    live = await api.client.post(
        "/api/v1/reviews/releases", json={**create, "scope": "LIVE"}, headers=api.auth("admin")
    )
    assert live.status_code == 201, live.text
    assert not next(g["passed"] for g in live.json()["gates"] if g["code"] == "minimum_samples")
    review = {
        "expected_version": 1,
        "action": "APPROVE",
        "reason": "明确的隔离测试审批，不是生产上线",
        "personally_reviewed": True,
    }
    assert (
        await api.client.post(
            f"/api/v1/reviews/releases/{live.json()['id']}/decisions",
            json=review,
            headers=api.auth("admin"),
        )
    ).status_code == 409
    candidate = await api.client.post(
        "/api/v1/reviews/releases", json=create, headers=api.auth("admin")
    )
    assert candidate.status_code == 201, candidate.text
    assert all(g["passed"] for g in candidate.json()["gates"]), candidate.text
    metrics_path = out / "tfidf_logreg/metrics.json"
    metrics_bytes = metrics_path.read_bytes()
    metrics_path.write_text("[]", encoding="utf-8")
    broken = await api.client.get("/api/v1/reviews/releases", headers=api.auth("admin"))
    assert broken.status_code == 200
    assert not all(g["passed"] for g in broken.json()[0]["gates"])
    assert (
        await api.client.post(
            f"/api/v1/reviews/releases/{candidate.json()['id']}/decisions",
            json=review,
            headers=api.auth("admin"),
        )
    ).status_code == 409
    metrics_path.write_bytes(metrics_bytes)
    approved = await api.client.post(
        f"/api/v1/reviews/releases/{candidate.json()['id']}/decisions",
        json=review,
        headers=api.auth("admin"),
    )
    assert approved.status_code == 200, approved.text
    release = approved.json()
    package = api.settings.artifact_root / release["package_ref"]
    # This is a temporary test bundle; no main-project data or model is approved.
    result = ArtifactClassifier(package).predict("我的耳机没有声音")
    assert (
        result["deployment_scope"] == "SHADOW"
        and result["needs_manual_review"]
        and result["label"] is None
    )
    original = json.loads((out / "tfidf_logreg/manifest.json").read_text())
    assert original["deployment_approved"] is False
    api.settings.classifier_manifest = package
    classified = await api.client.post(
        "/api/v1/nlp/classify", json={"text": "我的耳机没有声音"}, headers=api.auth("admin")
    )
    assert classified.status_code == 200 and classified.json()["label"] is None
    assert (await api.client.get("/api/v1/capabilities")).json()[
        "classifier_deployment"
    ] == "shadow"
    ticket = await api.create("shadow-model-test", order=None)
    assert jobs.process_one(api.app.state.database, api.settings)
    analysis = await api.client.get(
        f"/api/v1/tickets/{ticket['id']}/analysis", headers=api.auth("admin")
    )
    assert analysis.json()["result"]["deployment_scope"] == "SHADOW"
    assert analysis.json()["result"]["label"] is None
    review.update(expected_version=release["version"], action="REVOKE")
    revoked = await api.client.post(
        f"/api/v1/reviews/releases/{release['id']}/decisions",
        json=review,
        headers=api.auth("admin"),
    )
    assert revoked.status_code == 200, revoked.text
    assert (
        await api.client.post(
            "/api/v1/nlp/classify", json={"text": "我的耳机没有声音"}, headers=api.auth("admin")
        )
    ).status_code == 409
    assert (await api.client.get("/api/v1/capabilities")).json()[
        "classifier_deployment"
    ] == "invalid"
    with api.app.state.database.sessions() as session:
        with pytest.raises(AppError, match="模型审批已失效"):
            reviews.verify_managed_release(session, api.settings, package)
    api.settings.classifier_manifest = package
    ticket = await api.create("revoked-model-test", order=None)
    assert jobs.process_one(api.app.state.database, api.settings)
    analysis = await api.client.get(
        f"/api/v1/tickets/{ticket['id']}/analysis", headers=api.auth("admin")
    )
    assert analysis.json()["result"]["needs_manual_review"] is True


@pytest.mark.anyio
async def test_refused_case_requires_consistent_verdict(api):
    response = await api.client.post(
        "/api/v1/reviews/collections",
        headers=api.auth("admin"),
        json={
            "code": "refusal-test",
            "title": "拒答模拟数据",
            "kind": "LLM",
            "provenance": "EXPLICIT_ISOLATED_TEST_FIXTURE",
            "items": [{"id": "refused-1", "question": "没有可核对的政策依据", "refused": True}],
        },
    )
    item = (await list_items(api, response.json()))[0]
    body = {
        "expected_version": 1,
        "decision": "APPROVED",
        "personally_reviewed": True,
        "reason": "隔离测试模拟拒答正确性核对",
    }
    for verdict, correct in (("ACCEPTABLE", None), ("INCORRECT", True), ("UNDETERMINED", False)):
        saved = await api.client.post(
            f"/api/v1/reviews/items/{item['id']}/decisions",
            headers=api.auth("admin"),
            json={**body, "annotation": {"verdict": verdict, "refusal_correct": correct}},
        )
        assert saved.status_code == 422
    saved = await api.client.post(
        f"/api/v1/reviews/items/{item['id']}/decisions",
        headers=api.auth("admin"),
        json={**body, "annotation": {"verdict": "ACCEPTABLE", "refusal_correct": True}},
    )
    assert saved.status_code == 200
