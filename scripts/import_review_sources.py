"""Import candidates only. This tool NEVER records a human decision or approves a model."""

import argparse
import json
import subprocess
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.db.runtime import Database
from backend.app.schemas.reviews import CollectionCreate
from backend.app.services.reviews import create_collection

ROOT = Path(__file__).resolve().parents[1]


def reference_sources():
    samples = [
        json.loads(line)
        for line in (ROOT / "data/samples/classification-smoke.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    catalog = json.loads((ROOT / "data/knowledge/catalog.json").read_text(encoding="utf-8"))
    retrieval = [
        json.loads(line)
        for line in (ROOT / "data/samples/retrieval-demo.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    result = [
        ("reference-classification-v1", "分类候选复核 · 72条", "CLASSIFICATION", samples),
        (
            "reference-knowledge-v1",
            "虚构政策核对 · 50篇",
            "KNOWLEDGE",
            [
                {
                    "id": row[0],
                    "title": row[1],
                    "content": row[2],
                    "provenance": catalog["provenance"],
                }
                for row in catalog["items"]
            ],
        ),
        ("reference-retrieval-v1", "检索真值复核 · 110条", "RETRIEVAL", retrieval),
    ]
    live = ROOT / "artifacts/evaluation/bailian-live-20261107T012904456529Z/report.json"
    if live.is_file():
        rows = json.loads(live.read_text(encoding="utf-8"))["results"]
        result.append(
            (
                "reference-llm-v1",
                "真实百炼回复评审 · 50例",
                "LLM",
                [
                    {
                        "id": f"live-{row['id']:02}",
                        "question": row["question"],
                        "output": row.get("body"),
                        "raw_output": row.get("raw_output"),
                        "evidence": row.get("evidence", []),
                        "error_code": row.get("error_code"),
                        "refused": row["status"] != "DRAFT_CREATED",
                        "suggested_document_key": row["document_key"],
                        "model": row.get("model_version"),
                        "usage": row.get("usage", {}),
                    }
                    for row in rows
                ],
            )
        )
    return result


def import_collections(sources, provenance):
    db = Database(Settings(_env_file=ROOT / ".env"))
    try:
        with db.sessions() as session:
            actor = session.scalar(select(User).where(User.login == "admin"))
            if actor is None:
                raise ValueError("Initialize the project administrator first")
            for code, title, kind, rows in sources:
                body = CollectionCreate(
                    code=code, title=title, kind=kind, provenance=provenance, items=rows
                )
                result = create_collection(session, actor, body, "explicit-cli-candidate-import")
                print({"collection": result.code, "kind": kind, "counts": result.counts})
    finally:
        db.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--classification", type=Path)
    parser.add_argument("--code")
    parser.add_argument("--provenance")
    parser.add_argument("--docker", action="store_true", help="导入隔离Compose业务库，不操作本机库")
    args = parser.parse_args()
    if args.classification:
        if not args.code or not args.provenance or args.classification.stat().st_size > 20_000_000:
            raise ValueError("Custom import requires code/provenance and at most 20MB")
        rows = [
            json.loads(line)
            for line in args.classification.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        sources = [(args.code, "分类数据复核 · " + args.code, "CLASSIFICATION", rows)]
        provenance = args.provenance
    else:
        sources = reference_sources()
        provenance = "AI_AUTHORED_FICTIONAL_REFERENCE; REAL_LLM_WHERE_MARKED; NOT_HUMAN_REVIEWED"
    if args.docker:
        code = (
            "import json,sys; from scripts.import_review_sources import import_collections; "
            "p=json.load(sys.stdin); import_collections(p['sources'],p['provenance'])"
        )
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "api", "python", "-c", code],
            input=json.dumps({"sources": sources, "provenance": provenance}),
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError("Compose candidate import failed; no automatic review performed")
        print(result.stdout)
    else:
        import_collections(sources, provenance)


if __name__ == "__main__":
    main()
