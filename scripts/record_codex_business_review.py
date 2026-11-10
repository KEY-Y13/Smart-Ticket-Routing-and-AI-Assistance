"""Materialize Codex's actual 2026-11-10 item-by-item assessment. NOT a human certification."""

import json

from sqlalchemy import select

from backend.app.db.models import ReviewCollection, User
from backend.app.schemas.reviews import Freeze
from backend.app.services import ai_review_import, reviews
from backend.app.services.assistant import render_draft
from nlp.artifacts import write_json
from nlp.data.dataset import canonical_hash
from scripts.review_data_stage import ROOT, STAGE, Database, Settings

# These reasons were written after reading all fifty policies and all 110 blind queries.
REASONS = [
    "区分未出库改址与已出库拦截，不保证拦截结果。",
    "先核验归属和仓储状态，申请不等于取消完成。",
    "逐包裹核对商品与轨迹，不将部分签收说成整单完成。",
    "核对订单、抬头及企业税号，不虚构开票号码或时限。",
    "价格与活动条件交人工核验，不自动承诺退差价。",
    "按是否出库区分处理方式，保留原订单商品快照。",
    "格式正确与订单存在分开，不存在或越权时不猜状态。",
    "核对脱敏支付记录，截图不能单独证明重复支付。",
    "处理中不等于成功或失败，不诱导反复付款。",
    "核对错误、时间和渠道，扣款争议转人工且不索验证码。",
    "按活动条件查优惠，不擅自补券或改金额。",
    "关联订单与支付流水，只展示事实，不伪造已支付状态。",
    "明确禁止索要密码、完整银行卡号及验证码，保留最少凭证。",
    "轨迹停滞不等于丢件，不捏造送达日期。",
    "核验签收和授权代收，不指责客户或直接判丢件。",
    "保存包装与开箱证据，不要求受损电器强行通电。",
    "拦截申请和承运确认分开，不提前声称退仓。",
    "核对实物、订单与脱敏面单，不先付未知费用再保证赔偿。",
    "少件先排除拆单，证据不足不自动承诺补发赔付。",
    "运费依约定、售后原因和收费凭证人工核定，不保证报销。",
    "退款须本人订单和凭证，申请、审批、到账不混淆。",
    "审核中、退款中、完成分开，业务完成不等于银行到账。",
    "只使用审核通过的售后地址与编号，未知短信不是依据。",
    "据实记录配件和包装，不自动认定扣款。",
    "退仓签收与质检通过分开，不编造退款日期。",
    "部分退款结合明细、实付及既有退款，由人工决定金额。",
    "拒绝结果可申诉，保留现状，不越权改审核结果。",
    "撤销取决于阶段，已执行资金不保证逆转，留言不改状态。",
    "有异味、鼓包等风险即停止使用，不指导拆机或继续充电。",
    "鼓包停止使用，禁止穿刺拆解及未经确认的普通快递寄送。",
    "发热伴异味变形需停止使用并人工核查，不一概称正常。",
    "屏幕故障收集安全可得信息，不拆机或保证保修。",
    "耳机排查限可逆操作，持续异常人工检测，不先认定质量缺陷。",
    "重置前备份配置，不索WiFi密码，不把网络问题直接当硬件损坏。",
    "进液停止通电，不烘烤拆电池或反复开机，保修人工判定。",
    "备份以安全可操作为前提，不强求通电，不接收账户密码。",
    "型号、凭证、序列号共同核验，不凭口述判断期限费用。",
    "期限依具体商品政策和购买日期，缺信息转人工，无虚构通用年限。",
    "寄修登记不等于维修完成，进度只引用实际记录。",
    "换新须政策和检测，不保证无条件换新或具体库存。",
    "登录恢复走受控核验，不在客服聊天代输验证码。",
    "换号需身份核验，原号失效转人工，不绕过验证。",
    "密码由客户在受控入口重置，不接收明文密码或设固定初始密码。",
    "被盗转安全人工，未执行不能声称已冻结，不泄露他人信息。",
    "客户归属与客服业务组权限同时满足，不能仅凭工单分配放行。",
    "注销核验身份和未结交易，不删业务审计或承诺立即抹除。",
    "多诉求确定主次且保留次要诉求，不把路由当业务处理结果。",
    "只收必要信息，缺事实不猜状态，不索无关身份或密码。",
    "安全、盗用、金额与赔偿升级人工，不自动执行资金操作。",
    "重开保留旧记录并重建SLA周期；具体时间窗口仍须系统规则。",
]

EXTRA_RELEVANCE = {
    5: ["logistics-shortage"],
    19: ["payment-security"],
    37: ["order-split"],
    38: ["order-split"],
    66: ["warranty-replacement"],
    74: ["warranty-period"],
}


def main():
    plan = json.loads((STAGE / "plan.json").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "data/knowledge/catalog.json").read_text(encoding="utf-8"))[
        "items"
    ]
    cases = [
        case for batch in plan["batches"] if batch["kind"] == "BUSINESS" for case in batch["cases"]
    ]
    decisions, replays = [], []
    for case in cases:
        original = case["original"]
        if case["kind"] == "KNOWLEDGE":
            index = next(i for i, row in enumerate(catalog) if row[0] == original["id"])
            annotation = {"verdict": "DEMO_VALID"}
            reason = REASONS[index] + "仅认定虚构项目内程序性规则一致，非法律或企业政策认证。"
        elif case["kind"] == "RETRIEVAL":
            index = int(original["id"].split("-")[1])
            relevant = (
                [catalog[(index - 1) // 2][0], *EXTRA_RELEVANCE.get(index, [])]
                if index <= 100
                else []
            )
            annotation = {"answerable": bool(relevant), "relevant_document_keys": relevant}
            reason = (
                "问题主诉由"
                + "、".join(relevant)
                + "直接给出处理条件或禁止事项；无需虚构业务状态。"
                if relevant
                else "超出六类客服政策或要求越权数据/操作；当前知识不能提供该事实或执行结果。"
            )
        else:
            index = int(original["id"].split("-")[1])
            if original["refused"]:
                annotation = {"verdict": "INCORRECT", "refusal_correct": False}
                reason = (
                    "quality-data明确说明安全备份与退出账户，当前查询可答；检索门槛造成漏答。"
                    if index == 36
                    else (
                        "process-missing明确说明最少补充信息与禁止猜测，"
                        "当前查询可答；检索门槛造成漏答。"
                    )
                )
            else:
                evidence = {str(row["chunk_id"]): row["text"] for row in original["evidence"]}
                quotes = original["raw_output"]["quotes"]
                if any(
                    q["chunk_id"] not in evidence or q["quote"] not in evidence[q["chunk_id"]]
                    for q in quotes
                ):
                    raise RuntimeError("Quote evidence changed; assessment cannot be reused")
                suitable = 29 <= index <= 35
                annotation = {
                    "verdict": "ACCEPTABLE" if suitable else "INCORRECT",
                    "refusal_correct": None,
                }
                reason = (
                    "故障/安全主诉的完整引用保留停止使用、禁止拆机与人工确认条件，没有越权承诺。"
                    if suitable
                    else (
                        "政策引用与主诉相符且未越权，但模板无条件追问故障现象与时间，"
                        "偏离当前业务问题；需修改后使用。"
                    )
                )
                citations = [
                    (key, evidence[key]) for key in dict.fromkeys(q["chunk_id"] for q in quotes)
                ]
                replays.append(
                    {
                        "id": original["id"],
                        "question": original["question"],
                        "historical_output_sha256": canonical_hash(original["output"]),
                        "body": render_draft(citations, {"order": None}),
                        "method": "OFFLINE_RENDERER_REPLAY_OF_EXISTING_EVIDENCE_NOT_NEW_LLM_CALL",
                        "codex_assessment": "ACCEPTABLE_AS_REVIEW_REQUIRED_EVIDENCE_DRAFT",
                    }
                )
        decisions.append({"id": case["id"], "annotation": annotation, "reason": reason})
    report = STAGE / "codex-business-review.json"
    if not report.exists():
        write_json(
            report,
            {
                "method": "AI_CODEX_BUSINESS_REVIEW",
                "reviewer_model": "Codex",
                "basis": (
                    "Actual item-by-item reading of 50 policies, 110 blind questions "
                    "and 50 historical outputs in this session; "
                    "not an independent organization or human certification"
                ),
                "baseline_hash": canonical_hash(catalog),
                "review": {
                    "kind": "BUSINESS",
                    "input": cases,
                    "decisions": decisions,
                    "protocol": "fictional-ai-review-v1",
                    "human_review": False,
                },
            },
        )
        write_json(
            STAGE / "reply-renderer-v2-replay.json",
            {
                "new_paid_calls": 0,
                "results": replays,
                "unresolved_refusals": ["live-36", "live-48"],
                "historical_results_not_overwritten": True,
            },
        )
    settings = Settings(_env_file=ROOT / ".env")
    db = Database(settings)
    try:
        with db.sessions() as session:
            actor = session.scalar(select(User).where(User.login == "admin"))
            print(ai_review_import.apply_offline(session, actor, settings, report))
            for code in ("reference-knowledge-v1", "reference-retrieval-v1", "reference-llm-v1"):
                collection = session.scalar(
                    select(ReviewCollection).where(ReviewCollection.code == code)
                )
                frozen = reviews.freeze(
                    session,
                    actor,
                    collection.id,
                    Freeze(
                        reason="Codex逐项AI业务复核，含不合格与漏答结论；非人工认证",
                        acknowledged=True,
                    ),
                    "codex-business-review",
                )
                target = STAGE / (code + "-snapshot.json")
                if not target.exists():
                    write_json(target, frozen.model_dump(mode="json"))
                print(frozen.model_dump(mode="json"))
    finally:
        db.dispose()


if __name__ == "__main__":
    main()
