import json
from pathlib import Path

from nlp.artifacts import write_json
from nlp.data.dataset import canonical_hash
from nlp.labels import label_map_hash


def compare_runs(paths: list[Path], output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"拒绝覆盖对比报告: {output}")
    entries = {}
    identity = None
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        split = json.loads((path.parent / "split-manifest.json").read_text(encoding="utf-8"))
        if (
            report["split_manifest_hash"] != canonical_hash(split)
            or report["dataset_hash"] != split["dataset_hash"]
            or report["purpose"] != split["purpose"]
            or split["label_map_hash"] != label_map_hash()
        ):
            raise ValueError("不能比较：报告与实际数据切分清单不一致")
        current = (
            report["dataset_hash"],
            report["split_manifest_hash"],
            split["label_map_hash"],
            report["purpose"],
        )
        if identity is None:
            identity = current
        if current != identity:
            raise ValueError("不能比较不同数据、切分、标签或用途的实验")
        models = report.get("models", {report.get("name"): report})
        for name, model in models.items():
            if name in entries or name is None:
                raise ValueError("模型名称缺失或重复")
            entries[name] = {
                "source_report": str(path.resolve()),
                "test": model["test"],
                "test_selective": model["test_selective"],
                "latency": model["latency"],
                "environment": model["environment"],
                "model_bytes": model["model_bytes"],
            }
    if not entries:
        raise ValueError("没有可比较的实验")
    result = {
        "purpose": identity[3],
        "dataset_hash": identity[0],
        "split_manifest_hash": identity[1],
        "label_map_hash": identity[2],
        "models": entries,
        "limitations": [
            "smoke 未复核合成小样本不构成正式模型或业务效果结论",
            "不同设备与计时边界不可直接宣称模型速度优劣；请查看每模型 environment/latency",
            "没有根据此对比自动批准任何模型部署",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    return result
