"""Fetch pinned translation assets and verify hashes; never execute or call paid APIs."""

import argparse
import hashlib
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REVISION = "408d9bc410a388e1d9aef112a2daba955b945255"
MODEL = "Helsinki-NLP/opus-mt-en-zh"
FILES = {
    "README.md",
    "config.json",
    "generation_config.json",
    "source.spm",
    "target.spm",
    "tokenizer_config.json",
    "vocab.json",
    "pytorch_model.bin",
}


def download_model(model, revision, files, target):
    target.mkdir(parents=True, exist_ok=True)
    records = {}
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        response = client.get(
            f"https://huggingface.co/api/models/{model}/revision/{revision}?blobs=true"
        )
        response.raise_for_status()
        metadata = response.json()
        if metadata["sha"] != revision:
            raise RuntimeError("Model revision mismatch")
        for entry in metadata["siblings"]:
            name = entry["rfilename"]
            if name not in files:
                continue
            path = target / name
            if path.is_symlink() or not path.resolve().is_relative_to(target.resolve()):
                raise RuntimeError("Refusing linked model assets")
            url = f"https://huggingface.co/{model}/resolve/{revision}/{name}"
            expected = entry.get("lfs", {}).get("sha256")
            if name in {"pytorch_model.bin", "model.safetensors"} and not expected:
                raise RuntimeError("Missing expected weight hash")
            if not path.exists():
                partial = path.with_suffix(path.suffix + ".part")
                with client.stream("GET", url) as download:
                    download.raise_for_status()
                    with partial.open("xb") as output:
                        for block in download.iter_bytes():
                            output.write(block)
                partial.rename(path)
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if expected and digest != expected:
                raise RuntimeError("Model weight hash mismatch; retaining evidence")
            records[name] = {
                "bytes": path.stat().st_size,
                "sha256": digest,
                "expected_lfs_sha256": expected,
                "source_url": url,
            }
            print(
                {"file": name, "bytes": path.stat().st_size, "lfs_hash_verified": bool(expected)},
                flush=True,
            )
    if set(records) != files:
        raise RuntimeError("Missing model assets")
    (target / "source-manifest.json").write_text(
        json.dumps(
            {
                "model": model,
                "revision": revision,
                "license": "apache-2.0 according to publisher card",
                "runtime_requirement": (
                    "pickle .bin: isolated torch>=2.6 with weights_only; no remote code"
                    if "pytorch_model.bin" in files
                    else "local safetensors only; no pickle or remote code"
                ),
                "files": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--posteditor", action="store_true")
    parser.add_argument("--reviewer", action="store_true")
    args = parser.parse_args()
    if args.reviewer:
        download_model(
            "Qwen/Qwen2.5-1.5B-Instruct",
            "989aa7980e4cf806f80c7fef2b1adb7bc71aa306",
            {
                "LICENSE",
                "README.md",
                "config.json",
                "generation_config.json",
                "merges.txt",
                "model.safetensors",
                "tokenizer.json",
                "tokenizer_config.json",
                "vocab.json",
            },
            ROOT / "artifacts/pretrained/qwen25-1.5b-reviewer",
        )
    elif args.posteditor:
        download_model(
            "Qwen/Qwen3-0.6B",
            "c1899de289a04d12100db370d81485cdf75e47ca",
            {
                "LICENSE",
                "README.md",
                "config.json",
                "generation_config.json",
                "merges.txt",
                "model.safetensors",
                "tokenizer.json",
                "tokenizer_config.json",
                "vocab.json",
            },
            ROOT / "artifacts/pretrained/qwen3-0.6b-posteditor",
        )
    else:
        download_model(MODEL, REVISION, FILES, ROOT / "artifacts/pretrained/opus-mt-en-zh")


if __name__ == "__main__":
    main()
