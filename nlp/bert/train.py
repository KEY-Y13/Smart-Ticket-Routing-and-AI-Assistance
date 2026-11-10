import gc
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

import numpy as np
import psutil
import torch
from torch.nn import functional as F
from torch.utils.data import DataLoader
from transformers import BertForSequenceClassification, BertTokenizerFast, DataCollatorWithPadding

from nlp.artifacts import write_json
from nlp.bert.assets import verify_assets
from nlp.evaluation.metrics import classification_metrics
from nlp.experiments import experiment_inputs, runtime_environment, save_experiment
from nlp.labels import LABEL_NAMES


@dataclass(frozen=True)
class BertTrainingConfig:
    epochs: int = 1
    batch_size: int = 2
    accumulation_steps: int = 4
    max_length: int = 128
    learning_rate: float = 2e-5
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    device: str = "cuda"

    def __post_init__(self):
        if self.epochs < 1 or self.batch_size < 1 or self.accumulation_steps < 1:
            raise ValueError("epochs/batch/accumulation 必须为正整数")
        if not 8 <= self.max_length <= 512 or not 0 < self.learning_rate < 1:
            raise ValueError("BERT 长度或学习率无效")
        if self.device not in ("cpu", "cuda"):
            raise ValueError("device 必须明确为 cpu 或 cuda")


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def encode(tokenizer, records, max_length: int) -> list[dict]:
    features = tokenizer([s.text for s in records], truncation=True, max_length=max_length)
    return [
        {**{k: v[i] for k, v in features.items()}, "labels": s.label} for i, s in enumerate(records)
    ]


def evaluate(model, records, tokenizer, config: BertTrainingConfig) -> tuple[np.ndarray, float]:
    model.eval()
    collator = DataCollatorWithPadding(tokenizer=tokenizer)
    loader = DataLoader(
        encode(tokenizer, records, config.max_length),
        batch_size=config.batch_size,
        collate_fn=collator,
    )
    scores, losses = [], []
    with torch.inference_mode():
        for batch in loader:
            labels = batch.pop("labels").to(config.device)
            inputs = {k: v.to(config.device) for k, v in batch.items()}
            logits = model(**inputs).logits.float()
            losses.extend(F.cross_entropy(logits, labels, reduction="none").cpu().tolist())
            scores.append(torch.softmax(logits, dim=-1).cpu().numpy())
    return np.concatenate(scores), float(np.mean(losses))


def train_bert(
    data: Path, manifest_path: Path, assets: Path, output: Path, config: BertTrainingConfig
) -> dict:
    manifest, partitions = experiment_inputs(data, manifest_path)
    source = verify_assets(assets)
    if output.exists():
        raise FileExistsError(f"拒绝覆盖实验目录: {output}")
    if config.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA 不可用；不会静默改用 CPU 或改变训练配置")
    seed_everything(manifest["seed"])
    torch.set_num_threads(2)
    if config.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    tokenizer = BertTokenizerFast.from_pretrained(assets, local_files_only=True)
    model, loading_info = BertForSequenceClassification.from_pretrained(
        assets,
        num_labels=6,
        id2label=LABEL_NAMES,
        label2id={v: k for k, v in LABEL_NAMES.items()},
        local_files_only=True,
        use_safetensors=True,
        output_loading_info=True,
        attn_implementation="eager",
    )
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.to(config.device)
    original = (
        model.bert.encoder.layer[0].attention.self.query.weight[:8, :8].detach().cpu().clone()
    )
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "loading-info.json", loading_info)
    parameters = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(
        parameters, lr=config.learning_rate, weight_decay=config.weight_decay, foreach=False
    )
    scaler = torch.amp.GradScaler("cuda", enabled=config.device == "cuda")
    generator = torch.Generator().manual_seed(manifest["seed"])
    loader = DataLoader(
        encode(tokenizer, partitions["train"], config.max_length),
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
        num_workers=0,
        collate_fn=DataCollatorWithPadding(tokenizer=tokenizer),
    )
    history, best_key, best_epoch, steps = [], None, None, 0
    best_dir = output / "best-model"
    started = perf_counter()
    for epoch in range(config.epochs):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        epoch_loss, examples = 0.0, 0
        for index, batch in enumerate(loader):
            labels = batch.pop("labels").to(config.device)
            inputs = {k: v.to(config.device) for k, v in batch.items()}
            # Correctly scale the final, potentially short accumulation window.
            window_start = (index // config.accumulation_steps) * config.accumulation_steps
            window_examples = min(
                config.accumulation_steps * config.batch_size,
                len(partitions["train"]) - window_start * config.batch_size,
            )
            with torch.autocast(
                device_type=config.device, dtype=torch.float16, enabled=config.device == "cuda"
            ):
                logits = model(**inputs).logits
                loss = F.cross_entropy(logits, labels)
            if not torch.isfinite(loss):
                raise ValueError("BERT loss 非有限数值，停止并保留失败记录")
            scaler.scale(loss * len(labels) / window_examples).backward()
            epoch_loss += float(loss.detach()) * len(labels)
            examples += len(labels)
            if (index + 1) % config.accumulation_steps == 0 or index + 1 == len(loader):
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(parameters, config.max_grad_norm)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                steps += 1
        val_scores, val_loss = evaluate(model, partitions["validation"], tokenizer, config)
        val_metrics = classification_metrics(
            [s.label for s in partitions["validation"]], val_scores.argmax(axis=1)
        )
        key = (val_metrics["macro_f1"], -val_loss)
        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": epoch_loss / examples,
                "validation_loss": val_loss,
                "validation_macro_f1": key[0],
            }
        )
        print(
            f"epoch={epoch + 1} train_loss={epoch_loss / examples:.6f} val_macro_f1={key[0]:.6f}",
            flush=True,
        )
        if best_key is None or key > best_key:
            best_key, best_epoch = key, epoch + 1
            model.save_pretrained(best_dir, safe_serialization=True)
            tokenizer.save_pretrained(best_dir)
    train_ms = (perf_counter() - started) * 1000
    changed = not torch.equal(
        original, model.bert.encoder.layer[0].attention.self.query.weight[:8, :8].detach().cpu()
    )
    if not changed:
        raise RuntimeError("编码器参数未发生变化，不能报告已完成 BERT 微调")
    write_json(output / "training-history.json", history)
    del optimizer, scaler, parameters, model
    gc.collect()
    if config.device == "cuda":
        torch.cuda.empty_cache()
    best = BertForSequenceClassification.from_pretrained(
        best_dir, local_files_only=True, use_safetensors=True, attn_implementation="eager"
    ).to(config.device)
    val_scores, _ = evaluate(best, partitions["validation"], tokenizer, config)
    test_scores, _ = evaluate(best, partitions["test"], tokenizer, config)
    # Full save/reload check against the selected checkpoint, with dropout off.
    expected = test_scores.copy()
    del best
    gc.collect()
    if config.device == "cuda":
        torch.cuda.empty_cache()
    best = BertForSequenceClassification.from_pretrained(
        best_dir, local_files_only=True, use_safetensors=True, attn_implementation="eager"
    ).to(config.device)
    actual, _ = evaluate(best, partitions["test"], tokenizer, config)
    np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=1e-6)
    latency = []
    single_config = BertTrainingConfig(**{**asdict(config), "batch_size": 1})
    evaluate(best, partitions["test"][:1], tokenizer, single_config)
    for _ in range(3):
        for sample in partitions["test"]:
            if config.device == "cuda":
                torch.cuda.synchronize()
            start = perf_counter()
            evaluate(best, [sample], tokenizer, single_config)
            if config.device == "cuda":
                torch.cuda.synchronize()
            latency.append((perf_counter() - start) * 1000)
    memory = psutil.Process().memory_info()
    environment = runtime_environment(["torch", "transformers", "numpy", "psutil", "safetensors"])
    environment.update(
        {
            "measurement_device": config.device,
            "gpu_name": torch.cuda.get_device_name(0) if config.device == "cuda" else None,
            "torch_cuda_runtime": torch.version.cuda,
            "process_rss_bytes": memory.rss,
            "process_peak_working_set_bytes": getattr(memory, "peak_wset", None),
            "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated()
            if config.device == "cuda"
            else None,
            "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved()
            if config.device == "cuda"
            else None,
            "bitwise_reproducibility_guaranteed": False,
        }
    )
    return save_experiment(
        output,
        "bert_base_chinese",
        manifest,
        partitions,
        val_scores,
        test_scores,
        config={
            **asdict(config),
            "seed": manifest["seed"],
            "source": source,
            "selection": "validation Macro-F1, then validation loss; never test",
            "gradient_checkpointing": True,
            "mixed_precision": config.device == "cuda",
            "latency_boundary": (
                "single-item tokenization, transfer, forward, softmax and labels loss; "
                "synchronized GPU, warmed"
            ),
        },
        environment=environment,
        latency_ms=latency,
        train_ms=train_ms,
        model_files=sorted(p for p in best_dir.iterdir() if p.is_file()),
        extra={
            "best_epoch": best_epoch,
            "optimizer_step_attempts": steps,
            "encoder_update_verified": changed,
            "effective_batch_size": config.batch_size * config.accumulation_steps,
            "trainable_parameters": sum(p.numel() for p in best.parameters()),
            "expected_optimizer_step_attempts": config.epochs
            * math.ceil(len(loader) / config.accumulation_steps),
        },
    )
