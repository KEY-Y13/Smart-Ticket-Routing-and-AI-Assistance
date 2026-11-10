"""Standalone worker: only stdlib, fasttext, NumPy 1.x and psutil are needed."""

import importlib.metadata
import json
import platform
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path
from time import perf_counter

import fasttext
import numpy as np
import psutil


def preprocess(text: str) -> str:
    return " ".join(c for c in unicodedata.normalize("NFKC", text) if not c.isspace())


def predictions(model, records) -> list[list[float]]:
    values = []
    for record in records:
        labels, probabilities = model.predict(preprocess(record["text"]), k=6)
        by_label = dict(zip(labels, probabilities, strict=True))
        if set(by_label) != {f"__label__{i}" for i in range(6)}:
            raise ValueError("fastText 没有返回完整的六类标签")
        values.append([float(by_label[f"__label__{i}"]) for i in range(6)])
    return values


def run_native(payload: dict, output: Path, scratch: Path) -> dict:
    train_path = scratch / "train.fasttext.txt"
    with train_path.open("x", encoding="utf-8") as handle:
        for record in payload["partitions"]["train"]:
            handle.write(f"__label__{record['label']} {preprocess(record['text'])}\n")
    shutil.copyfile(train_path, output / "train.fasttext.txt")
    started = perf_counter()
    model = fasttext.train_supervised(input=str(train_path), **payload["parameters"])
    train_ms = (perf_counter() - started) * 1000
    model_path = scratch / "model.bin"
    model.save_model(str(model_path))
    shutil.copyfile(model_path, output / "model.bin")
    partitions = payload["partitions"]
    validation = predictions(model, partitions["validation"])
    test = predictions(model, partitions["test"])
    reloaded = fasttext.load_model(str(model_path))
    np.testing.assert_allclose(
        predictions(reloaded, partitions["test"]), test, rtol=1e-6, atol=1e-7
    )
    predictions(reloaded, partitions["test"][:1])
    latency = []
    for _ in range(5):
        for record in partitions["test"]:
            start = perf_counter()
            predictions(reloaded, [record])
            latency.append((perf_counter() - start) * 1000)
    memory = psutil.Process().memory_info()
    result = {
        "validation_scores": validation,
        "test_scores": test,
        "latency_ms": latency,
        "train_ms": train_ms,
        "dimension": model.get_dimension(),
        "model_reload_verified": True,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "measurement_device": "CPU",
            "distribution": "third-party fasttext-wheel",
            "native_path_bridge": "ASCII scratch for native IO; Python copies Unicode output paths",
            "packages": {
                n: importlib.metadata.version(n) for n in ["fasttext-wheel", "numpy", "psutil"]
            },
            "process_rss_bytes_after_reload": memory.rss,
            "process_peak_working_set_bytes": getattr(memory, "peak_wset", None),
            "memory_boundary": "whole worker lifetime, including original and reloaded model",
        },
    }
    return result


def main(payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    output = payload_path.parent
    native_root = Path(__file__).resolve().parents[2] / "artifacts/native-workspaces"
    if not str(native_root).isascii():
        raise ValueError("当前 Windows wheel 需要 ASCII 项目路径作为原生临时工作目录")
    native_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ft-", dir=native_root) as temporary:
        scratch = Path(temporary).resolve()
        if not scratch.is_relative_to(native_root.resolve()):
            raise ValueError("原生临时目录不在项目管理的 workspace 内")
        result = run_native(payload, output, scratch)
    with (output / "worker-result.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"status": "completed", "samples": len(result["test_scores"])}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
