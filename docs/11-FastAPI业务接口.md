# 认证、工单与智能辅助 API

## 边界与入口

`backend/app/main.py` 组装应用、错误和健康检查；`api/dependencies.py` 提供请求级 Session 与真实登录用户；`api/routes.py` 负责 HTTP/Schema；`services/` 负责事务与权限；`db/models.py` 为 21 张核心业务表。

当前已在项目专用MySQL迁移、初始化并执行真实并发场景；Vue生产构建完成工单和证据审核闭环。知识/草稿/SLA/统计接口在api/intelligence.py，当前29业务表、50个HTTP操作，完整契约见docs/api/reference-openapi.json。分类模型部署仍受正式数据审批约束，付费LLM按用户要求禁用。

## 认证与安全

- 管理员通过受控接口创建客户/客服；无公开注册选角色入口。
- 密码用 pwdlib/Argon2 哈希；响应模型不输出密码、hash、auth_version 等内部字段。
- POST `/api/v1/auth/login` 接收 login/password JSON，返回 Bearer JWT、有效期和公开用户信息。错误统一，不区分用户名不存在/密码错误。
- JWT 限制 HS256、issuer、audience、exp/iat/nbf/sub/ver/jti；密钥需独立配置且至少32字符。
- 每次请求从数据库取用户实际角色、enabled 和 auth_version，不信任 Token 里自称的角色。
- POST `/auth/logout` 撤销该账户所有现有 Token；禁用/改角色也递增 auth_version。忙碌客服要先重新分配活跃工单。
- 登录限制是单 API 进程、每来源10次/60秒，来源记录数有上限；不是分布式限流验收。
- 日志只记录请求 ID、方法、状态与异常类别，不记录请求体、Token、连接口令或完整客户文本。

本期没有刷新令牌、忘记密码、SSO 或 MFA，不宣称具备这些能力。真实线上部署还需 HTTPS 和应用数据库最小权限配置。

## 主要契约

完整 Schema 位于 `schemas/business.py`；离线 OpenAPI 为 `docs/api/core-openapi.json`。ID 输出为字符串，避免 JavaScript 大整数精度问题；priority 数值0..3分别对应P0..P3。

| 接口 | 关键要求 |
|---|---|
| POST /tickets | 客户身份；subject/body/order_no可选；必须 Idempotency-Key |
| GET /tickets | 角色过滤在数据库查询中先执行，再分页；page_size≤100；字符串搜索参数化 |
| GET /tickets/{id} | 自己的客户工单或当前指派客服，管理员可见 |
| PATCH /tickets/{id} | expected_version、reason；客服可改标题，优先级仅管理员 |
| POST /tickets/{id}/transitions | expected_version、target_status、reason；显式状态机 |
| POST /tickets/{id}/classification-review | 客服授权工单或管理员；人工记录独立审计，不改 AI 原结果 |
| POST /tickets/{id}/assign | 管理员；expected_version；可指定合格客服或按规则选择 |
| POST /admin/dispatch-next | 管理员触发；按优先级与排队顺序选已分类工单；空队列明确返回 |
| POST /tickets/{id}/messages | expected_version、PUBLIC/INTERNAL、幂等键；客户不能发内部备注 |
| GET /tickets/{id}/messages | 游标与上限；客户响应完全过滤内部消息 |
| GET /tickets/{id}/events | 客户只看脱敏进度；内部分类/派单/查询审计不给客户 |
| GET /tickets/{id}/sla | 当前周期开始/截止/首次响应/解决时间 |
| GET /tickets/{id}/business-context | 工单权限、订单归属、客服技能组查询权限三层检查 |
| POST /admin/users、PATCH /admin/users/{id} | 管理员；用户停用/改角色有版本与 Token 撤销 |
| POST /admin/groups、/groups/{id}/members、/routing-rules | 管理员配置技能和派单规则，保存管理审计 |
| PATCH /agents/{id}/availability | 客服只能改自己在线状态；容量仅管理员且不能低于活跃负载 |

创建工单前不调用AI。工单、首条消息、SLA、事件和分类任务同一事务提交；持久化分类worker已接入，包含租约/令牌和过期输入检查。没有正式批准模型时实际执行人工兜底，不能把任务完成说成已有预测类别。受管模型加载前及派单前检查审批；影子包不自动分流。

## 错误与幂等

统一 `{error:{code,message,request_id}}`。401登录问题，403操作角色不允许，404资源不存在/不可访问，409版本/状态/约束/键冲突，422输入，429限流，503数据库或配置未就绪。

创建和消息的 Idempotency-Key 8..128个ASCII字母数字/下划线/短横线，区分大小写。同人同操作同键同有效输入重放第一次结果，改内容则409；过期键不自动重用。消息重放前仍重新检查当前权限，防止已撤销指派的旧客服重放。

所有写入显式 commit；失败 rollback 不留下半份事件或负载。ORM version_id_col 保护遗漏的陈旧写入；业务接口同时要求 expected_version。不能对关键实体使用绕过版本条件的批量更新。

## 受控业务数据

查询只返回 ORM 实际记录，缺少订单404、数据库失败503，不补造事实。返回 source=SIMULATED_BUSINESS_DATABASE、查询时间、订单/商品/支付/物流/退款的允许字段；不输出真实支付凭据。没有执行退款、支付修改、任意 SQL 或模型数据库工具接口。

模拟导入入口scripts/seed_simulation.py默认只显示计划，--apply需具名目标；不使用默认口令或覆盖已有账号。项目33307及Compose独立库已显式初始化虚构数据，原3306未执行导入。新增运行状态只允许管理员，返回心跳/队列/额度及有范围标识的进程计数，不返回原始异常内容或密钥。

## 单独运行与验证

安装 `requirements-backend-win.lock` 到项目 .conda；`scripts/check.cmd` 跑本轮测试。当前未配置真实业务库，`scripts/start_api.cmd` 启动后健康 liveness=200、readiness=503；根目录 .env 若存在仅由启动子进程加载。

待授权迁移和初始化后，Swagger 位于 http://127.0.0.1:8010/docs。先登录，再在 Authorize 输入 Bearer token；创建需填写幂等键，后续修改使用响应 version。进程运行、接口已写、MySQL 已通过是三个不同状态。

面试追问：为什么 RBAC 还要检查资源归属？JWT 为何仍查询数据库？为什么不能把密码或响应快照直接打日志？幂等与乐观锁分别解决什么问题？为什么服务启动不自动建表？
