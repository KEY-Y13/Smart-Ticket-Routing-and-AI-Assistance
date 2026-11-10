import copy
import json

import pytest
from pydantic import ValidationError

from nlp.data.dataset import (
    build_groups,
    load_samples,
    normalize_for_leakage,
    prepare_manifest,
    split_samples,
    verify_manifest,
)
from nlp.data.schema import TicketSample
from nlp.labels import LABEL_NAMES


def test_sample_scope_is_explicit(samples):
    assert len(samples) == 72
    assert {s.label for s in samples} == set(LABEL_NAMES)
    assert all(s.source_kind == "synthetic" and s.review_status == "unreviewed" for s in samples)


def test_formal_experiment_rejects_unreviewed_samples(sample_path):
    with pytest.raises(ValueError, match="正式实验被阻止"):
        prepare_manifest(sample_path, "formal")


def test_split_is_deterministic_complete_and_disjoint(sample_path, samples):
    first = prepare_manifest(sample_path, "smoke", 42)
    second = prepare_manifest(sample_path, "smoke", 42)
    assert first == second
    verify_manifest(samples, first)
    sets = [set(partition) for partition in first["splits"].values()]
    assert len(set.union(*sets)) == len(samples)
    assert not sets[0] & sets[1] and not sets[0] & sets[2] and not sets[1] & sets[2]
    assert all(
        set(counts) == {str(k) for k in LABEL_NAMES} for counts in first["distribution"].values()
    )


def test_order_id_variations_are_grouped(samples):
    a = samples[0].model_copy(
        update={"sample_id": "a", "group_id": "a", "text": "订单 ST202611060001 想修改收货地址。"}
    )
    b = a.model_copy(
        update={"sample_id": "b", "group_id": "b", "text": "订单ST202611069999想修改收货地址！"}
    )
    groups, audit = build_groups([a, b])
    assert groups["a"] == groups["b"]
    assert audit["exact_duplicate_pairs"] == 1
    assert normalize_for_leakage("七天和15天") != normalize_for_leakage("七天和30天")


def test_cross_label_similar_group_requires_review(samples):
    a = samples[0]
    b = a.model_copy(update={"sample_id": "other", "label": 1})
    with pytest.raises(ValueError, match="标签冲突"):
        build_groups([a, b])


def test_shared_source_group_never_crosses_splits(samples):
    changed = list(samples)
    changed[1] = changed[1].model_copy(update={"group_id": changed[0].group_id})
    groups, _ = build_groups(changed)
    splits = split_samples(changed, groups)
    destinations = {s.sample_id: name for name, part in splits.items() for s in part}
    assert destinations[changed[0].sample_id] == destinations[changed[1].sample_id]


def test_data_change_invalidates_manifest(sample_path, samples):
    manifest = prepare_manifest(sample_path, "smoke")
    samples[0] = samples[0].model_copy(update={"text": "数据内容已修改，需要重新生成固定切分清单"})
    with pytest.raises(ValueError, match="数据已变化"):
        verify_manifest(samples, manifest)


def test_overlapping_split_is_rejected(sample_path, samples):
    manifest = prepare_manifest(sample_path, "smoke")
    manifest["splits"]["test"].append(manifest["splits"]["train"][0])
    with pytest.raises(ValueError, match="重复样本"):
        verify_manifest(samples, manifest)


def test_reviewer_calibration_cannot_be_moved_into_classifier_holdout(sample_path, samples):
    manifest = prepare_manifest(sample_path, "smoke")
    manifest["train_only_ids"] = [manifest["splits"]["train"][0]]
    verify_manifest(samples, manifest)
    manifest["train_only_ids"] = [manifest["splits"]["test"][0]]
    with pytest.raises(ValueError, match="评审器校准"):
        verify_manifest(samples, manifest)


def test_tampered_group_mapping_is_rejected(sample_path, samples):
    manifest = prepare_manifest(sample_path, "smoke")
    manifest["groups"][samples[0].sample_id] = "fabricated"
    with pytest.raises(ValueError, match="分组清单被修改"):
        verify_manifest(samples, manifest)


def test_approval_requires_identified_reviewer(samples):
    record = samples[0].model_dump()
    record["review_status"] = "approved"
    with pytest.raises(ValidationError, match="复核人"):
        TicketSample.model_validate(record)


@pytest.mark.parametrize("field,value", [("label", 6), ("label", "0"), ("rights_status", "free")])
def test_invalid_data_contract(samples, field, value):
    record = samples[0].model_dump()
    record[field] = value
    with pytest.raises(ValidationError):
        TicketSample.model_validate(record)


def test_unknown_field_rejected(samples):
    record = samples[0].model_dump()
    record["customer_phone"] = "not_allowed"
    with pytest.raises(ValidationError):
        TicketSample.model_validate(record)


def test_duplicate_sample_ids_rejected(tmp_path, samples):
    path = tmp_path / "invalid.jsonl"
    line = samples[0].model_dump_json()
    path.write_text(line + "\n" + line, encoding="utf-8")
    with pytest.raises(ValueError, match="sample_id 重复"):
        load_samples(path)


def test_unknown_rights_block_formal_evaluation(tmp_path, samples):
    path = tmp_path / "unknown.jsonl"
    records = [s.model_dump() for s in samples]
    for record in records:
        record.update(review_status="approved", reviewer="test-reviewer", rights_status="unknown")
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    with pytest.raises(ValueError, match="权利未确认 72"):
        prepare_manifest(path, "formal")


def test_missing_class_or_insufficient_groups_rejected(samples):
    groups, _ = build_groups(samples)
    with pytest.raises(ValueError, match="六类标签"):
        split_samples([s for s in samples if s.label != 5], groups)
    selected = [s for s in samples if s.label != 0][:] + [samples[0]]
    with pytest.raises(ValueError, match="至少需要 3 组"):
        split_samples(selected, groups)


def test_each_partition_needs_all_labels(sample_path, samples):
    manifest = copy.deepcopy(prepare_manifest(sample_path, "smoke"))
    by_id = {s.sample_id: s for s in samples}
    removed = [i for i in manifest["splits"]["test"] if by_id[i].label == 0]
    manifest["splits"]["test"] = [i for i in manifest["splits"]["test"] if i not in removed]
    manifest["splits"]["train"].extend(removed)
    with pytest.raises(ValueError, match="全部六类"):
        verify_manifest(samples, manifest)
