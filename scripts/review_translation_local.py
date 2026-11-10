"""Focused bilingual equivalence review, separate from Chinese intent classification."""

import argparse
import asyncio
import json

import httpx

from nlp.data.dataset import canonical_hash
from scripts.local_review_runtime import URL
from scripts.review_data_stage import STAGE

PROMPT = """Evaluate an English-to-Chinese translation of a RETAIL CUSTOMER'S MESSAGE.
Do NOT answer the customer. Do NOT classify a ticket. Compare only the two provided texts.
First identify the customer's action in English and in Chinese separately. Then judge fidelity.
Accept natural paraphrases and normal colloquial Chinese. Do not invent differences that aren't in the texts.
Require the same request/action, facts, negation, speaker role and material conditions.
Refund is 退款, not 退货 or 报销. Return is 退货. Exchange is 换货. Delivery date is 到货日期, not 发货日期.
English 'I want to delete my account, how?' / Chinese '我想删除账户，怎么操作？' => ACCOUNT/ACCOUNT,true,NONE.
English 'My product is defective, how can I report it?' / Chinese '产品有问题，该怎样反馈？' => QUALITY/QUALITY,true,NONE.
English 'I want a refund' / Chinese '我想申请退款' => REFUND/REFUND,true,NONE.
English 'I need help tracking my delivery' / Chinese '我需要帮忙查包裹位置' => TRACK/TRACK,true,NONE.
English 'Help me close my account' / Chinese '我帮您关闭账户' => ACCOUNT/ACCOUNT,false,ROLE.
English 'I want a refund' / Chinese '我要退货' => REFUND/RETURN,false,MEANING.
English 'Pay by debit card' / Chinese '用信用卡付款' => PAY/PAY,false,FACT.
English 'My account was stolen, help reset my password' / Chinese '帮我重置密码' => ACCOUNT/ACCOUNT,false,FACT.
English 'Where is my package' / Chinese '我想退货' => TRACK/RETURN,false,MEANING.
Give source_action, target_action, faithful (boolean), and error_code. Use NONE only for faithful translations.
Actions: CANCEL, MODIFY, HISTORY, INVOICE, PAY, REFUND, RETURN, EXCHANGE, TRACK, DELIVERY_TIME,
SHIPPING_FEE, ACCOUNT, QUALITY, OTHER. Classify a refund progress query as REFUND, not TRACK.
"""  # noqa: E501 - frozen prompt bytes
ACTIONS = [
    "CANCEL",
    "MODIFY",
    "HISTORY",
    "INVOICE",
    "PAY",
    "REFUND",
    "RETURN",
    "EXCHANGE",
    "TRACK",
    "DELIVERY_TIME",
    "SHIPPING_FEE",
    "ACCOUNT",
    "QUALITY",
    "OTHER",
]
SCHEMA = {
    "type": "object",
    "properties": {
        "source_action": {"type": "string", "enum": ACTIONS},
        "target_action": {"type": "string", "enum": ACTIONS},
        "faithful": {"type": "boolean"},
        "error_code": {"type": "string", "enum": ["NONE", "MEANING", "FACT", "ROLE", "FLUENCY"]},
    },
    "required": ["source_action", "target_action", "faithful", "error_code"],
    "additionalProperties": False,
}


async def run(limit):
    plan = json.loads((STAGE / "plan.json").read_text(encoding="utf-8"))
    cases = [
        case
        for batch in plan["batches"]
        if batch["kind"] == "CLASSIFICATION"
        for case in batch["cases"]
    ]
    calibration = json.loads((STAGE / "calibration-plan.json").read_text(encoding="utf-8"))
    first = calibration["batches"][0]["cases"]
    fresh = json.loads((STAGE / "reviewer-fresh-holdout.json").read_text(encoding="utf-8"))["cases"]
    first_ids = {case["id"] for case in first + fresh}
    cases = first + fresh + [case for case in cases if case["id"] not in first_ids]
    assets = json.loads((STAGE / "qwen7b-existing-assets.json").read_text())
    source_hash = canonical_hash(
        {"cases": cases, "prompt": PROMPT, "schema": SCHEMA, "assets": assets}
    )
    output = STAGE / "translation-qwen7b-v3-results.jsonl"
    old = (
        [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        if output.exists()
        else []
    )
    if any(row["source_hash"] != source_hash for row in old) or len(
        {row["id"] for row in old}
    ) != len(old):
        raise RuntimeError("Input changed or duplicate local review")
    ids = {row["id"] for row in old}
    pending = [case for case in cases if case["id"] not in ids]
    if limit:
        pending = pending[:limit]
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(trust_env=False, timeout=300) as client:

        async def evaluate(case):
            async with semaphore:
                if case["english"] is None:
                    body = {"message": {"content": "null"}, "done_reason": "stop"}
                    decision = {
                        "source_action": "OTHER",
                        "target_action": "OTHER",
                        "faithful": True,
                        "error_code": "NONE",
                    }
                else:
                    response = await client.post(
                        URL + "/api/chat",
                        json={
                            "model": "qwen2.5:7b",
                            "stream": False,
                            "messages": [
                                {"role": "system", "content": PROMPT},
                                {
                                    "role": "user",
                                    "content": json.dumps(
                                        {key: case[key] for key in ("english", "chinese")},
                                        ensure_ascii=False,
                                    ),
                                },
                            ],
                            "format": SCHEMA,
                            "options": {"temperature": 0, "num_predict": 128, "num_ctx": 2048},
                            "keep_alive": "10m",
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                    decision = json.loads(body["message"]["content"])
                    if (
                        set(decision) != set(SCHEMA["properties"])
                        or body.get("done_reason") != "stop"
                        or type(decision["faithful"]) is not bool
                        or decision["source_action"] not in ACTIONS
                        or decision["target_action"] not in ACTIONS
                    ):
                        raise ValueError("Invalid translation review")
                    if (
                        decision["source_action"] != decision["target_action"]
                        or decision["error_code"] != "NONE"
                    ):
                        decision["faithful"] = False
                return {
                    "id": case["id"],
                    "case": case,
                    "source_hash": source_hash,
                    "model": "qwen2.5:7b",
                    "model_digest": assets["model_digest"],
                    "decision": decision,
                    "raw_response": body["message"]["content"],
                    "human_review": False,
                    "paid_calls": 0,
                }

        tasks = [asyncio.create_task(evaluate(case)) for case in pending]
        completed = len(old)
        with output.open("a", encoding="utf-8") as handle:
            for future in asyncio.as_completed(tasks):
                row = await future
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                completed += 1
                if completed % 16 == 0 or completed == len(cases):
                    print({"reviewed": completed, "total": len(cases), "paid_calls": 0}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    asyncio.run(run(parser.parse_args().limit))
