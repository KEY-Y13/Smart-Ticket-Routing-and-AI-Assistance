import json

import joblib
import numpy as np
import pytest

from nlp.artifacts import write_json
from nlp.baselines.train import train_baselines
from nlp.cli import main
from nlp.data.dataset import prepare_manifest
from nlp.evaluation.metrics import choose_threshold, classification_metrics
from nlp.inference.classifier import ArtifactClassifier, ClassifierNotReady


def test_metrics_computed_from_predictions():
    report = classification_metrics([0, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 0])
    assert report["accuracy"] == 5 / 6
    assert report["confusion_matrix"][5][0] == 1
    assert report["sample_count"] == 6


def test_threshold_can_disable_all_automatic_classification():
    probs = np.full((6, 6), 0.02)
    for i in range(6):
        probs[i, (i + 1) % 6] = 0.9
    policy = choose_threshold(list(range(6)), probs)
    assert policy["enabled"] is False
    assert policy["threshold"] is None


def test_threshold_acceptance_is_selected_using_validation_scores():
    probs = np.array([[0.8, 0.1, 0.025, 0.025, 0.025, 0.025], [0.4, 0.3, 0.1, 0.1, 0.05, 0.05]])
    policy = choose_threshold([0, 1], probs, 1.0)
    assert policy["threshold"] == 0.8
    assert policy["coverage"] == 0.5
    assert policy["statistical_guarantee"] is False


def test_real_baselines_use_train_vocabulary_and_save_errors(tmp_path, sample_path, samples):
    manifest = prepare_manifest(sample_path, "smoke")
    # Add a unique token only to a held-out sample, then regenerate valid hashes.
    test_id = manifest["splits"]["test"][0]
    altered = [
        s.model_copy(update={"text": s.text + " Ω"}) if s.sample_id == test_id else s
        for s in samples
    ]
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(s.model_dump_json() for s in altered), encoding="utf-8")
    manifest = prepare_manifest(path, "smoke")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest)
    out = tmp_path / "run"
    report = train_baselines(path, manifest_path, out)
    assert set(report["models"]) == {"tfidf_rf", "tfidf_logreg"}
    by_id = {s.sample_id: s for s in altered}
    for name in report["models"]:
        pipeline = joblib.load(out / name / "model.joblib")
        assert "ω" not in pipeline["tfidf"].vocabulary_
        test = [by_id[i] for i in manifest["splits"]["test"]]
        predicted = pipeline.predict([s.text for s in test])
        assert report["models"][name]["test"]["accuracy"] == np.mean(
            predicted == [s.label for s in test]
        )
        assert (out / name / "errors.json").exists()
        assert report["models"][name]["model_reload_verified"] is True
        with pytest.raises(ClassifierNotReady, match="部署审批"):
            ArtifactClassifier(out / name / "manifest.json")
    before = (out / "comparison.json").read_bytes()
    with pytest.raises(FileExistsError):
        train_baselines(path, manifest_path, out)
    assert (out / "comparison.json").read_bytes() == before


def test_cli_does_not_overwrite_outputs(tmp_path, sample_path):
    path = tmp_path / "manifest.json"
    path.write_text("keep me", encoding="utf-8")
    assert (
        main(["prepare", "--data", str(sample_path), "--purpose", "smoke", "--out", str(path)]) == 2
    )
    assert path.read_text(encoding="utf-8") == "keep me"


def test_smoke_artifact_rejected_before_loading_pickle(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"purpose": "smoke", "deployment_approved": True}), encoding="utf-8")
    with pytest.raises(ClassifierNotReady):
        ArtifactClassifier(path)


def test_cli_failure_does_not_write_into_preexisting_experiment(tmp_path, sample_path):
    manifest = tmp_path / "manifest.json"
    write_json(manifest, prepare_manifest(sample_path, "smoke"))
    existing = tmp_path / "existing"
    existing.mkdir()
    keep = existing / "notes.txt"
    keep.write_text("existing user content", encoding="utf-8")
    assert (
        main(
            [
                "train-baselines",
                "--data",
                str(sample_path),
                "--manifest",
                str(manifest),
                "--out",
                str(existing),
            ]
        )
        == 2
    )
    assert list(existing.iterdir()) == [keep]
