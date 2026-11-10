from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from scripts.local_https import create_certificates
from scripts.monitor_local import event_from_snapshot
from scripts.package_release import release_paths


def test_monitor_events_are_deduplicated_and_recovery_is_distinct():
    unhealthy = {"status": "degraded", "alerts": ["明确模拟后台心跳过期"]}
    first, fingerprint = event_from_snapshot(unhealthy, None)
    assert first["event"] == "ATTENTION"
    duplicate, _ = event_from_snapshot(unhealthy, fingerprint)
    assert duplicate is None
    recovered, _ = event_from_snapshot({"status": "healthy", "alerts": []}, fingerprint)
    assert recovered["event"] == "RECOVERED" and recovered["alerts"] == []


def test_release_allowlist_excludes_private_data_and_environments(tmp_path):
    names = [
        "README.md",
        ".env.example",
        ".env",
        ".mysql-local/credentials.json",
        ".tls-local/server.key",
        ".conda/python.exe",
        "frontend/node_modules/secret.js",
        "frontend/dist/index.html",
        "backend/main.py",
        "backend/__pycache__/private.pyc",
        "data/reviewed/private.jsonl",
        "data/samples/classification-smoke.jsonl",
    ]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("EXPLICIT_SYNTHETIC_PACKAGING_TEST")
    actual = {path.relative_to(tmp_path).as_posix() for path in release_paths(tmp_path)}
    assert actual == {
        "README.md",
        ".env.example",
        "frontend/dist/index.html",
        "backend/main.py",
        "data/samples/classification-smoke.jsonl",
    }


def test_release_rejects_requirement_links_even_in_root_glob(tmp_path, monkeypatch):
    source = tmp_path / "requirements-linked.txt"
    source.write_text("EXPLICIT_TEST_NOT_A_REAL_SECRET")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == source or original(path))
    with pytest.raises(ValueError, match="link"):
        release_paths(tmp_path)


def test_local_ca_and_leaf_have_correct_scope_and_refuse_overwrite(tmp_path):
    root, certificate, key = create_certificates(tmp_path / "private")
    ca = x509.load_pem_x509_certificate(root.read_bytes())
    leaf = x509.load_pem_x509_certificate(certificate.read_bytes())
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    assert leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert leaf.issuer == ca.subject and leaf.not_valid_after_utc > datetime.now(UTC)
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["localhost"]
    assert (
        ExtendedKeyUsageOID.SERVER_AUTH
        in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    )
    assert key.exists()
    with pytest.raises(FileExistsError):
        create_certificates(tmp_path / "private")
