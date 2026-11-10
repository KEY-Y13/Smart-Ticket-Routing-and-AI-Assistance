import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from nlp.artifacts import write_json
from nlp.baselines.train import train_baselines
from nlp.data.dataset import prepare_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SmartTicket 数据验证与真实基线实验")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="校验、相似归组和固定切分")
    prepare.add_argument("--data", type=Path, required=True)
    prepare.add_argument("--purpose", choices=("smoke", "formal"), required=True)
    prepare.add_argument("--seed", type=int, default=42)
    prepare.add_argument("--out", type=Path, required=True)
    train = commands.add_parser("train-baselines", help="拟合 RF/LR，验证集选阈值，测试集评估")
    train.add_argument("--data", type=Path, required=True)
    train.add_argument("--manifest", type=Path, required=True)
    train.add_argument("--target-accuracy", type=float, default=0.9)
    train.add_argument("--out", type=Path, required=True)
    fasttext = commands.add_parser("train-fasttext", help="通过隔离解释器执行 fastText")
    fasttext.add_argument("--data", type=Path, required=True)
    fasttext.add_argument("--manifest", type=Path, required=True)
    fasttext.add_argument("--python", type=Path, required=True)
    fasttext.add_argument("--out", type=Path, required=True)
    assets = commands.add_parser("prepare-bert-assets", help="复制并校验本地预训练缓存，不下载")
    assets.add_argument("--source", type=Path, required=True)
    assets.add_argument("--out", type=Path, required=True)
    bert = commands.add_parser("train-bert", help="使用本地 safetensors 微调中文 BERT")
    bert.add_argument("--data", type=Path, required=True)
    bert.add_argument("--manifest", type=Path, required=True)
    bert.add_argument("--assets", type=Path, required=True)
    bert.add_argument("--epochs", type=int, default=1)
    bert.add_argument("--device", choices=("cpu", "cuda"), required=True)
    bert.add_argument("--out", type=Path, required=True)
    compare = commands.add_parser("compare", help="只合并数据/切分一致的实测报告")
    compare.add_argument("--reports", type=Path, nargs="+", required=True)
    compare.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    output_preexisted = args.out.exists()
    try:
        if args.command == "prepare":
            manifest = prepare_manifest(args.data, args.purpose, args.seed)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.out, manifest)
            print(
                json.dumps(
                    {
                        "sample_count": manifest["sample_count"],
                        "purpose": manifest["purpose"],
                        "distribution": manifest["distribution"],
                        "audit": manifest["audit"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "train-baselines":
            report = train_baselines(args.data, args.manifest, args.out, args.target_accuracy)
            print(
                json.dumps(
                    {
                        "evidence_scope": report["evidence_scope"],
                        "models": {
                            k: {
                                "test_macro_f1": v["test"]["macro_f1"],
                                "test_count": v["test"]["sample_count"],
                            }
                            for k, v in report["models"].items()
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "train-fasttext":
            from nlp.fasttext.train import train_fasttext

            report = train_fasttext(args.data, args.manifest, args.out, args.python)
            print(
                json.dumps(
                    {
                        "name": report["name"],
                        "purpose": report["purpose"],
                        "test_macro_f1": report["test"]["macro_f1"],
                        "test_count": report["test"]["sample_count"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "prepare-bert-assets":
            from nlp.bert.assets import prepare_assets

            report = prepare_assets(args.source, args.out)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "train-bert":
            from nlp.bert.train import BertTrainingConfig, train_bert

            report = train_bert(
                args.data,
                args.manifest,
                args.assets,
                args.out,
                BertTrainingConfig(epochs=args.epochs, device=args.device),
            )
            print(
                json.dumps(
                    {
                        "name": report["name"],
                        "purpose": report["purpose"],
                        "test_macro_f1": report["test"]["macro_f1"],
                        "test_count": report["test"]["sample_count"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "compare":
            from nlp.evaluation.compare import compare_runs

            report = compare_runs(args.reports, args.out)
            print(json.dumps({"models": list(report["models"]), "purpose": report["purpose"]}))
        return 0
    except (ValueError, OSError, KeyError, RuntimeError, ImportError) as exc:
        print(f"失败: {exc}", file=sys.stderr)
        if args.command.startswith("train-") and not output_preexisted and args.out.is_dir():
            failure = args.out / "failure.json"
            if not failure.exists() and not (args.out / "comparison.json").exists():
                write_json(failure, {"error": str(exc), "at": datetime.now(UTC).isoformat()})
        return 2


if __name__ == "__main__":
    sys.exit(main())
