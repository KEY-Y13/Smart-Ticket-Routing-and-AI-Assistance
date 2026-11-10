import json

import numpy as np
import pytest

from nlp.artifacts import file_hash, write_json
from nlp.bert.assets import FILES, REVISION, prepare_assets, verify_assets
from nlp.data.dataset import canonical_hash, prepare_manifest
from nlp.evaluation.compare import compare_runs
from nlp.experiments import normalized_scores


def test_native_six_class_scores_are_reordered_and_normalized():
    result = normalized_scores([[0.10001, 0.10001, 0.20001, 0.20001, 0.10001, 0.30001]])
    assert result.shape == (1, 6)
    np.testing.assert_allclose(result.sum(axis=1), 1)
    assert result.argmax(axis=1).tolist() == [5]


@pytest.mark.parametrize("values", [[], [[1, 2]], [[float("nan")] * 6], [[0] * 6], [[-1] * 6]])
def test_malformed_predictions_rejected(values):
    with pytest.raises(ValueError):
        normalized_scores(values)


def test_asset_copy_is_non_destructive_and_detects_changes(tmp_path):
    # Tiny inert bytes only exercise copy/integrity logic, never model inference.
    source = tmp_path / REVISION
    source.mkdir()
    for name in FILES:
        (source / name).write_text("test-fixture", encoding="utf-8")
    (source / "config.json").write_text(json.dumps({"model_type": "bert", "hidden_size": 768}))
    destination = tmp_path / "assets"
    manifest = prepare_assets(source, destination)
    assert manifest == verify_assets(destination)
    assert file_hash(source / "model.safetensors") == file_hash(destination / "model.safetensors")
    with pytest.raises(FileExistsError):
        prepare_assets(source, destination)
    (destination / "vocab.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希变化"):
        verify_assets(destination)


def test_wrong_revision_rejected_before_copy(tmp_path):
    with pytest.raises(ValueError, match="revision"):
        prepare_assets(tmp_path / "wrong-revision", tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


def test_comparison_rejects_changed_split_and_never_overwrites(tmp_path, sample_path):
    manifest = prepare_manifest(sample_path, "smoke")
    paths = []
    for i in range(2):
        folder = tmp_path / str(i)
        folder.mkdir()
        write_json(folder / "split-manifest.json", manifest)
        report = {
            "name": f"model-{i}",
            "purpose": "smoke",
            "dataset_hash": manifest["dataset_hash"],
            "split_manifest_hash": canonical_hash(manifest),
            "test": {"accuracy": 0.5},
            "test_selective": {},
            "latency": {},
            "environment": {},
            "model_bytes": 1,
        }
        write_json(folder / "metrics.json", report)
        paths.append(folder / "metrics.json")
    output = tmp_path / "comparison.json"
    result = compare_runs(paths, output)
    assert set(result["models"]) == {"model-0", "model-1"}
    with pytest.raises(FileExistsError):
        compare_runs(paths, output)
    report["split_manifest_hash"] = "changed"
    paths[1].write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="不能比较"):
        compare_runs(paths, tmp_path / "invalid-comparison.json")
    assert not (tmp_path / "invalid-comparison.json").exists()
