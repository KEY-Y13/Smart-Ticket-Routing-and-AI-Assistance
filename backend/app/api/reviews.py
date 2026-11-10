from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from backend.app.api.dependencies import CurrentUser, SessionDep
from backend.app.core.errors import AppError
from backend.app.db.models import ModelRelease, ReviewSnapshot
from backend.app.schemas import reviews as s
from backend.app.schemas.business import ResourceId
from backend.app.services import reviews
from backend.app.services.tickets import require_role

router = APIRouter(prefix="/api/v1/reviews", tags=["人工复核与模型发布"])


@router.get("/collections")
def collections(user: CurrentUser, session: SessionDep) -> list[s.CollectionView]:
    return reviews.collections(session, user)


@router.post("/collections", status_code=201)
def create_collection(
    body: s.CollectionCreate, user: CurrentUser, session: SessionDep, request: Request
) -> s.CollectionView:
    return reviews.create_collection(session, user, body, request.state.request_id)


@router.get("/items")
def items(
    user: CurrentUser,
    session: SessionDep,
    collection_id: ResourceId,
    status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
) -> s.ItemPage:
    return reviews.items(session, user, collection_id, status, page)


@router.post("/items/{item_id}/decisions")
def decide(
    item_id: ResourceId,
    body: s.DecisionCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> s.ItemView:
    return reviews.decide(session, user, item_id, body, request.state.request_id)


@router.get("/items/{item_id}/history")
def history(item_id: ResourceId, user: CurrentUser, session: SessionDep) -> list[s.DecisionView]:
    return reviews.history(session, user, item_id)


@router.post("/collections/{collection_id}/freeze", status_code=201)
def freeze(
    collection_id: ResourceId,
    body: s.Freeze,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> s.SnapshotView:
    return reviews.freeze(session, user, collection_id, body, request.state.request_id)


@router.get("/snapshots")
def snapshots(user: CurrentUser, session: SessionDep) -> list[s.SnapshotView]:
    require_role(user, "ADMIN")
    return [
        reviews.snapshot_view(row)
        for row in session.scalars(
            select(ReviewSnapshot).order_by(ReviewSnapshot.id.desc()).limit(100)
        )
    ]


@router.get("/snapshots/{snapshot_id}/download")
def download(snapshot_id: ResourceId, user: CurrentUser, session: SessionDep) -> Response:
    return Response(
        content=reviews.download(session, user, snapshot_id),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="review-{snapshot_id}.jsonl"'},
    )


@router.get("/artifacts")
def artifacts(user: CurrentUser, request: Request) -> list[dict]:
    require_role(user, "ADMIN")
    settings = request.app.state.settings
    result = []
    for path in sorted(
        settings.artifact_root.glob("runs/*/baselines/*/manifest.json"), reverse=True
    )[:50]:
        try:
            import json

            meta = json.loads(path.read_text(encoding="utf-8"))
            ref = path.relative_to(settings.artifact_root).as_posix()
            reviews.artifact_files(settings, ref)
            result.append(
                {
                    "manifest_ref": ref,
                    "name": meta["name"],
                    "purpose": meta["purpose"],
                    "note": "smoke不能发布；正式模型还需绑定人审冻结版本",
                }
            )
        except (AppError, ValueError, OSError, KeyError, TypeError):
            continue
    return result


@router.get("/releases")
def releases(user: CurrentUser, session: SessionDep, request: Request) -> list[s.ReleaseView]:
    require_role(user, "ADMIN")
    return [
        reviews.release_view(session, request.app.state.settings, row)
        for row in session.scalars(select(ModelRelease).order_by(ModelRelease.id.desc()).limit(100))
    ]


@router.post("/releases", status_code=201)
def register(
    body: s.ReleaseCreate, user: CurrentUser, session: SessionDep, request: Request
) -> s.ReleaseView:
    return reviews.create_release(
        session, user, body, request.app.state.settings, request.state.request_id
    )


@router.post("/releases/{release_id}/decisions")
def release_decision(
    release_id: ResourceId,
    body: s.ReleaseDecision,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> s.ReleaseView:
    return reviews.review_release(
        session, user, release_id, body, request.app.state.settings, request.state.request_id
    )
