# SmartTicket AI

企业智能工单分流与 AI 辅助处理参考项目，场景为虚构 3C 电商售后。

2026-11-10本轮交付：1,238条AI复核中文分类数据、四模型同切分重训、原282项候选的AI业务评审及回复模板修正。见[本轮数据与业务评审交付](docs/23-本轮数据与业务评审交付.md)和artifacts/data-review-v3/acceptance.json。生产验收按用户要求暂不推进；代码由AI完成，用户重点理解业务逻辑，独立复刻改为可选。

[整体交付与运行手册](docs/21-整体交付与运行手册.md)保留启动、恢复与原15项阶段A操作说明。11月9日final-acceptance.json属于上一交付版，不能代替本轮证据。

FastAPI + MySQL + Vue 3 / Element Plus：工单、资源权限、规则派单、SLA、知识版本、BM25 / BGE / FAISS / RRF / Reranker、人工审核草稿与实测报表。RF、LR、fastText、中文 BERT 已用1,238条AI复核数据训练并重载验证。**本机百炼LLM启用，总预留58/60次；分类模型保留离线评估及人工确认，没有自动批准生产分流。AI复核不等于人工金标准。**

LLM供应商为阿里云百炼，默认qwen-plus；支持私有SMARTTICKET_LLM_API_KEY或DASHSCOPE_API_KEY。本机开关已打开；新安装示例和Docker默认仍关闭，避免其他实例另开预算。草稿生成后仍须审核、再独立发送。[百炼配置及剩余验收清单](docs/18-百炼接入与剩余验收.md)；[真实调用评测记录](docs/19-真实百炼验收与恢复演练.md)。

管理员“数据与评审”可查看AI/人工身份、结论历史与冻结导出。本机原282条候选已完成AI评审，新分类批次1,573条去重候选中1,238条通过、335条排除。影子/正式审批功能保留；AI数据不能绕过生产人工金标准门槛。见[复核与发布操作说明](docs/20-数据复核与模型审批.md)。

## 本机启动

GitHub 仓库包含源码、前端构建、1,238 条分类数据、数据许可证和选定评测记录；不包含私有配置、账号密码、数据库、Python 环境及模型权重。克隆后需按[环境与实验运行](docs/环境与实验运行.md)配置自己的环境和数据服务；本机路径与验收结果仅对应开发机器，不能视为新机器已完成安装验收。`.env.example` 默认关闭付费调用，密钥只在本地填写。

项目位于 E:\AI_project\SmartTicket-AI，原 E:\AI_project\first_project 保留不变。

    Set-Location 'E:\AI_project\SmartTicket-AI'
    .\scripts\start.cmd

打开 [工作台](http://127.0.0.1:8010)。随机账号密码保存在私有文件 .mysql-local/login-info.txt，不写入前端或文档。管理员 admin，客户 demo_customer_1/2，客服 demo_agent_1/2；客服1有业务查询权限，客服2没有。

启动复用项目 MySQL 33307，并启动 API 8010、模型服务 8011、后台任务。端口冲突时拒绝替换其他进程。scripts/stop.cmd 停止应用服务并保留数据库；项目数据库可通过 .conda/python.exe -m scripts.local_mysql stop 优雅停止，只操作已核验数据目录。原 MySQL80/3306 不受影响。

## 完整演示

1. 客户创建工单，选填本人演示订单 ST202611060101。
2. 管理员在“工单操作与 SLA”填写原因，人工分类并按技能、在线和容量派单；无客服时保留队列原因。
3. 客服只访问自己获派工单，业务查询另校验技能组权限。
4. “生成证据摘录”真实执行混合检索与重排，**不调用大模型**；无充分依据则拒绝生成。
5. 核对政策原文，采纳、修改采纳或拒绝；另点“发送已审核回复”才发给客户。
6. 回复后解决，客户或管理员关闭；七日内可重开，保留旧记录并新建 SLA 周期。

知识库包含不可变内容版本、审核/发布/退役、生效区间和后台索引重建。管理员可管理用户/技能组/规则、查看SLA提醒和实验报表。附件、PDF/OCR、真实支付退款不在实现范围。

## 运行检查

    .\scripts\check.cmd
    .\.conda-bert\python.exe -X utf8 -m pytest -q tests_ml
    .\.conda\python.exe -X utf8 -m scripts.evaluate_reply_guards
    .\.conda\python.exe -X utf8 -m rag.evaluate
    .\scripts\run_models.cmd --device cuda --epochs 1

MySQL 并发用例位于 tests_mysql，严格要求具名隔离测试库，不向已有业务库设置测试连接。浏览器用例位于 frontend/e2e，先启动应用和 Vite，再运行 npm run test:e2e。备份恢复入口 python -m scripts.backup_restore_local，恢复到新建项目专用库，拒绝覆盖。

验收生产构建可直接使用已启动的8010：在frontend目录设置 `$env:SMARTTICKET_E2E_URL='http://127.0.0.1:8010'` 和 `$env:SMARTTICKET_E2E_CHANNEL='chrome'` 后运行 `npm run test:e2e`，无需另开Vite；只有SMARTTICKET_E2E_LLM严格等于1才会真实消耗一次模型额度。默认128项、MySQL并发/事务7项、ML专项5项和两条浏览器流程通过；本轮没有新增付费调用。详细证据见[最终验收记录](docs/最终验收记录.md)，不替代正式数据与独立人工质量评审。

## 数据和验收边界

本节下面的 72 条样本和未复核描述记录初期验收边界；当前 1,238 条数据及原 282 项候选的 AI 复核结果以[本轮交付](docs/23-本轮数据与业务评审交付.md)为准。独立人工金标准仍未完成。

- 分类：72条 AI 编写、未复核合成文本，48/12/12 固定组切分；正式模式拒绝未复核数据，不能宣传泛化准确率。
- 知识：50篇不同场景的虚构政策，演示初始化单独标记未经独立复核，不冒充人工审核。
- 检索：100条可回答、10条应拒答，相关性由AI编写，缺少独立复核。
- 回复：50条离线安全契约另有报告；新增50个真实百炼端到端案例，48个生成草稿、2个拒答，其中1个在检索阶段拦截。另有真实浏览器与对抗案例。样本为AI编写，仍缺独立人工质量评审。
- BERT：本机旧Torch仅作受控safetensors smoke验证；部署依赖与正式质量验收单列。
- 统计：只计算实际模拟业务记录；缺少真值的派单正确率、未知成本和零分母显示暂无，不捏造百分比。

## 环境与部署

基础 .conda、BERT/检索 .conda-bert、fastText .venv-fasttext 分开，避免 NumPy 与原生扩展冲突。新机器重建与精确版本见 [环境与实验运行](docs/环境与实验运行.md)。不使用 WindowsApps Python 占位入口，不修改全局 PATH/执行策略。

Docker 入口 compose.yaml，独立项目名 smartticket-ai，Web 映射 127.0.0.1:8012，数据库不映射外网。密钥以 Compose secrets 注入，迁移为独立显式服务，API 不在启动时建表。详见 [Docker部署](docs/15-Docker部署.md)。

Docker应用/CPU检索镜像、整栈浏览器闭环、真实Reranker和重启持久性已实测。验收后已关闭Docker Desktop恢复任务前状态；日常本地运行不需要Docker。容器、数据卷和原有27个容器均保留。

包含审核与审批代码的新镜像、容器浏览器与真实检索已复验；Docker MySQL已恢复到新数据卷，35表哈希一致，恢复库的最小权限、登录和查询通过；审核发布卷重启持久性、API写入和worker只读也通过。Docker仍默认禁用付费调用，本机Windows已启用。该阶段累计54次；本轮含失败调用预留后为58/60次，以当前数据库记录为准。

## 代码和学习

nlp/ 是数据门槛与训练评估，rag/ 是模型推理排序，backend/app/services/ 是业务规则，frontend/src/components/ 是分模块界面，alembic/versions/ 是静态迁移。实测与失败日志在 artifacts/；历史批次报告不是当前验收状态。

按 [模块学习入口](docs/模块学习入口.md) 阅读原理、输入输出、代码和验证。[实施进度](docs/实施进度.md)、最终验收记录与 [面试技术难点](docs/16-面试技术难点.md) 明确哪些已经验证、哪些不能夸大。参考项目与用户独立复刻阶段隔离；复刻时先审设计和提示，不直接修改用户代码。

新增运行保障：管理员“数据统计”可检查后台心跳、检索、索引、队列和剩余额度；统计SQL已去除按工单/反馈重复查询。5分钟5RPS本机只读负载1500请求全200，P95约32ms；真实停止/恢复worker和检索、独立本机CA的HTTPS证书链/主机名校验均有记录。详见21文档的复跑命令与范围，不称为公网生产容量。

可移交源码包由scripts.package_release按白名单生成，位于artifacts/releases；不含密钥、数据库、环境或下载权重。scripts.verify_release_package将其提取到新临时目录，禁用真实API配置并执行隔离业务测试；包名和SHA-256见artifacts/validation/release-package.json。
