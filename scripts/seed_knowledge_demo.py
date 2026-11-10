"""Explicit fictional demo bootstrap, never claims independent human review."""

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument, User
from backend.app.db.runtime import Database
from backend.app.services.admin import audit
from rag.text import chunks, digest

root = Path(__file__).resolve().parents[1]
settings = Settings(_env_file=root / ".env")
db = Database(settings)
catalog = json.loads((root / "data/knowledge/catalog.json").read_text(encoding="utf-8"))
cases = []
with db.sessions() as session:
    admin = session.scalar(select(User).where(User.login == "admin", User.role == "ADMIN"))
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
    session.commit()
for key, _title, _text, *questions in catalog["items"]:
    for question in questions:
        cases.append(
            {
                "id": f"retrieval-{len(cases) + 1:03}",
                "query": question,
                "relevant_document_keys": [key],
                "answerable": True,
                "provenance": "AI_AUTHORED_RELEVANCE_NOT_INDEPENDENTLY_REVIEWED",
            }
        )
for question in [
    "明天上海会下雨吗",
    "股票应该买哪只",
    "帮我执行一笔银行转账",
    "今年公司有多少利润",
    "能否提供客户的身份证号码",
    "黄金市场最新价格",
    "讲一个太空探险故事",
    "附近餐馆在哪里",
    "帮我订一张去巴黎的机票",
    "给我管理员数据库密码",
]:
    cases.append(
        {
            "id": f"retrieval-{len(cases) + 1:03}",
            "query": question,
            "relevant_document_keys": [],
            "answerable": False,
            "provenance": "AI_AUTHORED_RELEVANCE_NOT_INDEPENDENTLY_REVIEWED",
        }
    )
target = root / "data/samples/retrieval-demo.jsonl"
if not target.exists():
    target.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8"
    )
print(
    {
        "demo_documents": len(catalog["items"]),
        "retrieval_cases": len(cases),
        "human_reviewed": False,
    }
)
db.dispose()
