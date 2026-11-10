# 中文 BERT 微调：代码、实验与理解边界

## 解决的问题与入口

用上下文表示处理工单文本，和 TF-IDF / fastText 在同一固定切分上比较。核心：`nlp/bert/train.py`；资产校验：`nlp/bert/assets.py`；共用评估：`nlp/experiments.py`。实际是否运行完成、测试分数及显存以本次 metrics.json 和实施进度为准，不把“代码已写”当作训练已通过。

## 预训练与本任务

候选 `google-bert/bert-base-chinese`，固定 revision `8f23c25b06e129b6c986331a13d8d025a92cf0ea`。模型卡 Apache-2.0，隐藏维度 768。预训练的掩码语言建模不是工单六分类；加载编码器后新增六分类头，分类头随机初始化属于正常行为，保存 loading-info.json 便于追踪。不是从零预训练 BERT。

仅复制已存在的 safetensors 和 tokenizer 文件到项目新目录，逐个比较复制前后 SHA-256。不读取 pickle 权重，不执行远程模型代码，训练使用 local_files_only=True。当前资产清单记录本地完整性与官方 revision 元数据；不把目录名称当作远端 blob 的独立签名证明。

## 输入、维度与计算

原始文本经 tokenizer 成为 input_ids、attention_mask、token_type_ids，shape 均为 `[B,L]`。动态 padding 补到当前 batch 最长长度；max_length 默认 128，超出截断。编码器输出 `[B,L,768]`，分类模型使用首位置相关的池化表示，分类头输出 `[B,6]` logits。不要混淆 logits、Softmax 分数和真实正确概率。

Self-Attention 中，隐藏表示乘不同矩阵得到 Q/K/V，每头计算 `softmax(QK^T / sqrt(d_k)) V`。attention mask 防止把 padding 当成有效 token。双向上下文有利于区分近似措辞和不同诉求，但并不消除歧义或数据偏差。

## 手写训练循环

1. 仅从固定 train 分区读取样本；shuffle 使用记录的 seed。
2. 前向生成 logits，手动调用交叉熵，不把训练过程藏在一个 API 调用里。
3. 交叉熵为 `-log softmax(logits)[真实标签]`，batch 取均值。
4. 梯度累积按当前窗口的实际样本数加权；最后一个较短 batch 不应获得与完整 batch 相同权重。
5. CUDA 使用 autocast FP16 与 GradScaler；CPU 显式 FP32。反向后先 unscale，再裁剪梯度，optimizer.step、scaler.update、zero_grad。
6. gradient checkpointing 以重新计算换取激活显存降低，不等同于只保存模型文件。
7. 每个 epoch 在验证集 eval + inference_mode；按 Macro-F1，再按验证损失选 checkpoint。测试集只评估选定结果。
8. 检查编码器的实际参数发生变化。仅分类头有输出或 loss 能计算，不足以证明编码器已微调。
9. 保存模型/tokenizer 为 safetensors，独立重载比较预测，保存配置、错误样本、阈值、延迟与资源记录。

默认 batch=2、累积=4，有效 batch 上限为 8；最后不足窗口按实际样本计算。AdamW 默认 lr=2e-5，epoch=1 只为 smoke。不能据小样本分数或测试结果继续调参。随机种子不保证跨 GPU/库版本逐位一致。

## 指标、资源和局限

Macro-F1 平均各类 F1，避免多数类掩盖少数类；同时报告逐类 Precision/Recall/F1、混淆矩阵及全部错误样本。验证集选择接收阈值，没有满足目标的阈值就全部交人工；阈值不提供正确率保证。

CUDA 延迟必须同步，否则只测到了任务提交。当前计时包括 tokenizer、数据转移、前向、softmax 和评估用 loss，不含 HTTP；与 RF/fastText 的设备和边界不同，不能直接作为公平速度榜。工作集峰值是整个进程边界，CUDA allocated/reserved 分别是张量分配与缓存池，不等于显卡总占用。

风险：128 tokens 截断、短文本/模板偏差、少量标签、未校准分数、分布外工单、显存限制。替代方案有 fastText、线性模型、冻结编码器、较小模型；不能仅因 BERT 更复杂就上线。

## 运行和验证

完整编排：项目根目录 `scripts\run_models.cmd --device cuda --epochs 1`。它只运行 smoke，使用新目录，不自动下载或覆盖产物。需要已安装 `.conda-bert`、`.venv-fasttext` 和已校验本地资产。

单独训练使用 `.conda-bert/python.exe -X utf8 -m nlp.cli train-bert --data <JSONL> --manifest <固定清单> --assets <本地资产> --device cuda --epochs 1 --out <新目录>`。正式清单仍受人工复核和来源门槛约束。

`tests_ml/test_bert_training.py` 在 ML 环境执行，使用 tiny 随机模型验证形状、梯度、累积权重和推理确定性；它不是预训练 BERT 的效果实验。基础环境测试不导入可选 Torch 依赖，两个测试范围分别记录。

## 面试追问

- 预训练 MLM 与工单分类的目标有什么不同？哪些权重沿用、哪些新建？
- 为什么先 unscale 再做梯度裁剪？梯度累积最后不足一组如何计算？
- model.eval 与 inference_mode 分别改变什么？
- checkpointing 为什么节省显存却增加计算？
- 为什么验证集选 checkpoint，测试集不能跟着调 epoch？
- Macro-F1 比较高但某类召回为零，如何发现？
- 分数高但类别不在训练分布时，为什么仍可能自信地错误？

依据：https://huggingface.co/docs/transformers/model_doc/bert 、https://huggingface.co/google-bert/bert-base-chinese 、https://pytorch.org/get-started/previous-versions/ 。
