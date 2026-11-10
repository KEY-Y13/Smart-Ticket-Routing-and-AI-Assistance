"""Read-only domain-shift diagnostic; never tune models or thresholds against these cases."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/evaluation/classification-domain-v3"


def main():
    import numpy as np

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kind", choices=["baselines", "fasttext", "bert", "report"], required=True
    )
    args = parser.parse_args()
    run = sorted((ROOT / "artifacts/runs").glob("models-formal-*/comparison.json"))[-1].parent
    original = [
        json.loads(line)
        for line in (ROOT / "data/samples/classification-smoke.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    cases = [
        {
            "id": row["sample_id"],
            "text": row["text"],
            "expected": 1 if row["sample_id"] == "smoke-0-09" else row["label"],
        }
        for row in original
    ]
    cases.extend(
        {"id": row["id"], "text": row["text"], "expected": row["expected_label"]}
        for row in (
            json.loads(line)
            for line in (ROOT / "data/samples/classification-challenge-v2.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
    )
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    if args.kind == "baselines":
        import joblib

        for name in ("tfidf_rf", "tfidf_logreg"):
            model = joblib.load(run / "baselines" / name / "model.joblib")
            results[name] = model.predict_proba([row["text"] for row in cases]).tolist()
    elif args.kind == "fasttext":
        import fasttext

        from nlp.fasttext.worker import predictions

        model = fasttext.load_model(str(run / "fasttext/model.bin"))
        results["fasttext_char"] = predictions(model, cases)
    elif args.kind == "bert":
        import torch
        from transformers import BertForSequenceClassification, BertTokenizerFast

        torch.set_num_threads(4)
        path = run / "bert/best-model"
        tokenizer = BertTokenizerFast.from_pretrained(path, local_files_only=True)
        model = (
            BertForSequenceClassification.from_pretrained(
                path, local_files_only=True, use_safetensors=True
            )
            .to("cuda")
            .eval()
        )
        scores = []
        with torch.inference_mode():
            for start in range(0, len(cases), 8):
                inputs = tokenizer(
                    [row["text"] for row in cases[start : start + 8]],
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=128,
                ).to("cuda")
                scores.extend(model(**inputs).logits.softmax(dim=-1).cpu().tolist())
        results["bert_base_chinese"] = scores
    else:
        from nlp.evaluation.metrics import classification_metrics

        for file in ("baselines", "fasttext", "bert"):
            results.update(json.loads((OUT / (file + "-scores.json")).read_text()))
        report = {
            "scope": (
                "Native 3C domain-shift diagnostic and frozen edge cases; AI reference only. "
                "Earlier smoke runs used the 72 texts, current formal run did not. "
                "NOT a new clean model-selection benchmark."
            ),
            "thresholds_unchanged": True,
            "models": {},
        }
        for name, values in results.items():
            scores = np.asarray(values)
            predicted = scores.argmax(axis=1)
            directory = (
                run / "baselines" / name
                if name.startswith("tfidf_")
                else run / ("bert" if name == "bert_base_chinese" else "fasttext")
            )
            policy = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))[
                "threshold_policy"
            ]
            accepted = (
                scores.max(axis=1) >= policy["threshold"]
                if policy["enabled"]
                else np.zeros(len(cases), dtype=bool)
            )
            positive = np.asarray([row["expected"] >= 0 for row in cases])
            report["models"][name] = {
                "native_72": classification_metrics(
                    [row["expected"] for row in cases[:72]], predicted[:72]
                ),
                "domain_positive_count": int(positive.sum()),
                "out_of_scope_count": int((~positive).sum()),
                "out_of_scope_above_validation_threshold": int((accepted & ~positive).sum()),
                "threshold_policy": policy,
                "cases": [
                    {
                        **row,
                        "predicted": int(predicted[i]),
                        "above_validation_threshold": bool(accepted[i]),
                    }
                    for i, row in enumerate(cases)
                ],
            }
        with (OUT / "report.json").open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(
            {
                name: {
                    "native_macro_f1": row["native_72"]["macro_f1"],
                    "ood_above_threshold": row["out_of_scope_above_validation_threshold"],
                }
                for name, row in report["models"].items()
            }
        )
        return
    with (OUT / (args.kind + "-scores.json")).open("x", encoding="utf-8") as handle:
        json.dump(results, handle)
    print({"kind": args.kind, "cases": len(cases), "paid_calls": 0})


if __name__ == "__main__":
    main()
