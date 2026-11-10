import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from nlp.artifacts import write_json
from nlp.experiments import experiment_inputs, save_experiment


def train_fasttext(data: Path, manifest_path: Path, output: Path, python: Path) -> dict:
    manifest, partitions = experiment_inputs(data, manifest_path)
    if output.exists():
        raise FileExistsError(f"拒绝覆盖实验目录: {output}")
    if not python.is_file():
        raise FileNotFoundError(f"缺少独立 fastText 解释器: {python}")
    output.mkdir(parents=True, exist_ok=False)
    parameters = {
        "dim": 64,
        "epoch": 50,
        "lr": 0.5,
        "wordNgrams": 2,
        "bucket": 50000,
        "minCount": 1,
        "minn": 2,
        "maxn": 4,
        "loss": "softmax",
        "thread": 1,
        "verbose": 1,
        "seed": manifest["seed"],
    }
    config = {
        "seed": manifest["seed"],
        "native_seed": manifest["seed"],
        "native_seed_note": "explicit native seed and thread=1",
        "preprocessing": "NFKC, remove whitespace, separate every character",
        "parameters": parameters,
        "latency_boundary": "single-item CPU character preprocessing + native predict; warmed",
    }
    payload = output / "worker-input.json"
    write_json(
        payload,
        {
            "parameters": parameters,
            "partitions": {
                name: [s.model_dump() for s in part] for name, part in partitions.items()
            },
        },
    )
    worker = Path(__file__).with_name("worker.py")
    result = subprocess.run(
        [str(python.resolve()), "-X", "utf8", str(worker), str(payload.resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    (output / "worker.stdout.log").write_text(result.stdout, encoding="utf-8")
    (output / "worker.stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        write_json(
            output / "failure.json",
            {
                "returncode": result.returncode,
                "at": datetime.now(UTC).isoformat(),
                "message": "fastText worker failed; see worker.stderr.log",
            },
        )
        raise RuntimeError(f"fastText 训练失败，错误已保存: {output / 'worker.stderr.log'}")
    values = json.loads((output / "worker-result.json").read_text(encoding="utf-8"))
    if values["model_reload_verified"] is not True:
        raise ValueError("模型重载校验失败")
    return save_experiment(
        output,
        "fasttext_char",
        manifest,
        partitions,
        values["validation_scores"],
        values["test_scores"],
        config=config,
        environment=values["environment"],
        latency_ms=values["latency_ms"],
        train_ms=values["train_ms"],
        model_files=[output / "model.bin"],
        extra={
            "representation_dimension": values["dimension"],
            "score_transform": "normalize native six scores by their sum",
        },
    )
