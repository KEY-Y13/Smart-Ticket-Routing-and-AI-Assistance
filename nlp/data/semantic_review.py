"""Map the blinded bilingual reviewer's explicit actions to the fixed six business routes."""

import json
import re

from nlp.data.translation_qa import exclusion

ACTION_LABELS = {
    "CANCEL": 0,
    "MODIFY": 0,
    "HISTORY": 0,
    "INVOICE": 0,
    "PAY": 1,
    "TRACK": 2,
    "DELIVERY_TIME": 2,
    "SHIPPING_FEE": 2,
    "REFUND": 3,
    "RETURN": 3,
    "EXCHANGE": 3,
    "QUALITY": 4,
    "ACCOUNT": 5,
    "OTHER": -1,
}


def decide(row):
    case = row["case"]
    if case["english"] is None:
        return {
            "id": row["id"],
            "label": -1,
            "translation_ok": False,
            "quality_exclusion": "native_requires_codex_review",
        }
    # Preserve the exact model output separately, including our earlier conservative normalization.
    raw = json.loads(row["raw_response"])
    source, target = raw["source_action"], raw["target_action"]
    label = ACTION_LABELS[target]
    account_english = bool(re.search(r"account|profile|password", case["english"], re.I))
    account_chinese = bool(re.search(r"账户|账号|密码|个人资料|用户资料", case["chinese"]))
    order_chinese = bool(re.search(r"订单|商品|产品|发票", case["chinese"]))
    if target in {"CANCEL", "MODIFY"} and account_chinese and not order_chinese:
        label = 5
    compatible = source == target or (
        {source, target} <= {"CANCEL", "MODIFY", "ACCOUNT"}
        and account_english
        and account_chinese
        and not order_chinese
    )
    rejected = exclusion(case["english"], case["chinese"])
    good = raw["faithful"] and raw["error_code"] == "NONE" and compatible and not rejected
    return {
        "id": row["id"],
        "label": label,
        "translation_ok": bool(good),
        "semantic_action": target,
        "source_action": source,
        "quality_exclusion": rejected or (None if good else "model_fidelity_or_action_mismatch"),
    }
