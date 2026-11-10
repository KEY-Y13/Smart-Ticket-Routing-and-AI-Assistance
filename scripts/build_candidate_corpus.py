"""Select and translate licensed candidates. NEVER asserts semantic review or training approval."""

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/reference/bitext-retail-v1"
OUT = ROOT / "data/candidates/classification-v2-clean"
INTENTS = {
    0: {"cancel_order", "change_order", "request_invoice", "order_history"},
    1: {"pay", "payment_issue", "payment_methods"},
    2: {"delivery_time", "delivery_issue", "track_delivery", "shipping_costs"},
    3: {
        "request_refund",
        "refund_status",
        "return_product",
        "refund_policy",
        "return_policy",
        "exchange_product",
    },
    4: {"product_issue"},
    5: {"change_account", "close_account", "open_account", "recover_password"},
}


def select_candidates():
    OUT.mkdir(parents=True, exist_ok=True)
    source = json.loads((SOURCE / "source-manifest.json").read_text())
    filename = "bitext-retail-ecommerce-llm-chatbot-training-dataset.csv"
    if (
        hashlib.sha256((SOURCE / filename).read_bytes()).hexdigest()
        != source["files"][filename]["sha256"]
    ):
        raise RuntimeError("Source content changed")
    mapping = {intent: label for label, intents in INTENTS.items() for intent in intents}
    eligible = defaultdict(list)
    rejected = Counter()
    with (SOURCE / filename).open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            intent, text = row["intent"], row["instruction"].strip()
            if intent not in mapping:
                rejected["outside_label_contract"] += 1
                continue
            if set("WZ") & set(row["tags"]) or re.search(r"\b(fuck\w*|shit\w*)\b", text, re.I):
                rejected["offensive_or_typo_source_variant"] += 1
                continue
            if len(text) < 20 or len(text) > 350:
                rejected["unhelpful_length"] += 1
                continue
            if mapping[intent] == 4 and re.search(r"\b(refund|return|exchange)\b", text, re.I):
                rejected["quality_aftercare_ambiguity"] += 1
                continue
            eligible[mapping[intent]].append(
                {
                    "id": f"bitext-{index:05d}",
                    "english": text,
                    "proposed_label": mapping[intent],
                    "source_intent": intent,
                    "source_tags": row["tags"],
                    "source_revision": source["revision"],
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_license": source["license"],
                    "source_kind": source["source_kind"],
                }
            )
    rng = random.Random(20261110)
    selected = []
    for label in range(6):
        # Spread intents rather than letting a larger intent dominate its business category.
        pools = defaultdict(list)
        for row in eligible[label]:
            pools[row["source_intent"]].append(row)
        for pool in pools.values():
            rng.shuffle(pool)
        count = 0
        while any(pools.values()) and count < 550:
            for intent in sorted(pools):
                if pools[intent] and count < 550:
                    selected.append(pools[intent].pop())
                    count += 1
    path = OUT / "english-candidates.jsonl"
    with path.open("x", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = {
        "status": "CANDIDATES_ONLY_NOT_AI_REVIEWED",
        "seed": 20261110,
        "source_eligible": {label: len(rows) for label, rows in eligible.items()},
        "selected": dict(Counter(row["proposed_label"] for row in selected)),
        "rejected": dict(rejected),
        "source_revision": source["revision"],
        "source_license": source["license"],
    }
    (OUT / "selection-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report)


def translate(limit):
    import torch
    from packaging.version import Version
    from transformers import MarianMTModel, MarianTokenizer

    if Version(torch.__version__.split("+")[0]) < Version("2.6"):
        raise RuntimeError(
            "Use the new isolated translation environment, never bypass the weight loader gate"
        )
    torch.set_num_threads(4)
    assets = ROOT / "artifacts/pretrained/opus-mt-en-zh"
    manifest = json.loads((assets / "source-manifest.json").read_text())
    for name, entry in manifest["files"].items():
        if hashlib.sha256((assets / name).read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("Translation asset changed")
    rows = [
        json.loads(line)
        for line in (OUT / "english-candidates.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    output = OUT / "translated-candidates.jsonl"
    previous = (
        {
            row["id"]: row
            for row in (
                json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
            )
        }
        if output.exists()
        else {}
    )
    for row in rows:
        if row["id"] in previous and previous[row["id"]]["source_sha256"] != row["source_sha256"]:
            raise RuntimeError("Candidate changed after translation")
    pending = [row for row in rows if row["id"] not in previous]
    if limit:
        pending = pending[:limit]
    tokenizer = MarianTokenizer.from_pretrained(assets, local_files_only=True)
    model = MarianMTModel.from_pretrained(assets, local_files_only=True, weights_only=True).eval()
    prefix = ">>cmn_Hans<< " if ">>cmn_Hans<<" in tokenizer.get_vocab() else ""
    with output.open("a", encoding="utf-8") as handle:
        for start in range(0, len(pending), 16):
            batch = pending[start : start + 16]
            texts_in = []
            for row in batch:
                text = row["english"]
                # Preserve commercial meaning for the old MT model's overloaded word "order".
                text = re.sub(r"\borders\b", "purchase orders", text, flags=re.I)
                text = re.sub(r"\border\b", "purchase order", text, flags=re.I)
                texts_in.append(prefix + text)
            inputs = tokenizer(
                texts_in,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=128,
            )
            with torch.inference_mode():
                predictions = model.generate(**inputs, max_new_tokens=96, num_beams=4)
            texts = tokenizer.batch_decode(predictions, skip_special_tokens=True)
            for row, chinese in zip(batch, texts, strict=True):
                handle.write(
                    json.dumps(
                        {
                            **row,
                            "chinese": chinese,
                            "translation_model": manifest["model"],
                            "translation_revision": manifest["revision"],
                            "review_status": "unreviewed",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            handle.flush()
            print(
                {"translated": len(previous) + start + len(batch), "selected": len(rows)},
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["select", "translate"])
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    select_candidates() if args.action == "select" else translate(args.limit)
