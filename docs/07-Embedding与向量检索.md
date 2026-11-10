# Embedding 与向量检索

入口：`rag/worker.py` 的 models、encode、build、search。BM25不容易匹配“扣了两次钱”和“重复扣款”等改写，因此加入语义表示。模型为官方 BAAI/bge-small-zh-v1.5，固定revision、MIT许可证和文件SHA-256见 `artifacts/pretrained/bge-small-zh-v1.5/source-manifest.json`。只加载本地safetensors，关闭远程代码和隐式下载。

Tokenizer将B条文本变为 input_ids/attention_mask `[B,L]`，当前L≤384 token。编码器输出 `[B,L,512]`，取CLS位置得到 `[B,512]`，再做L2归一化。查询加入官方中文检索指令，文档不加；字符数不等于token数，长文档仍可能截断。

归一化后内积等于余弦相似度。FAISS IndexFlatIP(512)保存N条向量，返回位置通过不可变manifest映射到chunk_id。FlatIP是精确搜索，成本约O(Nd)，适合当前小库；大规模可评估HNSW/IVF，但需另测召回、内存和更新成本。

这里没有重新训练Embedding模型，也没有当前训练损失。检索预训练常用对比学习：`-log exp(sim(q,p+)/τ)/Σ exp(sim(q,p)/τ)`，拉近相关query/passage、拉远负例。温度、负样本和领域覆盖都影响结果；调用encode不等于完成领域训练。

索引写入全新generation，向量文件和manifest记录哈希，成功后才切换数据库active generation。已发布的未来版本也进入索引，查询时由数据库有效期过滤。模型revision变更必须重建；失败不切换，保留文件排查。

运行：启动模型服务，管理员构建索引；`python -m rag.evaluate` 实际查询四路策略。本机已执行512维真实向量/FAISS验证。缺点包括领域外也有最近邻、截断丢条件、300字符/40重叠切块不适合复杂表格。相似度不是事实正确率，必须核对引用与拒答。

追问：为什么按官方模型约定取CLS？何时内积等于余弦？N增长1000倍有何瓶颈？token截断如何影响召回？为什么升级模型不能复用旧索引？
