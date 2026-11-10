# Docker 部署与恢复

最新审核阶段已迁移0003_reviews，当前35表。审核/审批页面、真实检索、35表新卷恢复和恢复库应用验证通过；发布包governance_data卷由非root API写、worker只读挂载，重启指纹一致。详细操作与边界见[数据复核与模型审批](20-数据复核与模型审批.md)。下方30表记录是此前批次的历史验收。

2026-11-07已完成Docker整栈实机验收：应用与CPU检索镜像成功构建，migrate退出0，API健康，浏览器完整闭环和真实Reranker通过；重启后30表行数/内容哈希及索引文件哈希一致，业务账号最小权限通过。证据见最终验收记录、artifacts/validation/docker-runtime.json与docker-restart-persistence.json。此前DNS、无路由、镜像站TLS超时和引擎500失败日志均保留，不用compose配置检查替代运行验收。

入口为根目录 `Dockerfile`、`.dockerignore`、`compose.yaml` 与 `docker/retrieval.Dockerfile`。Node构建Vue，Python镜像只复制构建结果与必要源码，应用以10001非root用户运行。基础镜像固定tag及实际解析的digest；Docker独立使用已核验的MySQL8.4.11 LTS镜像和新数据卷，不发布3306。本地33307实例仍为8.0.44。Web仅发布127.0.0.1:8012。没有挂Docker socket或复制.env、私有口令、模型缓存进镜像。

先在项目新目录生成私有凭据，不覆盖已有文件：

```powershell
& '.\.conda\python.exe' -X utf8 scripts/prepare_docker.py
docker compose config --quiet
docker compose --profile rag up --build -d
docker compose ps
```

项目名固定smartticket-ai，与其他Compose项目隔离。已有 `.docker-secrets` 时不要重跑凭据生成。Docker演示账号密码在该目录admin_password/demo_password，独立于Windows实例。目录在.gitignore/.dockerignore内，Windows ACL只授予当前用户。

服务顺序：db健康 → migrate显式执行Alembic/初始化/最小权限授权 → api就绪 → worker。迁移服务独享root连接，应用只拿业务账号；审计仅追加、业务来源表只读，无DELETE/DDL权限。重启API不自动建表。生产Swagger关闭，健康与资源权限接口继续可用。

retrieval采用独立CPU PyTorch2.10.0环境，权重从Windows准备的固定官方safetensors目录只读挂载，索引用独立具名卷；不使用Windows CUDA wheel。运行前必须已有 `artifacts/pretrained/bge-small-zh-v1.5` 与 `bge-reranker-base` 及来源清单。未启用rag profile时BM25可用，向量/草稿会明确报告模型服务不可用，不展示假结果。

官方文档参考：[Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)、[依赖启动顺序](https://docs.docker.com/compose/how-tos/startup-order/)。本地Compose secret是受控文件挂载，不能照搬Swarm内存加密secret的安全承诺。共享生产部署还需要TLS反向代理、外部密钥管理、备份保留/告警、容量压测与正式数据审核，这些不是本机演示已验证成果。

网络故障可用 `scripts/prepare_docker_dns.py` 生成项目内临时build hosts覆盖，再以 `-f compose.yaml -f artifacts/docker-build-dns.json` 构建；不修改系统/daemon DNS，不关闭TLS校验。文件记录生成时刻，IP不应长期固化。代理与镜像站故障不通过重置Docker、删除缓存或改其他项目解决。

本机另提供离线构建路径：先npm run build，再用项目解释器运行scripts/download_linux_wheels.py --proxy和同命令加--retrieval，获得带SHA-256清单的Linux wheelhouse，最后docker compose -f compose.yaml -f compose.offline.yaml --profile rag up --build -d。下载仍通过官方PyPI/PyTorch的HTTPS与包哈希校验，容器RUN不再依赖软件包网络。此路径不等于“完全无联网冷安装”，基础镜像和模型资产仍要预先取得。

停止自己的栈可用 `docker compose --profile rag stop`；重新启动可用start/up。不要执行down -v、volume prune或system prune；它们不是普通重启。备份恢复不得覆盖原库。本地33307恢复入口为python -m scripts.backup_restore_local；Docker本轮另行完成了新卷恢复证明。

Docker数据库恢复入口为项目解释器运行scripts.verify_docker_restore，随后运行scripts.verify_restored_application。前者验证精确源卷，暂停worker、备份到私有目录，在全新独立卷和空库导入并比较30表行数/哈希；后者在已验证的恢复卷上重建最小权限账号，用真实恢复MySQL执行应用ASGI就绪/管理员登录/工单查询。两阶段都通过，证据artifacts/validation/docker-volume-restore.json。源卷和恢复卷保留，不覆盖原库；恢复检查不是公网流量切换或整机/全部模型资产丢失演练。

重启持久性检查入口为 `.\.conda\python.exe -X utf8 -m scripts.verify_docker_persistence`。它暂停本项目worker、只重启本项目数据库/API/检索，比较全表行数/内容SHA-256和索引文件SHA-256，再启动worker；会短暂中断8012服务，不用于无维护窗口的生产环境。随后运行scripts.verify_runtime --docker检查模型重载后的真实检索。

若首次初始化中引擎异常中断，只在已确认卷仅含系统表时使用scripts/recover_partial_docker_init.py先检查，再显式--apply。它核对精确项目/卷并拒绝任何已有业务库；其他状态应保留现场诊断，不拿该脚本绕过认证或重置数据库。

验收检查：镜像构建成功、migrate退出0、api healthy、浏览器真实登录、查询/写入权限、LLM保持禁用、重启记录保留、检索真实运行。任何一步未执行或失败都应留下日志并列为未完成。
