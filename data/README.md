# 数据来源和使用边界

当前交付为 `processed/classification-ai-v3.jsonl`，共 1,238 条 AI 复核中文分类样本，固定切分为训练 952、验证 144、测试 142。来源、许可证和生成复核流程见 `licenses/`、`processed/README.md` 及 `artifacts/data-review-v3/`。这些是虚构电商场景的合成适配数据，不是企业真实客户数据或人工金标准。

以下 72 条 smoke 样本为早期工程验证数据，保留以复现历史实验，不能替代上述当前数据集。

`samples/classification-smoke.jsonl` 由 `scripts/create_smoke_dataset.py` 中 72 条独立场景文本生成。全部由 AI 编写，非企业生产数据，标签尚未经过人工复核。每类 12 条只是验证工程链路，不构成正式数据集或准确率成果。

`source_kind=synthetic`、`review_status=unreviewed`，项目内测试使用，对外分发许可由项目所有者决定；不借用任何外部语料的许可证。正式实验会拒绝这些未复核记录。不能为了通过校验批量修改为 approved：必须实际逐条标注审核并记录 reviewer。

所有数据使用六类统一映射，详见 `docs/04-NLP数据处理.md`。已知同源对话、模板、改写共享 group_id；程序会进一步合并完全重复和高词面相似记录。程序没有证明语义无泄漏，样例切分不是正式独立测试集。

当前规模已达到原 1,200–3,000 条范围下限，但人工金标准和真实业务质量认证仍未完成。不从原有十类新闻语料迁移标签或结果。
