"""Collect the original 15 reference-project criteria, with explicit data and production limits."""

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.provision_local_mysql import ROOT
from scripts.validate import source_fingerprint
from scripts.verify_review_delivery import main as verify_review_stage


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    if verify_review_stage():
        raise RuntimeError("Current environment/review invariants failed")
    folder = ROOT / "artifacts/validation"
    latest = max(folder.glob("*/checks.json"), key=lambda path: path.stat().st_mtime)
    checks = load(latest)
    qa = Path.home() / ".codex/visualizations/smartticket-qa"
    classification = load(ROOT / "artifacts/evaluation/classification-smoke.json")
    retrieval = load(ROOT / "artifacts/evaluation/retrieval-demo.json")
    source_valid = checks.get("source_sha256") == source_fingerprint(ROOT)
    regression = source_valid and all(row["returncode"] == 0 for row in checks["checks"])
    mysql = "7 passed" in (ROOT / "artifacts/final-mysql-tests.log").read_text()
    ml = "5 passed" in (ROOT / "artifacts/final-ml-tests.log").read_text()
    browser = "2 passed" in (ROOT / "artifacts/final-browser.log").read_text()
    model_names = {row["model"] for row in classification["models"]}
    model_comparison = len(model_names) >= 3 and all(
        row["sample_count"] > 0 for row in classification["models"]
    )
    ops_browser = load(qa / "operations-browser.json")["passed"]
    runtime = load(folder / "local-runtime.json")["passed"]
    docker = (
        all(
            load(folder / filename)[key]
            for filename, key in (
                ("final-docker-source.json", "all_source_hashes_match"),
                ("docker-runtime.json", "passed"),
                ("docker-restart-persistence.json", "passed"),
                ("docker-volume-restore.json", "passed"),
            )
        )
        and load(qa / "operations-docker-browser.json")["passed"]
    )
    operational = all(
        load(folder / name)["passed"]
        for name in (
            "sustained-local-load.json",
            "operational-recovery.json",
            "local-https.json",
        )
    )
    packaged = load(folder / "release-package-verification.json")["passed"]
    review = load(folder / "review-delivery.json")
    docs = [
        "01-业务需求与场景.md",
        "02-系统技术架构.md",
        "03-数据库设计.md",
        "16-面试技术难点.md",
        "17-独立复刻指南.md",
    ]
    documents_present = all((ROOT / "docs" / name).stat().st_size > 500 for name in docs)
    rows = [
        (
            1,
            "可运行PC Web",
            browser and ops_browser and runtime,
            ["final-browser.log", "operations-browser.json"],
            "本机Chrome；未宣称所有浏览器/公网验收",
        ),
        (
            2,
            "完整工单闭环",
            browser and regression,
            ["final-browser.log", str(latest.relative_to(ROOT))],
            "真实API+模拟业务，不执行真实资金操作",
        ),
        (
            3,
            "至少三种NLP模型对比",
            model_comparison and ml,
            ["classification-smoke.json", "final-ml-tests.log"],
            "四模型smoke；72条合成样本，12条测试；正式规模与独立标注未完成",
        ),
        (
            4,
            "可复现BERT微调",
            ml and regression,
            ["final-ml-tests.log", "docs/06-BERT原理与微调.md"],
            "固定本机实验环境与safetensors；不是生产模型批准",
        ),
        (
            5,
            "真实混合检索及重排序",
            runtime and len(retrieval["strategies"]) == 4,
            ["local-runtime.json", "retrieval-demo.json"],
            "110条AI编写真值，独立人工相关性未完成",
        ),
        (
            6,
            "可追溯AI草稿",
            browser and regression,
            ["final-browser.log", "bailian-live-20261107T012904456529Z/report.json"],
            "已有真实百炼案例；本轮不新增付费，语义质量仍需独立评审",
        ),
        (
            7,
            "模拟业务查询",
            browser and mysql and review["checks"]["local_review_grants"],
            ["final-browser.log", "final-mysql-tests.log"],
            "授权查询及来源表只读，无真实退款",
        ),
        (
            8,
            "人工反馈机制",
            browser and regression,
            ["final-browser.log", "review-delivery.json"],
            "草稿审核与发送分开；数据复核原始记录仍全部待审核",
        ),
        (
            9,
            "权限错误处理日志",
            regression and mysql,
            [str(latest.relative_to(ROOT)), "final-mysql-tests.log"],
            "角色+归属+二次授权，不能保证覆盖所有攻击",
        ),
        (
            10,
            "自动化测试及记录",
            regression and mysql and ml and operational and browser,
            [str(latest.relative_to(ROOT)), "operational-recovery.json"],
            "本机/隔离库范围，未用单测冒充生产验收",
        ),
        (
            11,
            "Docker部署说明",
            docker and (ROOT / "docs/15-Docker部署.md").is_file(),
            ["final-docker-source.json", "docker-volume-restore.json"],
            "独立项目/卷，恢复演练不是整机丢失或公网切流",
        ),
        (
            12,
            "架构与数据库文档",
            documents_present and (ROOT / "docs/api/reference-openapi.json").is_file(),
            docs[:3],
            "当前35表/0003，保留历史设计并明确区分",
        ),
        (
            13,
            "有依据性能业务指标",
            operational and regression,
            ["sustained-local-load.json", "local-https.json"],
            "本机5分钟5RPS；业务指标只取实际模拟记录，不是生产收益",
        ),
        (
            14,
            "面试讲解材料",
            documents_present and packaged,
            ["docs/16-面试技术难点.md", "release-package-verification.json"],
            "明确模型、数据、并发及部署的实际边界",
        ),
        (
            15,
            "独立复刻指南",
            documents_present,
            ["docs/17-独立复刻指南.md"],
            "指南已交付，用户独立复刻能力尚未验收，未代写阶段B",
        ),
    ]
    criteria = [
        {
            "id": i,
            "criterion": title,
            "reference_status": "PASS" if passed else "FAIL",
            "evidence": evidence,
            "limit": limit,
        }
        for i, title, passed, evidence, limit in rows
    ]
    report = {
        "at": datetime.now(UTC).isoformat(),
        "scope": "ORIGINAL_STAGE_A_REFERENCE_IMPLEMENTATION; NOT_PRODUCTION_EFFECT_OR_HUMAN_REVIEW",
        "criteria": criteria,
        "passed": all(row[2] for row in rows),
        "current_source_sha256": source_fingerprint(ROOT),
        "source_matches_latest_tests": source_valid,
        "archive": load(folder / "release-package.json")["archive"],
        "outstanding_external_conditions": [
            "正式分类数据达到1200–3000条参考目标并独立复核；当前按原需求允许的缩小训练范围方案验收",
            "政策适用性/检索真值/真实回复的独立人工质量及实际百炼账单核对",
            "公网或企业生产目标环境的域名、正式证书、容量/SLO和运维责任验收",
            "用户本人按隔离指南完成阶段B学习复刻",
        ],
        "paid_budget": review["paid_budget"],
        "formal_classifier_deployed": False,
    }
    target = folder / "final-acceptance.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        {
            "report": str(target),
            "passed": report["passed"],
            "criteria_passed": sum(row[2] for row in rows),
            "total": 15,
        }
    )
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
