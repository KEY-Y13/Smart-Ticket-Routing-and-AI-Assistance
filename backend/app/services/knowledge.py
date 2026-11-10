import time
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from backend.app.core.errors import AppError
from backend.app.db.models import JobTask, KnowledgeChunk, KnowledgeDocument, KnowledgeIndex, utcnow
from backend.app.schemas.intelligence import Evidence, KnowledgeView, SearchResult
from backend.app.services.admin import audit
from backend.app.services.tickets import check_version, lock_dispatch, require_role
from rag.text import bm25, chunks, digest, suspicious


def current(document, now=None):
    now = now or utcnow()
    return (
        document.status == "PUBLISHED"
        and document.valid_from <= now
        and (document.valid_until is None or now < document.valid_until)
    )


def create(session, user, body, request_id):
    require_role(user, "ADMIN")
    if suspicious(body.content) or "<script" in body.content.casefold():
        raise AppError(422, "UNSAFE_DOCUMENT", "文档含疑似注入或脚本，请人工检查")
    lock_dispatch(session)
    revision = (
        session.scalar(
            select(func.max(KnowledgeDocument.revision)).where(
                KnowledgeDocument.document_key == body.document_key
            )
        )
        or 0
    ) + 1
    doc = KnowledgeDocument(
        **body.model_dump(), revision=revision, content_hash=digest(body.content), author_id=user.id
    )
    session.add(doc)
    session.flush()
    for i, text in enumerate(chunks(body.content)):
        session.add(
            KnowledgeChunk(document_id=doc.id, position=i, text=text, text_hash=digest(text))
        )
    audit(
        session,
        user,
        "KNOWLEDGE_CREATED",
        "knowledge",
        doc.id,
        {"revision": revision, "hash": doc.content_hash},
        request_id,
    )
    session.commit()
    return KnowledgeView.model_validate(doc)


def act(session, user, document_id, body, request_id):
    require_role(user, "ADMIN")
    lock_dispatch(session)
    doc = session.get(KnowledgeDocument, document_id)
    if doc is None:
        raise AppError(404, "KNOWLEDGE_NOT_FOUND", "文档不存在")
    check_version(doc, body.expected_version)
    target = {
        "review": ("DRAFT", "REVIEWED"),
        "publish": ("REVIEWED", "PUBLISHED"),
        "retire": ("PUBLISHED", "RETIRED"),
    }[body.action]
    if doc.status != target[0]:
        raise AppError(409, "KNOWLEDGE_STATE", "文档状态不允许此操作")
    if body.action == "publish":
        # A new published revision atomically retires the previous revision.
        for old in session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.document_key == doc.document_key,
                KnowledgeDocument.status == "PUBLISHED",
            )
        ):
            if doc.valid_from > utcnow() and old.valid_from < doc.valid_from:
                old.valid_until = (
                    min(old.valid_until, doc.valid_from) if old.valid_until else doc.valid_from
                )
            else:
                old.status = "RETIRED"
    doc.status = target[1]
    if body.action == "review":
        doc.reviewer_id, doc.review_note = user.id, body.reason
    if body.action in {"publish", "retire"}:
        session.add(
            JobTask(
                type="BUILD_KNOWLEDGE_INDEX",
                dedupe_key=f"knowledge:{doc.id}:{doc.version}:{body.action}",
                payload={"actor_id": user.id},
            )
        )
    audit(
        session,
        user,
        "KNOWLEDGE_" + body.action.upper(),
        "knowledge",
        doc.id,
        {"reason": body.reason},
        request_id,
    )
    session.commit()
    return KnowledgeView.model_validate(doc)


def remote(settings, endpoint, payload):
    if settings.retrieval_secret is None:
        raise AppError(503, "RETRIEVAL_UNCONFIGURED", "模型检索服务尚未配置；可选 BM25")
    try:
        with httpx.Client(timeout=httpx.Timeout(180, connect=5), trust_env=False) as client:
            response = client.post(
                settings.retrieval_url + endpoint,
                json=payload,
                headers={"Authorization": "Bearer " + settings.retrieval_secret.get_secret_value()},
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            503, "RETRIEVAL_UNAVAILABLE", "本地模型检索失败，请检查模型服务或使用 BM25"
        ) from exc


def build_index(session, user, settings, request_id):
    require_role(user, "ADMIN")
    lock_dispatch(session)
    rows = session.execute(
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument)
        .where(KnowledgeDocument.status == "PUBLISHED")
        .order_by(KnowledgeChunk.id)
    ).all()
    # Include scheduled published versions; the DB validity filter is authoritative at query time.
    payload = [{"id": str(c.id), "text": d.title + "\n" + c.text} for c, d in rows]
    if not payload:
        for old in session.scalars(select(KnowledgeIndex).where(KnowledgeIndex.active.is_(True))):
            old.active = False
        audit(session, user, "EMPTY_INDEX_ACTIVATED", "knowledge_index", 0, {}, request_id)
        session.commit()
        return {"generation": None, "chunk_count": 0, "model_revision": "EMPTY"}
    generation = uuid4().hex
    result = remote(settings, "/build", {"generation": generation, "chunks": payload})
    for old in session.scalars(select(KnowledgeIndex).where(KnowledgeIndex.active.is_(True))):
        old.active = False
    index = KnowledgeIndex(generation=generation, active=True, **result)
    session.add(index)
    session.flush()
    audit(
        session,
        user,
        "INDEX_ACTIVATED",
        "knowledge_index",
        index.id,
        {"generation": generation, "chunks": len(payload)},
        request_id,
    )
    session.commit()
    return {"generation": generation, **result}


def search(session, settings, body):
    start = time.perf_counter()
    rows = session.execute(
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument)
        .where(KnowledgeDocument.status == "PUBLISHED")
        .order_by(KnowledgeChunk.id)
    ).all()
    allowed = {c.id: (c, d) for c, d in rows if current(d) and not suspicious(c.text)}
    if not allowed:
        return SearchResult(
            generation=None,
            strategy=body.strategy,
            items=[],
            latency_ms=int((time.perf_counter() - start) * 1000),
            sufficient=False,
            reason="NO_SUFFICIENT_CURRENT_EVIDENCE",
        )
    generation = None
    if body.strategy == "bm25":
        ids = list(allowed)
        scores = bm25(body.query, [allowed[i][1].title + "\n" + allowed[i][0].text for i in ids])
        hits = [
            {"chunk_id": str(ids[i]), "score": scores[i]}
            for i in sorted(range(len(ids)), key=lambda i: -scores[i])
            if scores[i] > 0
        ]
    else:
        index = session.scalar(
            select(KnowledgeIndex)
            .where(KnowledgeIndex.active.is_(True))
            .order_by(KnowledgeIndex.id.desc())
        )
        if index is None:
            raise AppError(503, "INDEX_NOT_READY", "请先构建并激活知识索引；BM25 可独立使用")
        generation = index.generation
        hits = remote(
            settings,
            "/search",
            {
                "generation": generation,
                "manifest_hash": index.manifest_hash,
                "query": body.query,
                "strategy": body.strategy,
                "top_k": 30,
            },
        )["items"]
    items = []
    for hit in hits:
        pair = allowed.get(int(hit["chunk_id"]))
        if pair is None:
            continue
        c, d = pair
        items.append(
            Evidence(
                chunk_id=c.id,
                document_id=d.id,
                title=d.title,
                revision=d.revision,
                text=c.text,
                text_hash=c.text_hash,
                source=d.source,
                score=hit["score"],
                rerank_score=hit.get("rerank_score"),
            )
        )
        if len(items) >= body.top_k:
            break
    # Conservative operational gate, not a calibrated probability of answer correctness.
    sufficient = bool(items) and (
        body.strategy != "rerank"
        or (items[0].rerank_score is not None and items[0].rerank_score >= 0)
    )
    return SearchResult(
        generation=generation,
        strategy=body.strategy,
        items=items,
        latency_ms=int((time.perf_counter() - start) * 1000),
        sufficient=sufficient,
        reason=None if sufficient else "NO_SUFFICIENT_CURRENT_EVIDENCE",
    )
