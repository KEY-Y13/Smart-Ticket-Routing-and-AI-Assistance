"""Optional ML-runtime tests. Tiny random models here are not pretrained experiment results."""

import numpy as np
import pytest
import torch
from transformers import BertConfig, BertForSequenceClassification, BertTokenizerFast

from nlp.bert.train import BertTrainingConfig, encode, evaluate
from nlp.data.schema import TicketSample


def test_invalid_training_settings_rejected():
    with pytest.raises(ValueError):
        BertTrainingConfig(batch_size=0)
    with pytest.raises(ValueError):
        BertTrainingConfig(max_length=513)


def test_tiny_model_shapes_loss_and_encoder_update():
    model = BertForSequenceClassification(
        BertConfig(
            vocab_size=32,
            hidden_size=16,
            num_hidden_layers=1,
            num_attention_heads=2,
            intermediate_size=32,
            num_labels=6,
        )
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
    before = model.bert.encoder.layer[0].attention.self.query.weight.detach().clone()
    outputs = model(
        input_ids=torch.tensor([[2, 4, 5, 3], [2, 6, 3, 0]]),
        attention_mask=torch.tensor([[1, 1, 1, 1], [1, 1, 1, 0]]),
    )
    assert outputs.logits.shape == (2, 6)
    loss = torch.nn.functional.cross_entropy(outputs.logits, torch.tensor([0, 5]))
    loss.backward()
    optimizer.step()
    assert loss.isfinite()
    assert not torch.equal(before, model.bert.encoder.layer[0].attention.self.query.weight)


def test_sample_weighted_accumulation_matches_full_batch_gradient():
    torch.manual_seed(42)
    model = torch.nn.Linear(3, 6)
    inputs, labels = torch.randn(3, 3), torch.tensor([0, 1, 2])
    torch.nn.functional.cross_entropy(model(inputs), labels).backward()
    expected = model.weight.grad.clone()
    model.zero_grad()
    for start, end in ((0, 2), (2, 3)):
        loss = torch.nn.functional.cross_entropy(model(inputs[start:end]), labels[start:end])
        (loss * (end - start) / len(labels)).backward()
    torch.testing.assert_close(model.weight.grad, expected)


def test_tokenization_truncation_evaluation_and_dropout_off(tmp_path):
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("[PAD]\n[UNK]\n[CLS]\n[SEP]\n[MASK]\n订\n单\n问\n题\n", encoding="utf-8")
    tokenizer = BertTokenizerFast(vocab_file=str(vocab))
    sample = TicketSample(
        sample_id="unit-1",
        text="订单问题订单问题订单问题",
        label=0,
        group_id="unit-1",
        source_kind="synthetic",
        source_ref="unit-test-only",
        source_license="fixture",
        rights_status="project_internal",
        review_status="unreviewed",
    )
    encoded = encode(tokenizer, [sample], 8)
    assert len(encoded[0]["input_ids"]) == 8
    assert encoded[0]["labels"] == 0
    model = BertForSequenceClassification(
        BertConfig(
            vocab_size=32,
            hidden_size=16,
            num_hidden_layers=1,
            num_attention_heads=2,
            intermediate_size=32,
            num_labels=6,
        )
    )
    config = BertTrainingConfig(device="cpu", max_length=8)
    first, loss = evaluate(model, [sample], tokenizer, config)
    second, _ = evaluate(model, [sample], tokenizer, config)
    np.testing.assert_allclose(first, second)
    assert first.shape == (1, 6) and np.isfinite(loss)
