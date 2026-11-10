import importlib.metadata
import json
from pathlib import Path

import joblib
import numpy as np

from nlp.artifacts import file_hash
from nlp.labels import LABEL_NAMES, label_map_hash


class ClassifierNotReady(RuntimeError):
    pass


class ArtifactClassifier:
    """Only load operator-configured, trusted, deployment-approved artifacts."""

    def __init__(self, manifest_path: Path):
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        if metadata["purpose"] != "formal" or not metadata["deployment_approved"]:
            raise ClassifierNotReady("模型没有通过正式评估和部署审批")
        if metadata["label_map_hash"] != label_map_hash():
            raise ClassifierNotReady("模型标签映射不匹配")
        for package, version in metadata["packages"].items():
            if importlib.metadata.version(package) != version:
                raise ClassifierNotReady("模型依赖版本与训练环境不同")
        model_path = (manifest_path.parent / metadata["model_file"]).resolve()
        if model_path.parent != manifest_path.parent.resolve():
            raise ClassifierNotReady("模型路径超出产物目录")
        if file_hash(model_path) != metadata["model_sha256"]:
            raise ClassifierNotReady("模型文件哈希校验失败")
        self.pipeline = joblib.load(model_path)
        if list(self.pipeline.classes_) != list(LABEL_NAMES):
            raise ClassifierNotReady("模型类别顺序错误")
        self.metadata = metadata

    def predict(self, text: str) -> dict:
        scores = self.pipeline.predict_proba([text])[0]
        if (
            scores.shape != (6,)
            or not np.isfinite(scores).all()
            or np.any(scores < 0)
            or np.any(scores > 1)
            or not np.isclose(scores.sum(), 1)
        ):
            raise ClassifierNotReady("模型输出无效")
        label = int(scores.argmax())
        policy = self.metadata["threshold_policy"]
        accepted = policy["enabled"] and float(max(scores)) >= policy["threshold"]
        if self.metadata.get("deployment_scope") == "SHADOW":
            accepted = False
        return {
            "label": label if accepted else None,
            "label_name": LABEL_NAMES[label] if accepted else None,
            "candidate_label": label,
            "scores": {str(i): float(score) for i, score in enumerate(scores)},
            "needs_manual_review": not accepted,
            "model_version": self.metadata["model_sha256"],
            "score_is_calibrated_probability": False,
            "deployment_scope": self.metadata.get("deployment_scope", "LIVE"),
        }
