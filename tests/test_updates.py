from __future__ import annotations

import argparse
import base64
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from second_mind import updates
from second_mind.config import DEFAULT_UPDATE_REPOSITORY, ConfigManager
from second_mind.updater import UpdateTransaction
from second_mind.updates import UpdateError, verify_download_metadata, verify_signed_manifest


def test_v01_config_migrates_to_default_release_channel(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"data_dir": str(tmp_path), "update_repository": None}),
        encoding="utf-8",
    )

    settings = ConfigManager(config_path).load()

    assert settings.update_repository == DEFAULT_UPDATE_REPOSITORY


def signed_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(updates, "UPDATE_PUBLIC_KEY_PEM", public_pem)
    installer = tmp_path / "Second-Mind-Setup-0.2.0.exe"
    installer.write_bytes(b"verified installer")
    import hashlib

    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "repository": "owner/repository",
        "tag": "v0.2.0",
        "version": "0.2.0",
        "installer": {
            "name": installer.name,
            "size": installer.stat().st_size,
            "sha256": digest,
        },
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    signature_bytes = base64.b64encode(private_key.sign(manifest_bytes)) + b"\n"
    metadata = installer.with_suffix(installer.suffix + ".json")
    metadata.write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "sha256": digest,
                "manifest": base64.b64encode(manifest_bytes).decode(),
                "signature": base64.b64encode(signature_bytes).decode(),
            }
        ),
        encoding="utf-8",
    )
    return installer, metadata


def test_signed_manifest_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, metadata = signed_download(tmp_path, monkeypatch)
    manifest = verify_download_metadata(installer, metadata)
    assert manifest["version"] == "0.2.0"

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    manifest_bytes = base64.b64decode(payload["manifest"])
    signature_bytes = base64.b64decode(payload["signature"])
    with pytest.raises(UpdateError, match="signature"):
        verify_signed_manifest(manifest_bytes + b" ", signature_bytes)

    installer.write_bytes(b"tampered installer")
    with pytest.raises(UpdateError, match="changed"):
        verify_download_metadata(installer, metadata)


def test_failed_update_restores_application_and_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, metadata = signed_download(tmp_path, monkeypatch)
    install_dir = tmp_path / "program" / "Second Mind"
    desktop = install_dir / "second-mind-desktop" / "second-mind-desktop.exe"
    core = install_dir / "second-mind-core" / "second-mind-core.exe"
    desktop.parent.mkdir(parents=True)
    core.parent.mkdir(parents=True)
    desktop.write_bytes(b"old desktop")
    core.write_bytes(b"old core")
    data_dir = tmp_path / "user-data"
    data_dir.mkdir()
    database = data_dir / "second-mind.db"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('preserved')")
    config = data_dir / "config.json"
    config.write_text('{"setting":"preserved"}', encoding="utf-8")

    arguments = argparse.Namespace(
        installer=str(installer),
        metadata=str(metadata),
        install_dir=str(install_dir),
        data_dir=str(data_dir),
        api_url="http://127.0.0.1:8765",
        desktop_pid=0,
        current_version="0.1.0",
        no_restart=True,
    )
    transaction = UpdateTransaction(arguments)
    monkeypatch.setattr(transaction, "stop_core", lambda: None)
    monkeypatch.setattr(transaction, "stop_installed_processes", lambda: None)
    monkeypatch.setattr(transaction, "wait_for_desktop", lambda: None)
    monkeypatch.setattr(
        transaction,
        "launch_desktop",
        lambda rolled_back=False, failed=False: None,
    )

    def broken_install() -> None:
        desktop.write_bytes(b"new desktop")
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE marker SET value='migrated'")
        config.write_text('{"setting":"migrated"}', encoding="utf-8")

    def validate(version: str, timeout: int = 60) -> None:
        if version == "0.2.0":
            raise UpdateError("new Core failed")

    monkeypatch.setattr(transaction, "run_installer", broken_install)
    monkeypatch.setattr(transaction, "start_core_and_validate", validate)

    assert transaction.execute() == 2
    assert desktop.read_bytes() == b"old desktop"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "preserved"
    assert json.loads(config.read_text(encoding="utf-8"))["setting"] == "preserved"
    state = json.loads(transaction.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "rolled_back"
