"""One label contract shared by training, evaluation and API schemas."""

import hashlib
import json
from enum import IntEnum


class Category(IntEnum):
    ORDER = 0
    PAYMENT = 1
    LOGISTICS = 2
    REFUND = 3
    QUALITY = 4
    ACCOUNT = 5


LABEL_NAMES = {
    0: "订单问题",
    1: "支付问题",
    2: "物流问题",
    3: "售后退款",
    4: "商品质量问题",
    5: "账户问题",
}
LABEL_VERSION = "six-primary-v1"


def label_map_hash() -> str:
    payload = json.dumps(LABEL_NAMES, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
