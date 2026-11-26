from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

import keyring
from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field, field_validator

APP_NAME = "Second Mind"
KEYRING_SERVICE = "SecondMind"
DEFAULT_UPDATE_REPOSITORY = "markKikhtenko/second-mind"


def default_data_dir() -> Path:
    override = os.environ.get("SECOND_MIND_DATA_DIR")
    return Path(override).expanduser().resolve() if override else user_data_path(APP_NAME, APP_NAME)


class AppSettings(BaseModel):
    """Non-secret settings persisted as JSON in the local application directory."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    data_dir: Path = Field(default_factory=default_data_dir)
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8765, ge=1024, le=65535)
    llm_base_url: str = "http://127.0.0.1:1234"
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=180.0, ge=5, le=900)
    telegram_allowed_user_ids: list[int] = Field(default_factory=list)
    telegram_enabled: bool = False
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str | None = None
    setup_completed: bool = False
    autostart_enabled: bool = False
    update_repository: str | None = DEFAULT_UPDATE_REPOSITORY
    automatic_update_checks: bool = True
    update_check_interval_hours: int = Field(default=24, ge=1, le=168)
    log_level: str = "INFO"

    @field_validator("api_host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Second Mind API must bind to a loopback address")
        return value

    @field_validator("llm_base_url")
    @classmethod
    def normalize_llm_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
            raise ValueError("External LLM endpoints require an explicit provider adapter and consent")
        return value

    @property
    def database_path(self) -> Path:
        return self.data_dir / "second-mind.db"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def api_url(self) -> str:
        host = "127.0.0.1" if self.api_host in {"localhost", "::1"} else self.api_host
        return f"http://{host}:{self.api_port}"


class ConfigManager:
    def __init__(self, path: Path | None = None) -> None:
        default_dir = default_data_dir()
        self.path = path or default_dir / "config.json"

    def load(self) -> AppSettings:
        if not self.path.exists():
            settings = AppSettings(data_dir=self.path.parent)
            self.save(settings)
            return settings
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw.setdefault("data_dir", str(self.path.parent))
        if "automatic_update_checks" not in raw and not raw.get("update_repository"):
            # v0.1.0 stored an explicit null, which would otherwise suppress the
            # new default release channel after an in-place upgrade.
            raw["update_repository"] = DEFAULT_UPDATE_REPOSITORY
        return AppSettings.model_validate(raw)

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = settings.model_dump(mode="json")
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    def update(self, **changes: Any) -> AppSettings:
        settings = self.load()
        updated = settings.model_copy(update=changes)
        updated = AppSettings.model_validate(updated.model_dump())
        self.save(updated)
        return updated


class SecretStore:
    """Secrets live in Windows Credential Manager through keyring, never in config.json."""

    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, name)
        except keyring.errors.KeyringError:
            return None

    def set(self, name: str, value: str) -> None:
        if not value:
            self.delete(name)
            return
        keyring.set_password(KEYRING_SERVICE, name, value)

    def delete(self, name: str) -> None:
        with suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(KEYRING_SERVICE, name)


class MemorySecretStore(SecretStore):
    """Test-only secret storage."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        if value:
            self.values[name] = value
        else:
            self.values.pop(name, None)

    def delete(self, name: str) -> None:
        self.values.pop(name, None)
