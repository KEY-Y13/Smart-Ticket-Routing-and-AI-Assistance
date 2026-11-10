import math

import numpy as np
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from nlp.labels import LABEL_NAMES


def classification_metrics(y_true, y_pred) -> dict:
    labels = list(LABEL_NAMES)
    return {
        "sample_count": len(y_true),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(
            f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)
        ),
        "classification_report": classification_report(
            y_true,
            y_pred,
            labels=labels,
            target_names=list(LABEL_NAMES.values()),
            output_dict=True,
            zero_division=0,
        ),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
        "confusion_matrix_axis": "row=true, column=predicted; label order=0..5",
    }


def choose_threshold(y_true, probabilities, target_accuracy: float = 0.9) -> dict:
    """Use validation only; maximize coverage subject to empirical accepted accuracy."""
    if not 0 < target_accuracy <= 1:
        raise ValueError("目标接收准确率必须位于 (0,1]")
    probabilities = np.asarray(probabilities)
    y_true = np.asarray(y_true)
    if probabilities.shape != (len(y_true), len(LABEL_NAMES)) or not len(y_true):
        raise ValueError("阈值选择需要非空六类验证集预测")
    scores = probabilities.max(axis=1)
    predicted = probabilities.argmax(axis=1)
    best = {
        "enabled": False,
        "threshold": None,
        "accepted_count": 0,
        "coverage": 0.0,
        "accepted_accuracy": None,
    }
    for threshold in sorted(set(float(v) for v in scores)):
        accepted = scores >= threshold
        accuracy = float(np.mean(predicted[accepted] == y_true[accepted]))
        count = int(accepted.sum())
        if accuracy >= target_accuracy and count > best["accepted_count"]:
            best = {
                "enabled": True,
                "threshold": threshold,
                "accepted_count": count,
                "coverage": count / len(y_true),
                "accepted_accuracy": accuracy,
            }
    return {
        **best,
        "selected_on": "validation",
        "target_accuracy": target_accuracy,
        "validation_count": len(y_true),
        "score_is_calibrated_probability": False,
        "statistical_guarantee": False,
    }


def selective_metrics(y_true, probabilities, policy: dict) -> dict:
    probabilities = np.asarray(probabilities)
    accepted = np.zeros(len(y_true), dtype=bool)
    if policy["enabled"]:
        accepted = probabilities.max(axis=1) >= policy["threshold"]
    count = int(accepted.sum())
    return {
        "accepted_count": count,
        "sample_count": len(y_true),
        "coverage": count / len(y_true) if len(y_true) else 0.0,
        "accepted_accuracy": (
            float(np.mean(probabilities.argmax(axis=1)[accepted] == np.asarray(y_true)[accepted]))
            if count
            else None
        ),
    }


def percentile_latency(milliseconds: list[float]) -> dict:
    if not milliseconds or any(not math.isfinite(v) or v < 0 for v in milliseconds):
        raise ValueError("延迟记录必须是非空非负有限数值")
    return {
        "count": len(milliseconds),
        "p50_ms": float(np.percentile(milliseconds, 50)),
        "p95_ms": float(np.percentile(milliseconds, 95)),
        "boundary": "single-item CPU pipeline predict_proba, including TF-IDF; warmed",
        "concurrency": 1,
    }
