"""Shared data contract and reporting for independently executed model experiments."""

import csv
import importlib.metadata
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from nlp.artifacts import file_hash, write_json
from nlp.data.dataset import canonical_hash, load_samples, verify_manifest
from nlp.evaluation.metrics import (
    choose_threshold,
    classification_metrics,
    percentile_latency,
    selective_metrics,
)
from nlp.labels import label_map_hash


def experiment_inputs(data: Path, manifest_path: Path) -> tuple[dict, dict]:
    samples = load_samples(data)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verify_manifest(samples, manifest)
    lookup = {s.sample_id: s for s in samples}
    return manifest, {
        name: [lookup[sample_id] for sample_id in ids] for name, ids in manifest["splits"].items()
    }


def runtime_environment(packages: list[str]) -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {name: importlib.metadata.version(name) for name in packages},
    }


def normalized_scores(values) -> np.ndarray:
    probabilities = np.asarray(values, dtype=np.float64)
    if (
        probabilities.ndim != 2
        or probabilities.shape[1] != 6
        or not len(probabilities)
        or not np.isfinite(probabilities).all()
        or np.any(probabilities < 0)
        or np.any(probabilities > 1.001)
        or np.any(probabilities.sum(axis=1) <= 0)
    ):
        raise ValueError("模型必须输出有限的非负六类分数")
    # Native fastText adds a small epsilon; use a documented sum normalization.
    return probabilities / probabilities.sum(axis=1, keepdims=True)


def save_experiment(
    output: Path,
    name: str,
    manifest: dict,
    partitions: dict,
    validation_scores,
    test_scores,
    *,
    config: dict,
    environment: dict,
    latency_ms: list[float],
    train_ms: float,
    model_files: list[Path],
    target_accuracy: float = 0.9,
    extra: dict | None = None,
) -> dict:
    val = normalized_scores(validation_scores)
    test = normalized_scores(test_scores)
    if len(val) != len(partitions["validation"]) or len(test) != len(partitions["test"]):
        raise ValueError("模型输出与固定切分数量不一致")
    y_val = [s.label for s in partitions["validation"]]
    y_test = [s.label for s in partitions["test"]]
    policy = choose_threshold(y_val, val, target_accuracy)
    latency = percentile_latency(latency_ms)
    latency["boundary"] = config["latency_boundary"]
    code_root = Path(__file__).resolve().parent
    code_hashes = {
        path.relative_to(code_root).as_posix(): file_hash(path)
        for path in sorted(code_root.rglob("*.py"))
    }
    report = {
        "schema_version": 1,
        "name": name,
        "created_at": datetime.now(UTC).isoformat(),
        "purpose": manifest["purpose"],
        "evidence_scope": manifest["evidence_scope"],
        "dataset_hash": manifest["dataset_hash"],
        "label_map_hash": label_map_hash(),
        "split_manifest_hash": canonical_hash(manifest),
        "seed": manifest["seed"],
        "validation": classification_metrics(y_val, val.argmax(axis=1)),
        "test": classification_metrics(y_test, test.argmax(axis=1)),
        "test_selective": selective_metrics(y_test, test, policy),
        "threshold_policy": policy,
        "latency": latency,
        "train_ms": train_ms,
        "environment": environment,
        "model_reload_verified": True,
        "model_files": {str(p.relative_to(output)): file_hash(p) for p in model_files},
        "model_bytes": sum(p.stat().st_size for p in model_files),
        "experiment_code_hash": canonical_hash(code_hashes),
        "source_code_hashes": code_hashes,
        "deployment_approved": False,
        "limitations": manifest["limitations"],
        **(extra or {}),
    }
    write_json(output / "split-manifest.json", manifest)
    write_json(output / "config.json", config)
    write_json(output / "metrics.json", report)
    write_json(
        output / "manifest.json",
        {
            "name": name,
            "purpose": manifest["purpose"],
            "deployment_approved": False,
            "label_map_hash": label_map_hash(),
            "dataset_hash": manifest["dataset_hash"],
            "model_files": report["model_files"],
            "threshold_policy": policy,
        },
    )
    write_json(
        output / "errors.json",
        [
            {"sample_id": s.sample_id, "text": s.text, "true": s.label, "predicted": int(pred)}
            for s, pred in zip(partitions["test"], test.argmax(axis=1), strict=True)
            if s.label != int(pred)
        ],
    )
    with (output / "test-predictions.csv").open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "text", "true_label", "predicted_label", "max_score"])
        for s, probs in zip(partitions["test"], test, strict=True):
            writer.writerow([s.sample_id, s.text, s.label, int(probs.argmax()), float(probs.max())])
    return report
