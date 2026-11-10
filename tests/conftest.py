from pathlib import Path

import pytest

from nlp.data.dataset import load_samples

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sample_path():
    return ROOT / "data/samples/classification-smoke.jsonl"


@pytest.fixture
def samples(sample_path):
    return load_samples(sample_path)
