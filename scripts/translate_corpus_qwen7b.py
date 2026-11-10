"""Translate a fixed balanced source subset with existing local Qwen7B; not an approval."""

import argparse
import asyncio
import json
import random
from collections import defaultdict

import httpx

from nlp.artifacts import write_json
from nlp.data.dataset import canonical_hash
from scripts.local_review_runtime import URL
from scripts.review_data_stage import ROOT, STAGE

OUT = ROOT / "data/candidates/classification-v3-qwen7b"
PROMPT = """Translate a retail CUSTOMER'S English message into natural Simplified Chinese.
Only translate the customer's words; do not answer or offer assistance as the seller.
Preserve facts, negation, requested action, timing and who is speaking. Do not add instructions or promises.
Retail glossary: refund/reimbursement/money back=退款, return/send back=退货, exchange=换货,
invoice/bill=发票, debit card=借记卡, credit card=信用卡, delivery date/time=送达日期/到货时间,
shipping/dispatch=发货, shipping cost=运费, order history=订单历史, close/delete account=注销账户.
Cancelling one item means 取消订单中的某件商品, not necessarily cancelling the entire order.
Examples: 'Help me close my account' -> '请帮我注销账户。'
'Where do I check the delivery time?' -> '在哪里能查到预计到货时间？'
'I bought a defective product, how can I report it?' -> '我买的商品有问题，应该怎么反馈？'
'I want to pay by debit card' -> '我想用借记卡付款。'
Return JSON with only chinese, containing the translation. No explanations or extra text.
"""  # noqa: E501 - frozen prompt bytes


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    lineage = json.loads((STAGE / "lineage.json").read_text(encoding="utf-8"))
    fresh = json.loads((STAGE / "reviewer-fresh-holdout.json").read_text(encoding="utf-8"))["cases"]
    by_english = {row["english"]: row for row in lineage.values()}
    first = [by_english[case["english"]] for case in fresh]
    selected_ids = {row["id"] for row in first}
    pools = defaultdict(list)
    for row in lineage.values():
        if row["id"] not in selected_ids:
            pools[row["proposed_label"]].append(row)
    rng = random.Random(20261114)
    selected = list(first)
    for label in range(6):
        rng.shuffle(pools[label])
        selected.extend(
            pools[label][: max(0, 340 - sum(row["proposed_label"] == label for row in first))]
        )
    rows = [
        {
            key: value
            for key, value in row.items()
            if key
            not in {
                "chinese",
                "translation_model",
                "translation_revision",
                "translation_protocol",
                "review_status",
            }
        }
        for row in selected
    ]
    write_json(
        OUT / "translation-plan.json",
        {
            "rows": rows,
            "source_hash": canonical_hash(rows),
            "protocol": "local-qwen7b-customer-translation-v1",
            "prompt_hash": canonical_hash(PROMPT),
            "first_120": [row["id"] for row in rows[:120]],
            "target_per_class_cap": 340,
            "note": (
                "Balanced independent source rows; not fabricated product-nam"
                "e variants; old translations retained and retired"
            ),
        },
    )
    print({"source_rows": len(rows), "paid_calls": 0})


async def translate(limit):
    plan = json.loads((OUT / "translation-plan.json").read_text(encoding="utf-8"))
    if (
        canonical_hash(PROMPT) != plan["prompt_hash"]
        or canonical_hash(plan["rows"]) != plan["source_hash"]
    ):
        raise RuntimeError("Translation plan changed")
    output = OUT / "translated.jsonl"
    old = (
        [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        if output.exists()
        else []
    )
    if any(row["plan_hash"] != plan["source_hash"] for row in old):
        raise RuntimeError("Mixed translation lineage")
    ids = {row["id"] for row in old}
    pending = [row for row in plan["rows"] if row["id"] not in ids]
    if limit:
        pending = pending[:limit]
    slots = asyncio.Semaphore(4)
    async with httpx.AsyncClient(trust_env=False, timeout=300) as client:

        async def generate(row):
            async with slots:
                response = await client.post(
                    URL + "/api/chat",
                    json={
                        "model": "qwen2.5:7b",
                        "stream": False,
                        "messages": [
                            {"role": "system", "content": PROMPT},
                            {"role": "user", "content": row["english"]},
                        ],
                        "format": {
                            "type": "object",
                            "properties": {"chinese": {"type": "string"}},
                            "required": ["chinese"],
                            "additionalProperties": False,
                        },
                        "options": {"temperature": 0, "num_predict": 128, "num_ctx": 2048},
                        "keep_alive": "10m",
                    },
                )
                response.raise_for_status()
                body = response.json()
                text = json.loads(body["message"]["content"])
                if (
                    body.get("done_reason") != "stop"
                    or set(text) != {"chinese"}
                    or not isinstance(text["chinese"], str)
                ):
                    raise ValueError("Invalid translation result; no automatic retry")
                return {
                    **row,
                    "chinese": text["chinese"],
                    "translation_model": "qwen2.5:7b",
                    "plan_hash": plan["source_hash"],
                    "review_status": "unreviewed",
                    "paid_calls": 0,
                }

        tasks = [asyncio.create_task(generate(row)) for row in pending]
        completed = len(old)
        with output.open("a", encoding="utf-8") as handle:
            for future in asyncio.as_completed(tasks):
                row = await future
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                completed += 1
                if completed % 16 == 0 or completed == len(plan["rows"]):
                    print(
                        {"translated": completed, "total": len(plan["rows"]), "paid_calls": 0},
                        flush=True,
                    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "translate"])
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    else:
        asyncio.run(translate(args.limit))
