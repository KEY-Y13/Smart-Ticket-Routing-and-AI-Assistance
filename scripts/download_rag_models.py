"""Download only pinned official BAAI safetensors/tokenizer assets, verify LFS hashes."""

import hashlib
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    session = requests.Session()
    for model in ("bge-small-zh-v1.5", "bge-reranker-base"):
        repo = "BAAI/" + model
        response = session.get(f"https://huggingface.co/api/models/{repo}?blobs=true", timeout=60)
        response.raise_for_status()
        info = response.json()
        target = ROOT / "artifacts/pretrained" / model
        target.mkdir(parents=True, exist_ok=True)
        manifest = {
            "repo": repo,
            "revision": info["sha"],
            "license": info.get("cardData", {}).get("license"),
            "files": {},
        }
        names = {
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "vocab.txt",
            "sentencepiece.bpe.model",
            "README.md",
        }
        for entry in info["siblings"]:
            name = entry["rfilename"]
            if name not in names:
                continue
            path = target / name
            expected = entry.get("lfs", {}).get("sha256")
            if not path.exists() or (
                expected and hashlib.file_digest(path.open("rb"), "sha256").hexdigest() != expected
            ):
                url = f"https://huggingface.co/{repo}/resolve/{info['sha']}/{name}"
                with session.get(url, stream=True, timeout=(45, 180)) as download:
                    download.raise_for_status()
                    partial = path.with_suffix(path.suffix + ".partial")
                    with partial.open("wb") as out:
                        for chunk in download.iter_content(1024 * 1024):
                            out.write(chunk)
                    digest = hashlib.file_digest(partial.open("rb"), "sha256").hexdigest()
                    if expected and digest != expected:
                        raise RuntimeError("official LFS hash mismatch: " + name)
                    partial.replace(path)
            digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
            manifest["files"][name] = {
                "sha256": digest,
                "bytes": path.stat().st_size,
                "official_lfs_sha256": expected,
            }
            print(model, name, path.stat().st_size, flush=True)
        (target / "source-manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
