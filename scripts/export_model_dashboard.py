"""Copy measured experiment metrics into a public-safe dashboard artifact, without host paths."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
formal = sorted((root / "artifacts/runs").glob("models-formal-*/comparison.json"))
sources = formal or sorted((root / "artifacts/runs").glob("models-smoke-*/comparison.json"))
if not sources:
    raise RuntimeError("No measured multi-model comparison exists")
source = sources[-1]
report = json.loads(source.read_text(encoding="utf-8"))
split = json.loads((source.parent / "split-manifest.json").read_text(encoding="utf-8"))
models = []
for name, values in report["models"].items():
    test = values["test"]
    models.append(
        {
            "model": name,
            "sample_count": test["sample_count"],
            "accuracy": test["accuracy"],
            "macro_f1": test["macro_f1"],
            "precision": test["classification_report"]["macro avg"]["precision"],
            "recall": test["classification_report"]["macro avg"]["recall"],
            "confusion_matrix": test["confusion_matrix"],
            "latency": values["latency"],
            "deployment_approved": False,
        }
    )
output = {
    "purpose": report["purpose"],
    "dataset_hash": report["dataset_hash"],
    "split_manifest_hash": report["split_manifest_hash"],
    "source": str(source.relative_to(root)).replace("\\", "/"),
    "data_note": (
        f"{split['sample_count']}条许可合成中文工单，经翻译/改编与AI主诉复核，固定同源分组切分；"
        f"训练{len(split['splits']['train'])}、验证{len(split['splits']['validation'])}、"
        f"测试{len(split['splits']['test'])}。同一合成来源的离线结果，非人工金标准或真实业务准确率。"
        if report["purpose"] == "formal"
        else "72条未独立复核合成文本，测试12条；仅验证训练流程。"
    ),
    "models": models,
    "sample_count": split["sample_count"],
    "split_counts": {name: len(ids) for name, ids in split["splits"].items()},
    "human_gold_certified": False,
}
target = root / (
    "artifacts/evaluation/classification-formal.json"
    if formal
    else "artifacts/evaluation/classification-smoke.json"
)
target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print(target)
