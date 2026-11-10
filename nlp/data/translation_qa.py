"""Conservative exclusions calibrated before classifier splitting; never relabel text."""

import re


def exclusion(english, chinese):
    if english is None:
        return None
    source = english.lower()
    if re.search(r"refund|reimburse|money.?back", source) and "报销" in chinese:
        return "retail_refund_became_expense_reimbursement"
    if "debit card" in source and "信用卡" in chinese:
        return "debit_became_credit"
    if re.search(r"delivery (time|date)", source) and re.search(
        r"发货(时间|日期)|什么时候发货", chinese
    ):
        return "arrival_became_dispatch"
    if "refund" in source and "退货" in chinese and "退款" not in chinese:
        return "refund_became_return"
    if "exchange" in source and "换" not in chinese:
        return "exchange_lost"
    if re.search(r"check\s?out", source) and not any(
        word in chinese for word in ("付款", "支付", "结账")
    ):
        return "checkout_lost"
    if re.search(r"before (you|ya|u)\s?send|before usend", source) and not re.search(
        r"(发货|出库|发送).*前|未(发货|出库)", chinese
    ):
        return "pre_dispatch_condition_lost"
    if "card" in source and re.search(r"\bpay\b", source) and "卡" not in chinese:
        return "card_method_lost"
    if re.search(r"取消(某个)?产品|不工作正确|购买时.*(错误|问题)", chinese):
        return "unnatural_or_ambiguous_translation"
    if re.search(
        r"我(可以)?(帮您|帮你|为您)|我帮忙|可以帮(您|你)吗|我可以通过.*您|您可以前往哪里", chinese
    ):
        return "speaker_role_changed"
    if re.search(r"do not (need|want)|dont (need|want)|don't (need|want)", source) and re.search(
        r"我(需要|想要)我的|我不同意", chinese
    ):
        return "account_negation_lost"
    if (
        "product" in source
        and "订单问题" in chinese
        and not any(word in chinese for word in ("产品", "商品"))
    ):
        return "product_issue_became_order_issue"
    if re.search(r"\binvoice\b|\bbill\b", source) and not any(
        word in chinese for word in ("发票", "账单")
    ):
        return "invoice_lost"
    if re.search(r"\bpay\b|payment|\bcard\b|\btransfer\b", source) and not any(
        word in chinese for word in ("付款", "支付", "扣款", "转账", "银行卡", "信用卡", "借记卡")
    ):
        return "payment_context_lost"
    if "declined" in source and not any(word in chinese for word in ("拒", "失败", "不能", "无法")):
        return "payment_failure_lost"
    if re.search(r"shipping cost|cost.*shipping|mailing cost|cost.*mailing", source) and not any(
        word in chinese for word in ("费", "价格", "成本")
    ):
        return "shipping_fee_lost"
    if "history" in source and not any(word in chinese for word in ("历史", "记录", "清单")):
        return "purchase_history_lost"
    if re.search(r"stole|hacked|entered my account", source) and not any(
        word in chinese for word in ("盗", "黑客", "侵入", "进入", "入侵")
    ):
        return "account_intrusion_lost"
    if "退货" in chinese and not re.search(r"return|send.*back|exchange|refund|reimburse", source):
        return "return_request_added"
    if re.search(
        r"打开(它|账户|账号)|我的退款政策|退款帮助|换货帮助|用户账户帮助|一个产品我买的", chinese
    ):
        return "unnatural_customer_utterance"
    if "ID" in chinese and "ID" not in english and "identifier" not in source:
        return "account_identifier_added"
    if re.search(r"收到[您你]的订单", chinese) and "receive" not in source:
        return "received_order_fact_added"
    if (
        (
            english.endswith("?")
            or re.search(r"can i|could i|is it possible|would it be possible", source)
        )
        and re.search(r"可以[^？?]*[。.]$", chinese)
        and not any(word in chinese for word in ("哪里", "如何", "怎么", "我想", "我需要"))
    ):
        return "question_became_assertion"
    if "原价" in chinese and "price" not in source:
        return "original_price_fact_added"
    if (
        "validation" in source
        and re.search(r"did.?nt (get|receive)|did not (get|receive)", source)
        and not any(word in chinese for word in ("未收到", "没有收到", "没收到"))
    ):
        return "confirmation_receipt_lost"
    return None
