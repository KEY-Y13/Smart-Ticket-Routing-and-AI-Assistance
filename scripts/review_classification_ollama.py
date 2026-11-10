"""Read-only inference on existing local Qwen7B; one schema-constrained result per case."""

import argparse
import asyncio
import json

import httpx

from nlp.data.dataset import canonical_hash
from scripts.local_review_runtime import URL
from scripts.review_classification_local import INSTRUCTION
from scripts.review_data_stage import STAGE

RULES = (
    INSTRUCTION.replace(
        '仅输出JSON：{"label":整数,"translation_ok":布尔值}。不解释、不输出其他字段。', ""
    )
    + """
label只按中文内容判断，不能依据英文替中文补全丢失的语义。若中文不可分类用-1。
translation_ok需要检查全句事实、客户说话角色、否定和限定条件，不仅检查类别。
借记卡不是信用卡、退款不是报销、到货日期不是发货日期、换货不是退货。
把客户请求翻成客服答复，丢失已被盗/未发货等关键信息，或添加退货动作，均为false。
原生中文english=null时只判断中文是否自然完整。
输出JSON：label整数，translation_ok布尔值，reason用15至30个汉字具体说明核对结果。
reason必须先对照英文和中文的动作或事实，不能只说分类正确。先复核翻译，后判中文类别。
例：English=I want a refund，中文=我要退货，应写“原文要退款，译文变退货，处理动作改变”，translation_ok=false。
English=I want a refund，中文=我要退款，则true。
English=Help me close my account，中文=我帮您注销账号，为false，客户与客服角色颠倒。
English=What is the delivery date，中文=什么时候发货，为false，到货不等于发货。
English=Pay by debit card，中文=用信用卡付款，为false，卡种不同。
English=My account was stolen, reset my password，中文=我要重置密码，为false，丢失被盗事实。
"""  # noqa: E501 - frozen prompt bytes
)
SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "translation_ok": {"type": "boolean"},
        "label": {"type": "integer", "enum": [-1, 0, 1, 2, 3, 4, 5]},
    },
    "required": ["label", "translation_ok", "reason"],
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
    calibration = json.loads((STAGE / "calibration-plan.json").read_text(encoding="utf-8"))[
        "batches"
    ]
    first = [case for batch in calibration for case in batch["cases"]]
    first_ids = {case["id"] for case in first}
    cases = first + [case for case in cases if case["id"] not in first_ids]
    assets = json.loads((STAGE / "qwen7b-existing-assets.json").read_text())
    source_hash = canonical_hash({"cases": cases, "instruction": RULES, "model": assets})
    output = STAGE / "local-qwen7b-v2-results.jsonl"
    old = (
        [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        if output.exists()
        else []
    )
    if any(row["source_hash"] != source_hash for row in old) or len(
        {row["id"] for row in old}
    ) != len(old):
        raise RuntimeError("Local source changed or duplicate results")
    previous = {row["id"] for row in old}
    pending = [case for case in cases if case["id"] not in previous]
    if limit:
        pending = pending[:limit]
    slots = asyncio.Semaphore(4)
    options = {"temperature": 0, "num_predict": 128, "num_ctx": 2048, "seed": 20261110}
    async with httpx.AsyncClient(trust_env=False, timeout=300) as client:

        async def review(case):
            async with slots:
                response = await client.post(
                    URL + "/api/chat",
                    json={
                        "model": "qwen2.5:7b",
                        "stream": False,
                        "messages": [
                            {"role": "system", "content": RULES},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {key: case[key] for key in ("english", "chinese")},
                                    ensure_ascii=False,
                                ),
                            },
                        ],
                        "format": SCHEMA,
                        "options": options,
                        "keep_alive": "10m",
                    },
                )
                response.raise_for_status()
                body = response.json()
                raw = body["message"]["content"]
                try:
                    decision = json.loads(raw)
                    if (
                        set(decision) != {"label", "translation_ok", "reason"}
                        or type(decision["label"]) is not int
                        or not -1 <= decision["label"] <= 5
                        or type(decision["translation_ok"]) is not bool
                        or not isinstance(decision["reason"], str)
                        or body.get("done_reason") != "stop"
                    ):
                        raise ValueError("Invalid local result")
                except (ValueError, KeyError, TypeError):
                    decision = None
                return {
                    "id": case["id"],
                    "case": case,
                    "source_hash": source_hash,
                    "model": "qwen2.5:7b",
                    "model_digest": assets["model_digest"],
                    "options": options,
                    "raw_response": raw,
                    "decision": decision,
                    "human_review": False,
                    "paid_calls": 0,
                    "usage": {
                        key: body.get(key)
                        for key in ("prompt_eval_count", "eval_count", "total_duration")
                    },
                }

        tasks = [asyncio.create_task(review(case)) for case in pending]
        completed = len(old)
        with output.open("a", encoding="utf-8") as handle:
            for future in asyncio.as_completed(tasks):
                result = await future
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                completed += 1
                if completed % 16 == 0 or completed == len(cases):
                    print({"reviewed": completed, "total": len(cases), "paid_calls": 0}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    asyncio.run(run(parser.parse_args().limit))
