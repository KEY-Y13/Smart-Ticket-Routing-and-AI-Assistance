import json
import shutil
from pathlib import Path

from nlp.artifacts import file_hash, write_json

MODEL_ID = "google-bert/bert-base-chinese"
REVISION = "8f23c25b06e129b6c986331a13d8d025a92cf0ea"
FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json", "vocab.txt")


def prepare_assets(source: Path, destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError(f"拒绝覆盖模型资产: {destination}")
    if source.name != REVISION:
        raise ValueError("缓存目录 revision 与已核查官方 revision 不一致")
    if not all((source / name).is_file() for name in FILES):
        raise FileNotFoundError("缓存不完整，只允许复用 safetensors 和指定 tokenizer 文件")
    config = json.loads((source / "config.json").read_text(encoding="utf-8"))
    if config.get("model_type") != "bert" or config.get("hidden_size") != 768:
        raise ValueError("缓存不是目标中文 BERT 配置")
    destination.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in FILES:
        shutil.copy2(source / name, destination / name)
        hashes[name] = file_hash(destination / name)
        if hashes[name] != file_hash(source / name):
            raise ValueError(f"复制校验失败: {name}")
    provenance = {
        "model_id": MODEL_ID,
        "revision": REVISION,
        "license": "Apache-2.0",
        "model_card": f"https://huggingface.co/{MODEL_ID}/blob/{REVISION}/README.md",
        "origin": "read-only existing Hugging Face cache, copied into project",
        "files": hashes,
        "verification": (
            "local copy integrity and official revision metadata; not remote blob attestation"
        ),
        "remote_code": False,
        "serialization": "safetensors",
    }
    write_json(destination / "source-manifest.json", provenance)
    return provenance


def verify_assets(directory: Path) -> dict:
    manifest = json.loads((directory / "source-manifest.json").read_text(encoding="utf-8"))
    if manifest["model_id"] != MODEL_ID or manifest["revision"] != REVISION:
        raise ValueError("模型身份不匹配")
    if set(manifest["files"]) != set(FILES):
        raise ValueError("模型资产清单不完整")
    for name in FILES:
        if file_hash(directory / name) != manifest["files"][name]:
            raise ValueError(f"模型资产哈希变化: {name}")
    return manifest
