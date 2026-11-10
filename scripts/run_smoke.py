"""Run the small synthetic baseline smoke in fresh, non-overwriting output paths."""

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    if Path(sys.prefix).resolve() != (project / ".conda").resolve():
        raise RuntimeError("请使用项目独立 Conda 环境运行实验")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    data = project / "data/samples/classification-smoke.jsonl"
    manifest = project / f"data/processed/smoke-{stamp}.json"
    output = project / f"artifacts/runs/smoke-{stamp}"
    print("仅运行 72 条未复核合成样例的 CPU smoke；无 LLM/预训练模型下载。", flush=True)
    commands = [
        ["prepare", "--data", str(data), "--purpose", "smoke", "--out", str(manifest)],
        ["train-baselines", "--data", str(data), "--manifest", str(manifest), "--out", str(output)],
    ]
    for args in commands:
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "nlp.cli", *args], cwd=project)
        if result.returncode:
            return result.returncode
    print(f"报告: {output / 'comparison.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
