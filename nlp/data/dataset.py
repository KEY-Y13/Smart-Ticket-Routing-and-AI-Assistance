import hashlib
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from nlp.data.schema import TicketSample
from nlp.labels import LABEL_NAMES, LABEL_VERSION, label_map_hash

Purpose = Literal["smoke", "formal"]
SPLIT_NAMES = ("train", "validation", "test")


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_samples(path: Path) -> list[TicketSample]:
    samples = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            samples.append(TicketSample.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"{path.name}:{line_no}: 无效样本: {exc}") from exc
    if not samples:
        raise ValueError("数据集为空")
    if len({s.sample_id for s in samples}) != len(samples):
        raise ValueError("sample_id 重复")
    return sorted(samples, key=lambda s: s.sample_id)


def normalize_for_leakage(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    # Mask long IDs but keep short semantic numbers such as a 7-day return window.
    text = re.sub(r"[a-z]*\d[a-z\d-]{5,}", "编号", text)
    return "".join(c for c in text if c.isalnum())


def build_groups(
    samples: list[TicketSample], similarity_threshold: float = 0.82
) -> tuple[dict[str, str], dict]:
    """Conservative lexical grouping; semantic/source review is still required."""
    if not 0 < similarity_threshold <= 1:
        raise ValueError("相似度阈值必须位于 (0,1]")
    parents = list(range(len(samples)))

    def root(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = root(i), root(j)
        if a != b:
            parents[max(a, b)] = min(a, b)

    normalized = [normalize_for_leakage(s.text) for s in samples]
    if any(not text for text in normalized):
        raise ValueError("存在归一化后为空的文本")
    grams = [{text[i : i + 2] for i in range(max(1, len(text) - 1))} for text in normalized]
    declared: dict[str, int] = {}
    duplicate_count = 0
    similar_count = 0
    for i, sample in enumerate(samples):
        if sample.group_id in declared:
            union(i, declared[sample.group_id])
        declared[sample.group_id] = i
        for j in range(i):
            if normalized[i] == normalized[j]:
                duplicate_count += 1
                union(i, j)
                continue
            a, b = grams[i], grams[j]
            if min(len(a), len(b)) / max(len(a), len(b)) < similarity_threshold:
                continue
            if len(a & b) / len(a | b) >= similarity_threshold:
                similar_count += 1
                union(i, j)

    members: dict[int, list[TicketSample]] = defaultdict(list)
    for i, sample in enumerate(samples):
        members[root(i)].append(sample)
    for group in members.values():
        if len({s.label for s in group}) != 1:
            ids = ", ".join(s.sample_id for s in group)
            raise ValueError(f"同源/相似组标签冲突，须人工复核: {ids}")
    mapping = {
        sample.sample_id: min(s.sample_id for s in group)
        for group in members.values()
        for sample in group
    }
    return mapping, {
        "group_count": len(members),
        "exact_duplicate_pairs": duplicate_count,
        "near_duplicate_pairs": similar_count,
        "similarity_threshold": similarity_threshold,
        "semantic_leakage_review": "not_automatically_proven",
    }


def validate_purpose(samples: list[TicketSample], purpose: Purpose) -> None:
    if purpose not in ("smoke", "formal"):
        raise ValueError("未知数据用途")
    if any(s.review_status == "rejected" for s in samples):
        raise ValueError("数据包含被拒绝的样本，应移出候选集")
    if purpose == "formal":
        unreviewed = sum(s.review_status != "approved" for s in samples)
        unknown_rights = sum(s.rights_status == "unknown" for s in samples)
        if unreviewed or unknown_rights:
            raise ValueError(
                f"正式实验被阻止: 未复核 {unreviewed} 条，权利未确认 {unknown_rights} 条"
            )


def split_samples(
    samples: list[TicketSample], groups: dict[str, str], seed: int = 42
) -> dict[str, list[TicketSample]]:
    """Stratify homogeneous groups, choosing balance solely from label counts."""
    by_label: dict[int, dict[str, list[TicketSample]]] = defaultdict(lambda: defaultdict(list))
    for sample in samples:
        by_label[sample.label][groups[sample.sample_id]].append(sample)
    if set(by_label) != set(LABEL_NAMES):
        raise ValueError("六类标签必须全部有样本")
    splits: dict[str, list[TicketSample]] = {name: [] for name in SPLIT_NAMES}
    rng = random.Random(seed)
    for label in LABEL_NAMES:
        label_groups = list(by_label[label].values())
        n = len(label_groups)
        if n < 3:
            raise ValueError(f"类别 {label} 只有 {n} 个独立组，至少需要 3 组")
        n_holdout = max(1, round(n * 0.15))
        total = sum(map(len, label_groups))
        best = None
        best_loss = float("inf")
        for _ in range(128):
            rng.shuffle(label_groups)
            candidate = (
                label_groups[2 * n_holdout :],
                label_groups[:n_holdout],
                label_groups[n_holdout : 2 * n_holdout],
            )
            loss = sum(
                (sum(map(len, partition)) / total - target) ** 2
                for partition, target in zip(candidate, (0.7, 0.15, 0.15), strict=True)
            )
            if loss < best_loss:
                best_loss = loss
                best = tuple(list(part) for part in candidate)
        assert best is not None
        for name, partition in zip(SPLIT_NAMES, best, strict=True):
            splits[name].extend(s for group in partition for s in group)
    for partition in splits.values():
        partition.sort(key=lambda s: s.sample_id)
    return splits


def prepare_manifest(path: Path, purpose: Purpose, seed: int = 42) -> dict:
    samples = load_samples(path)
    validate_purpose(samples, purpose)
    groups, audit = build_groups(samples)
    if purpose == "formal" and audit["exact_duplicate_pairs"]:
        raise ValueError("正式实验需先去除归一化完全重复样本")
    splits = split_samples(samples, groups, seed)
    return {
        "schema_version": 1,
        "purpose": purpose,
        "evidence_scope": "synthetic_pipeline_smoke_only" if purpose == "smoke" else "offline_test",
        "seed": seed,
        "label_version": LABEL_VERSION,
        "label_map_hash": label_map_hash(),
        "source_file_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "dataset_hash": canonical_hash([s.model_dump() for s in samples]),
        "sample_count": len(samples),
        "review_counts": dict(Counter(s.review_status for s in samples)),
        "source_counts": dict(Counter(s.source_kind for s in samples)),
        "audit": audit,
        "groups": groups,
        "splits": {name: [s.sample_id for s in part] for name, part in splits.items()},
        "distribution": {
            name: {str(k): sum(s.label == k for s in part) for k in LABEL_NAMES}
            for name, part in splits.items()
        },
        "limitations": [
            "词面相似检测不能证明语义无泄漏，仍需按原始对话/模板/生成批次人工归组",
            "小样本结果仅用于验证流程，不能代表真实企业效果",
        ],
    }


def verify_manifest(samples: list[TicketSample], manifest: dict) -> None:
    """Reject stale, modified or overlapping splits before any model is fitted."""
    if manifest["label_map_hash"] != label_map_hash():
        raise ValueError("标签映射不一致")
    if manifest["dataset_hash"] != canonical_hash([s.model_dump() for s in samples]):
        raise ValueError("数据已变化，请重新准备清单")
    validate_purpose(samples, manifest["purpose"])
    expected = {s.sample_id for s in samples}
    seen: set[str] = set()
    seen_groups: set[str] = set()
    by_id = {s.sample_id: s for s in samples}
    actual_groups, audit = build_groups(samples)
    if manifest["groups"] != actual_groups:
        raise ValueError("分组清单被修改或归组规则发生变化")
    if manifest["purpose"] == "formal" and audit["exact_duplicate_pairs"]:
        raise ValueError("正式实验包含重复样本")
    if set(manifest["splits"]) != set(SPLIT_NAMES):
        raise ValueError("必须提供 train/validation/test 三个集合")
    for name in SPLIT_NAMES:
        ids = manifest["splits"][name]
        if len(set(ids)) != len(ids) or not ids or set(ids) - expected or seen & set(ids):
            raise ValueError("切分包含缺失、未知或重复样本")
        if {by_id[i].label for i in ids} != set(LABEL_NAMES):
            raise ValueError("每个集合都必须包含全部六类")
        group_set = {actual_groups[i] for i in ids}
        if seen_groups & group_set:
            raise ValueError("相似组跨集合泄漏")
        seen.update(ids)
        seen_groups.update(group_set)
    if seen != expected:
        raise ValueError("切分没有覆盖全部样本")
    train_only = manifest.get("train_only_ids", [])
    if not isinstance(train_only, list) or not set(train_only) <= set(manifest["splits"]["train"]):
        raise ValueError("评审器校准样本及同源组只能进入训练集")
