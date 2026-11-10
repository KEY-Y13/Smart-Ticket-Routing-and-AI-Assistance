"""Fictional demo bootstrap, visibly distinguished from independently reviewed policies."""

from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.services.admin import audit
from rag.text import chunks, digest


def seed_knowledge(session, admin, catalog):
    created = 0
    for key, title, text, *_questions in catalog["items"]:
        if session.scalar(
            select(KnowledgeDocument.id).where(KnowledgeDocument.document_key == key)
        ):
            continue
        content = "【虚构3C业务演示政策，非真实企业规定】\n" + text
        doc = KnowledgeDocument(
            document_key=key,
            revision=1,
            title=title,
            content=content,
            content_hash=digest(content),
            source=catalog["provenance"],
            status="PUBLISHED",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            author_id=admin.id,
            reviewer_id=None,
            review_note="DEMO_BOOTSTRAP_UNREVIEWED; never represent as human-reviewed policy",
        )
        session.add(doc)
        session.flush()
        for i, chunk in enumerate(chunks(content)):
            session.add(
                KnowledgeChunk(document_id=doc.id, position=i, text=chunk, text_hash=digest(chunk))
            )
        audit(
            session,
            admin,
            "DEMO_KNOWLEDGE_BOOTSTRAP",
            "knowledge",
            doc.id,
            {"independently_reviewed": False, "source": catalog["provenance"]},
            "demo-bootstrap",
        )
        created += 1
    return created
