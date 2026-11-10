# MySQL SQL 与实际执行范围

用户已授权项目隔离实例的迁移与初始化。项目 .mysql-local/data、127.0.0.1:33307 的两个专用库现为0004_ai_review_calls；原MySQL80/3306未改动。新增用途/数据引用/评审结果字段，将AI数据评审与客服生成放在同一预算账本；未删除旧记录。

1. 00-create-databases-review.sql：两个具名新库，无覆盖既有库逻辑。
2. 01-core-schema-review.sql：0001_core的21业务表及版本表、FK/唯一/CHECK/索引，InnoDB、utf8mb4，幂等键大小写敏感。
3. 02-initial-configuration-review.sql：互斥行和四档自然小时SLA初始政策。
4. 03-intelligence-schema-review.sql：0002_intelligence新增知识/索引/草稿/反馈/提醒/分析/调用账本和租约令牌；当前29业务表，另有版本表。
5. 04-review-governance-schema.sql：0003_reviews新增候选来源、条目、只追加复核记录、不可变冻结版本和模型审批5表及索引；当前34业务表加版本表共35表。本机两库与Compose库均已执行。
6. 05-full-schema-current.sql：上一版0003完整离线SQL，保留历史，不向已有库重复执行。
7. 06-full-schema-ai-review.sql：2026-11-10的当前完整离线SQL，含0004，仍是34业务表＋版本表。本轮只对本机33307两库做增量迁移，没有更新Docker/生产实例。

真实模拟数据显式初始化，随机密钥只写私有文件。应用账户来源业务表只读、审计表只追加，其余必要表SELECT/INSERT/UPDATE，无DELETE/DDL。并发测试仅用smartticket_ai_test，保留唯一标记测试行，不清空数据库。

Alembic在线入口只允许smartticket_ai/test，要求进程级SMARTTICKET_ALLOW_MIGRATION等于库名。非空库还必须具有本项目已知revision，拒绝接管任意既有库。静态revision不导入可变ORM；API启动不建表、不初始化账户。

scripts/migrate_local.py核对实例数据目录与端口后才执行增量迁移。不要把本次授权用于3306或其他项目；不提供自动破坏性downgrade。实际恢复脚本将快照导入全新smartticket_ai_restore_时间戳库，比较所有表行数/哈希，未覆盖现有库。

Docker使用另一个独立数据卷和MySQL8.4.11镜像，不能把Windows实例的通过自动算作Docker实机验收。详见最终验收记录。
