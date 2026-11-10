from backend.app.services.assistant import render_draft


def test_invoice_policy_does_not_ask_for_device_fault_and_keeps_conditions():
    policy = "企业电子发票需核对订单、抬头和税号；不得承诺固定开票时间。"
    result = render_draft([("invoice-1", policy)], {"order": None})
    assert policy in result
    assert "故障" not in result and "发生时间" not in result
    assert "[invoice-1]" in result


def test_renderer_keeps_safety_negation_and_only_supplied_business_facts():
    policy = "存在异味应停止使用，不得继续充电，不保证无条件换新。"
    result = render_draft(
        [("safety-1", policy)], {"order": {"order_no": "EXPLICIT-DEMO", "status": "SHIPPED"}}
    )
    assert policy in result and "模拟业务记录：订单 EXPLICIT-DEMO，当前状态 SHIPPED。" in result
    assert "退款成功" not in result
