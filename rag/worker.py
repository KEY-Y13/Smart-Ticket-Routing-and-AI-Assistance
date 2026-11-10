"""Single model process. Loopback/private network only, authenticated, no DB or LLM access."""

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path

import faiss
import numpy as np
import torch
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

from rag.text import bm25, digest, rrf

ROOT = Path(os.environ.get("SMARTTICKET_ARTIFACT_ROOT", "artifacts")).resolve()
LOCK = threading.Lock()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_models = None
_indexes = {}


def authorize(authorization):
    key = os.environ.get("SMARTTICKET_RETRIEVAL_SECRET", "")
    secret_path = Path("/run/secrets/SMARTTICKET_RETRIEVAL_SECRET")
    if not key and secret_path.is_file():
        key = secret_path.read_text().strip()
    if len(key) < 32 or not secrets.compare_digest(authorization or "", "Bearer " + key):
        raise HTTPException(401, "unauthorized")


def models():
    global _models
    if _models is None:
        torch.set_num_threads(4)
        objects = []
        for name, cls in (
            ("bge-small-zh-v1.5", AutoModel),
            ("bge-reranker-base", AutoModelForSequenceClassification),
        ):
            path = ROOT / "pretrained" / name
            manifest = json.loads((path / "source-manifest.json").read_text())
            for filename, spec in manifest["files"].items():
                if (
                    hashlib.file_digest((path / filename).open("rb"), "sha256").hexdigest()
                    != spec["sha256"]
                ):
                    raise RuntimeError("Model asset hash mismatch")
            tokenizer = AutoTokenizer.from_pretrained(
                path, local_files_only=True, trust_remote_code=False
            )
            model = cls.from_pretrained(
                path, use_safetensors=True, local_files_only=True, trust_remote_code=False
            ).eval()
            objects.extend([tokenizer, model, manifest["revision"]])
        _models = objects
    return _models


def encode(texts, query=False):
    tokenizer, model, *_ = models()
    vectors = []
    with torch.inference_mode():
        for start in range(0, len(texts), 16):
            batch = texts[start : start + 16]
            if query:
                batch = ["为这个句子生成表示以用于检索相关文章：" + text for text in batch]
            inputs = tokenizer(
                batch, padding=True, truncation=True, max_length=384, return_tensors="pt"
            )
            cls = model(**inputs).last_hidden_state[:, 0]
            vectors.append(torch.nn.functional.normalize(cls, p=2, dim=1).cpu().numpy())
    return np.ascontiguousarray(np.concatenate(vectors), dtype=np.float32)


class Chunk(BaseModel):
    id: str = Field(pattern=r"^[0-9]+$")
    text: str = Field(min_length=1, max_length=600)


class Build(BaseModel):
    generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    chunks: list[Chunk] = Field(min_length=1, max_length=10000)


@app.get("/health")
def health(authorization: str | None = Header(default=None)):
    authorize(authorization)
    return {"alive": True, "models_loaded": _models is not None}


@app.post("/build")
def build(body: Build, authorization: str | None = Header(default=None)):
    authorize(authorization)
    with LOCK:
        target = ROOT / "indexes" / body.generation
        target.mkdir(parents=True, exist_ok=False)
        texts = [c.text for c in body.chunks]
        embeddings = encode(texts)
        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(embeddings)
        faiss.write_index(index, str(target / "vectors.faiss"))
        metadata = {
            "generation": body.generation,
            "chunks": [c.model_dump() for c in body.chunks],
            "embedding_revision": models()[2],
            "reranker_revision": models()[5],
            "dimension": embeddings.shape[1],
            "vector_hash": hashlib.file_digest(
                (target / "vectors.faiss").open("rb"), "sha256"
            ).hexdigest(),
        }
        encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        (target / "manifest.json").write_text(encoded, encoding="utf-8")
        _indexes.clear()
        _indexes[body.generation] = (metadata, index)
        return {
            "manifest_hash": digest(encoded),
            "model_revision": models()[2],
            "chunk_count": len(texts),
        }


class Search(BaseModel):
    generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    query: str = Field(min_length=2, max_length=1000)
    strategy: str = Field(pattern=r"^(bm25|vector|hybrid|rerank)$")
    top_k: int = Field(default=30, ge=1, le=50)


@app.post("/search")
def search(body: Search, authorization: str | None = Header(default=None)):
    authorize(authorization)
    with LOCK:
        path = ROOT / "indexes" / body.generation
        encoded = (path / "manifest.json").read_text(encoding="utf-8")
        if digest(encoded) != body.manifest_hash:
            raise HTTPException(409, "Index hash mismatch")
        if body.generation not in _indexes:
            metadata = json.loads(encoded)
            if (
                hashlib.file_digest((path / "vectors.faiss").open("rb"), "sha256").hexdigest()
                != metadata["vector_hash"]
            ):
                raise HTTPException(409, "Vector hash mismatch")
            _indexes.clear()  # retain at most one warm generation
            _indexes[body.generation] = (metadata, faiss.read_index(str(path / "vectors.faiss")))
        metadata, index = _indexes[body.generation]
        if (
            metadata["embedding_revision"] != models()[2]
            or metadata["reranker_revision"] != models()[5]
        ):
            raise HTTPException(409, "Model revision changed; rebuild index")
        texts = [c["text"] for c in metadata["chunks"]]
        lexical = bm25(body.query, texts)
        sparse = sorted(
            (i for i, score in enumerate(lexical) if score > 0), key=lambda i: (-lexical[i], i)
        )[:30]
        distances, positions = index.search(encode([body.query], query=True), min(30, len(texts)))
        dense = positions[0].tolist()
        similarities = dict(zip(dense, distances[0].tolist(), strict=True))
        rankings = {"bm25": sparse, "vector": dense, "hybrid": rrf(sparse, dense)}
        ranked = rankings.get(body.strategy, rankings["hybrid"])
        reranked = {}
        if body.strategy == "rerank" and ranked:
            tokenizer, model = models()[3:5]
            with torch.inference_mode():
                for start in range(0, min(20, len(ranked)), 4):
                    subset = ranked[start : start + 4]
                    pairs = [[body.query, texts[i]] for i in subset]
                    inputs = tokenizer(
                        pairs, padding=True, truncation=True, max_length=384, return_tensors="pt"
                    )
                    values = model(**inputs).logits.view(-1).float().tolist()
                    reranked.update(zip(subset, values, strict=True))
            ranked = sorted(reranked, key=lambda i: (-reranked[i], i))
        return {
            "items": [
                {
                    "chunk_id": metadata["chunks"][i]["id"],
                    "score": lexical[i] if body.strategy == "bm25" else similarities.get(i, 0),
                    "rerank_score": reranked.get(i),
                }
                for i in ranked[: body.top_k]
            ]
        }
