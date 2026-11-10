import json

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from backend.app.core.errors import AppError
from backend.app.db.models import AIInvocation, User
from backend.app.services import data_review

CASES = [{"id": "case-1", "english": "My payment failed", "chinese": "我的付款失败了"}]


def response(labels=None, finish="stop"):
    return httpx.Response(
        200,
        json={
            "id": "mock-review-1",
            "model": "qwen-plus",
            "choices": [
                {
                    "finish_reason": finish,
                    "message": {
                        "content": json.dumps(
                            {"labels": [1] if labels is None else labels, "translation_ok": [True]}
                        )
                    },
                }
            ],
            "usage": {"total_tokens": 12},
        },
    )


@pytest.mark.anyio
async def test_review_blind_idempotent_and_shared_budget(api):
    settings = api.settings.model_copy(
        update={"llm_enabled": True, "llm_api_key": SecretStr("mock-only"), "llm_call_budget": 1}
    )
    requests = []

    def serve(request):
        payload = json.loads(request.content)
        assert "proposed_label" not in payload["messages"][1]["content"]
        requests.append(payload)
        return response()

    with api.app.state.database.sessions() as session:
        admin = session.get(User, api.users["admin"])
        first = data_review.execute(
            session,
            admin,
            settings,
            kind="CLASSIFICATION",
            cases=CASES,
            transport=httpx.MockTransport(serve),
        )
        cached = data_review.execute(
            session,
            admin,
            settings,
            kind="CLASSIFICATION",
            cases=CASES,
            transport=httpx.MockTransport(serve),
        )
        assert first.id == cached.id and len(requests) == 1
        assert first.ticket_id is None and first.review_result["human_review"] is False
        assert first.review_result["decisions"] == [
            {"id": "case-1", "label": 1, "translation_ok": True}
        ]
        with pytest.raises(AppError) as error:
            data_review.execute(
                session,
                admin,
                settings,
                kind="CLASSIFICATION",
                cases=[{**CASES[0], "id": "case-2"}],
                transport=httpx.MockTransport(serve),
            )
        assert error.value.code == "LLM_BUDGET_EXHAUSTED"
        assert session.scalar(select(func.count()).select_from(AIInvocation)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "labels,finish", [([], "stop"), ([True], "stop"), ([6], "stop"), ([1], "length")]
)
async def test_failed_review_stays_billed_without_retry(api, labels, finish):
    settings = api.settings.model_copy(
        update={"llm_enabled": True, "llm_api_key": SecretStr("mock-only"), "llm_call_budget": 5}
    )
    calls = []

    def serve(request):
        calls.append(1)
        return response(labels, finish)

    with api.app.state.database.sessions() as session:
        admin = session.get(User, api.users["admin"])
        for code in ("AI_REVIEW_FAILED", "REVIEW_ALREADY_RESERVED"):
            with pytest.raises(AppError) as error:
                data_review.execute(
                    session,
                    admin,
                    settings,
                    kind="CLASSIFICATION",
                    cases=CASES,
                    transport=httpx.MockTransport(serve),
                )
            assert error.value.code == code
        assert len(calls) == 1
        assert session.scalar(select(AIInvocation)).status == "FAILED"


@pytest.mark.anyio
async def test_review_access_and_label_blinding_before_reservation(api):
    with api.app.state.database.sessions() as session:
        for login, cases, code in [
            ("agent", CASES, "FORBIDDEN"),
            ("admin", [{**CASES[0], "label": 1}], "REVIEW_MUST_BE_BLIND"),
        ]:
            with pytest.raises(AppError) as error:
                data_review.execute(
                    session,
                    session.get(User, api.users[login]),
                    api.settings,
                    kind="CLASSIFICATION",
                    cases=cases,
                )
            assert error.value.code == code
        assert session.scalar(select(func.count()).select_from(AIInvocation)) == 0


def test_business_schema_and_complete_ids():
    cases = [{"id": "r-1", "kind": "RETRIEVAL"}]
    with pytest.raises(ValueError):
        data_review._validate(
            "BUSINESS",
            cases,
            {
                "decisions": [
                    {
                        "id": "r-1",
                        "annotation": {"answerable": True, "relevant_document_keys": []},
                        "reason": "缺少依据不能批准",
                    }
                ]
            },
        )


@pytest.mark.anyio
async def test_large_paid_batches_rejected_before_reservation(api):
    with api.app.state.database.sessions() as session:
        with pytest.raises(AppError) as error:
            data_review.execute(
                session,
                session.get(User, api.users["admin"]),
                api.settings,
                kind="CLASSIFICATION",
                cases=[{**CASES[0], "id": str(i)} for i in range(51)],
            )
        assert error.value.code == "INVALID_REVIEW_BATCH"
        assert session.scalar(select(func.count()).select_from(AIInvocation)) == 0


@pytest.mark.anyio
async def test_chinese_review_keeps_scope_distinct_from_translation_certification(api, tmp_path):
    from backend.app.db.models import ReviewDecision, ReviewItem
    from backend.app.services.ai_review_import import apply_offline
    from tests.backend.test_reviews import import_classification

    collection, _ = await import_classification(api)
    api.settings.artifact_root = tmp_path / "artifacts"
    directory = api.settings.artifact_root / "data-review-v3"
    directory.mkdir(parents=True)
    with api.app.state.database.sessions() as session:
        records = list(
            session.scalars(
                select(ReviewItem).where(ReviewItem.collection_id == int(collection["id"]))
            )
        )
        cases = [
            {
                "id": str(item.id),
                "review_item_id": item.id,
                "content_hash": item.content_hash,
                "chinese": item.payload["text"],
                "english": None,
            }
            for item in records
        ]
        report = {
            "method": "AI_LOCAL_CLASSIFICATION_REVIEW",
            "reviewer_model": "SIMULATED_UNIT_FIXTURE",
            "review": {
                "kind": "CLASSIFICATION",
                "assessment_scope": "CHINESE_INTENT_AND_NATURALNESS",
                "protocol": "fictional-ai-review-v1",
                "human_review": False,
                "input": cases,
                "decisions": [
                    {"id": str(item.id), "label": item.payload["label"], "natural": True}
                    for item in records
                ],
            },
        }
        path = directory / "simulated.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        result = apply_offline(session, session.get(User, api.users["admin"]), api.settings, path)
        assert result["applied"] == len(records)
        decision = session.scalar(select(ReviewDecision))
        assert decision.annotation["_ai"]["assessment_scope"] == "CHINESE_INTENT_AND_NATURALNESS"
        assert "translation_ok" not in decision.annotation["_ai"]["raw_decision"]


@pytest.mark.anyio
async def test_ai_import_attribution_freeze_and_no_duplicate_decisions(api):
    from backend.app.db.models import ReviewDecision, ReviewItem, ReviewSnapshot
    from backend.app.services.ai_review_import import apply_results
    from tests.backend.test_reviews import import_classification

    collection, _ = await import_classification(api)
    settings = api.settings.model_copy(
        update={"llm_enabled": True, "llm_api_key": SecretStr("mock-only"), "llm_call_budget": 1}
    )
    with api.app.state.database.sessions() as session:
        actor = session.get(User, api.users["admin"])
        items = list(
            session.scalars(
                select(ReviewItem)
                .where(ReviewItem.collection_id == int(collection["id"]))
                .order_by(ReviewItem.id)
            )
        )
        cases = [
            {
                "id": str(item.id),
                "review_item_id": item.id,
                "content_hash": item.content_hash,
                "chinese": item.payload["text"],
                "english": item.payload["text"],
            }
            for item in items
        ]
        labels = [item.payload["label"] for item in items]

        def serve(request):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json.dumps(
                                    {"labels": labels, "translation_ok": [True] * len(items)}
                                )
                            },
                        }
                    ]
                },
            )

        invocation = data_review.execute(
            session,
            actor,
            settings,
            kind="CLASSIFICATION",
            cases=cases,
            transport=httpx.MockTransport(serve),
        )
        assert apply_results(session, actor, invocation.id)["applied"] == len(items)
        assert apply_results(session, actor, invocation.id)["applied"] == 0
        assert session.scalar(select(func.count()).select_from(ReviewDecision)) == len(items)
        assert session.scalar(select(User).where(User.login == "system-ai-review")).enabled is False
    page = await api.client.get(
        "/api/v1/reviews/items",
        params={"collection_id": collection["id"]},
        headers=api.auth("admin"),
    )
    assert all(item["latest_decision"]["review_method"] == "AI" for item in page.json()["items"])
    frozen = await api.client.post(
        f"/api/v1/reviews/collections/{collection['id']}/freeze",
        headers=api.auth("admin"),
        json={"reason": "明确AI复核，非人工认证", "acknowledged": True},
    )
    assert frozen.status_code == 201, frozen.text
    with api.app.state.database.sessions() as session:
        snapshot = session.get(ReviewSnapshot, int(frozen.json()["id"]))
        assert all(row["reviewer"].startswith("AI:") for row in snapshot.rows)
        assert all("_ai" not in row for row in snapshot.rows)
    with pytest.raises(ValueError):
        data_review._validate(
            "BUSINESS",
            cases,
            {"decisions": [{"id": "unknown", "annotation": {}, "reason": "不能评审未知样本"}]},
        )
