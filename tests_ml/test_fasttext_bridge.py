"""Real native-worker reproducibility checks in the explicitly provisioned ML test run."""

import json
from pathlib import Path

import numpy as np
import pytest

from nlp.artifacts import write_json
from nlp.data.dataset import prepare_manifest
from nlp.fasttext.train import train_fasttext


def test_native_fasttext_reproducible_and_preserves_existing_output(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data = root / "data/samples/classification-smoke.jsonl"
    python = root / ".venv-fasttext/Scripts/python.exe"
    manifest = tmp_path / "split.json"
    write_json(manifest, prepare_manifest(data, "smoke"))
    first = train_fasttext(data, manifest, tmp_path / "first", python)
    second = train_fasttext(data, manifest, tmp_path / "second", python)
    assert first["test"] == second["test"]
    a = json.loads((tmp_path / "first/worker-result.json").read_text(encoding="utf-8"))
    b = json.loads((tmp_path / "second/worker-result.json").read_text(encoding="utf-8"))
    np.testing.assert_allclose(a["test_scores"], b["test_scores"], rtol=0, atol=0)
    assert first["model_files"]["model.bin"] == second["model_files"]["model.bin"]
    before = (tmp_path / "first/metrics.json").read_bytes()
    with pytest.raises(FileExistsError):
        train_fasttext(data, manifest, tmp_path / "first", python)
    assert before == (tmp_path / "first/metrics.json").read_bytes()
