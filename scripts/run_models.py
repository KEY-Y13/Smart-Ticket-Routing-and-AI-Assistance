"""Run all implemented classifiers against one immutable split manifest."""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--purpose", choices=("smoke", "formal"), default="smoke")
    parser.add_argument("--manifest", type=Path, help="复用已冻结切分，不重新抽样")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    base_python = root / ".conda/python.exe"
    bert_python = root / ".conda-bert/python.exe"
    ft_python = root / ".venv-fasttext/Scripts/python.exe"
    assets = root / "artifacts/pretrained/bert-base-chinese"
    if Path(sys.prefix).resolve() != (root / ".conda").resolve():
        raise RuntimeError("请通过 scripts/run_models.cmd 使用基础环境编排")
    if not bert_python.exists() or not ft_python.exists() or not assets.exists():
        raise RuntimeError("缺少隔离 ML 运行时或本地 BERT 资产，见文档，不会自动下载")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / f"artifacts/runs/models-{args.purpose}-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    data = args.data or root / "data/samples/classification-smoke.jsonl"
    manifest = output / "split-manifest.json"
    if args.manifest:
        shutil.copyfile(args.manifest, manifest)
    shared = ["--data", str(data), "--manifest", str(manifest)]
    commands = [
        (
            base_python,
            ["prepare", "--data", str(data), "--purpose", args.purpose, "--out", str(manifest)],
        ),
        (base_python, ["train-baselines", *shared, "--out", str(output / "baselines")]),
        (
            base_python,
            [
                "train-fasttext",
                *shared,
                "--python",
                str(ft_python),
                "--out",
                str(output / "fasttext"),
            ],
        ),
        (
            bert_python,
            [
                "train-bert",
                *shared,
                "--assets",
                str(assets),
                "--device",
                args.device,
                "--epochs",
                str(args.epochs),
                "--out",
                str(output / "bert"),
            ],
        ),
        (
            base_python,
            [
                "compare",
                "--reports",
                str(output / "baselines/comparison.json"),
                str(output / "fasttext/metrics.json"),
                str(output / "bert/metrics.json"),
                "--out",
                str(output / "comparison.json"),
            ],
        ),
    ]
    if args.manifest:
        commands = commands[1:]
    for index, (python, arguments) in enumerate(commands):
        command = [str(python), "-X", "utf8", "-m", "nlp.cli", *arguments]
        result = subprocess.run(
            command, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        (output / f"step-{index}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        print(result.stdout + result.stderr, flush=True)
        if result.returncode:
            (output / "failure.json").write_text(
                json.dumps(
                    {
                        "step": index,
                        "returncode": result.returncode,
                        "command": arguments,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return result.returncode
    print(f"用途 {args.purpose}；离线结果不能代表生产效果。对比报告: {output / 'comparison.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
