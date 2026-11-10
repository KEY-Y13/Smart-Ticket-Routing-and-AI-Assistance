import csv
import importlib.metadata
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from nlp.artifacts import file_hash, write_json
from nlp.data.dataset import canonical_hash, load_samples, verify_manifest
from nlp.evaluation.metrics import (
    choose_threshold,
    classification_metrics,
    percentile_latency,
    selective_metrics,
)
from nlp.labels import LABEL_NAMES, label_map_hash


def train_baselines(
    data_path: Path, manifest_path: Path, output: Path, target_accuracy: float = 0.9
) -> dict:
    samples = load_samples(data_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verify_manifest(samples, manifest)
    if not 0 < target_accuracy <= 1:
        raise ValueError("目标准确率必须位于 (0,1]")
    if output.exists():
        raise FileExistsError(f"拒绝覆盖已有实验目录: {output}")
    by_id = {s.sample_id: s for s in samples}
    partitions = {name: [by_id[i] for i in ids] for name, ids in manifest["splits"].items()}
    train = partitions["train"]
    validation = partitions["validation"]
    test = partitions["test"]
    seed = manifest["seed"]
    models = {
        "tfidf_rf": RandomForestClassifier(
            n_estimators=100, max_depth=30, class_weight="balanced", random_state=seed, n_jobs=1
        ),
        "tfidf_logreg": LogisticRegression(
            max_iter=1000, class_weight="balanced", random_state=seed
        ),
    }
    output.mkdir(parents=True, exist_ok=False)
    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "scikit-learn", "scipy", "joblib")
        },
        "measurement_device": "CPU",
        "memory_measurement": "not_measured",
        "gpu_memory_measurement": "not_applicable_cpu_run",
    }
    source_root = Path(__file__).resolve().parents[1]
    code_hashes = {
        str(path.relative_to(source_root)).replace("\\", "/"): file_hash(path)
        for path in sorted(source_root.rglob("*.py"))
    }
    comparison = {
        "created_at": datetime.now(UTC).isoformat(),
        "purpose": manifest["purpose"],
        "evidence_scope": manifest["evidence_scope"],
        "dataset_hash": manifest["dataset_hash"],
        "split_manifest_hash": canonical_hash(manifest),
        "seed": seed,
        "source_code_hashes": code_hashes,
        "experiment_code_hash": canonical_hash(code_hashes),
        "environment": environment,
        "models": {},
        "limitations": manifest["limitations"],
        "fasttext": "not_run",
        "bert": "not_run",
    }
    write_json(output / "split-manifest.json", manifest)
    for name, estimator in models.items():
        model_dir = output / name
        model_dir.mkdir()
        pipeline = Pipeline(
            [
                # Chinese char n-grams avoid a hidden jieba dictionary dependency.
                ("tfidf", TfidfVectorizer(analyzer="char", ngram_range=(1, 2), max_features=20000)),
                ("classifier", estimator),
            ]
        )
        started = perf_counter()
        pipeline.fit([s.text for s in train], [s.label for s in train])
        train_ms = (perf_counter() - started) * 1000
        if list(pipeline.classes_) != list(LABEL_NAMES):
            raise ValueError("模型 classes_ 与固定标签顺序不一致")
        val_probs = pipeline.predict_proba([s.text for s in validation])
        policy = choose_threshold([s.label for s in validation], val_probs, target_accuracy)
        test_probs = pipeline.predict_proba([s.text for s in test])
        predicted = test_probs.argmax(axis=1)
        pipeline.predict_proba([test[0].text])  # warmup, excluded from measurements
        latency = []
        for _ in range(5):
            for sample in test:
                start = perf_counter()
                pipeline.predict_proba([sample.text])
                latency.append((perf_counter() - start) * 1000)
        model_path = model_dir / "model.joblib"
        joblib.dump(pipeline, model_path)
        loaded = joblib.load(model_path)  # own artifact only; never load an uploaded pickle
        np.testing.assert_allclose(
            loaded.predict_proba([s.text for s in test]), test_probs, rtol=1e-12, atol=1e-12
        )
        report = {
            "purpose": manifest["purpose"],
            "evidence_scope": manifest["evidence_scope"],
            "validation": classification_metrics(
                [s.label for s in validation], val_probs.argmax(axis=1)
            ),
            "test": classification_metrics([s.label for s in test], predicted),
            "test_selective": selective_metrics([s.label for s in test], test_probs, policy),
            "threshold_policy": policy,
            "latency": percentile_latency(latency),
            "train_ms": train_ms,
            "train_shape": list(pipeline["tfidf"].transform([s.text for s in train]).shape),
            "model_bytes": model_path.stat().st_size,
            "model_reload_verified": True,
            "environment": environment,
        }
        config = {
            "seed": seed,
            "vectorizer": {
                "analyzer": "char",
                "ngram_range": [1, 2],
                "max_features": 20000,
                "fit_partition": "train_only",
            },
            "estimator": estimator.get_params(),
            "threshold_target_accuracy": target_accuracy,
            "threshold_selection_partition": "validation_only",
        }
        metadata = {
            "schema_version": 1,
            "name": name,
            "model_file": "model.joblib",
            "model_sha256": file_hash(model_path),
            "label_map_hash": label_map_hash(),
            "dataset_hash": manifest["dataset_hash"],
            "split_manifest_hash": canonical_hash(manifest),
            "purpose": manifest["purpose"],
            "evidence_scope": manifest["evidence_scope"],
            "threshold_policy": policy,
            "deployment_approved": False,
            "packages": environment["packages"],
        }
        write_json(model_dir / "manifest.json", metadata)
        write_json(model_dir / "config.json", config)
        write_json(model_dir / "metrics.json", report)
        with (model_dir / "test-predictions.csv").open("x", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sample_id", "text", "true_label", "predicted_label", "max_score"])
            for sample, pred, probs in zip(test, predicted, test_probs, strict=True):
                writer.writerow(
                    [sample.sample_id, sample.text, sample.label, int(pred), max(probs)]
                )
        errors = [
            {"sample_id": s.sample_id, "text": s.text, "true": s.label, "predicted": int(pred)}
            for s, pred in zip(test, predicted, strict=True)
            if s.label != int(pred)
        ]
        write_json(model_dir / "errors.json", errors)
        comparison["models"][name] = report
    write_json(output / "comparison.json", comparison)
    return comparison
