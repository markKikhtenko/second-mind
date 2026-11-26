from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

from .config import AppSettings

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MANIFEST_NAME = "second-mind-release.json"
SIGNATURE_NAME = "second-mind-release.json.sig"
MAX_INSTALLER_BYTES = 1_000_000_000
MAX_METADATA_BYTES = 256_000
UPDATE_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA2C5Ho8IURB9VC8lBawRuNf9+rhdyhGQu1dHnlPvB+vI=
-----END PUBLIC KEY-----
"""


class UpdateError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().casefold()


def verify_signed_manifest(manifest_bytes: bytes, signature_bytes: bytes) -> dict[str, Any]:
    """Verify and decode the canonical release manifest bundled with every release."""
    try:
        signature = base64.b64decode(signature_bytes.strip(), validate=True)
        key = serialization.load_pem_public_key(UPDATE_PUBLIC_KEY_PEM)
        if not isinstance(key, Ed25519PublicKey):
            raise UpdateError("The embedded update key is not an Ed25519 key")
        key.verify(signature, manifest_bytes)
        manifest = json.loads(manifest_bytes)
    except InvalidSignature as exc:
        raise UpdateError("Release manifest signature is invalid") from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise UpdateError("Release manifest or signature is malformed") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise UpdateError("Release manifest schema is unsupported")
    return manifest


def verify_download_metadata(installer: Path, metadata_path: Path) -> dict[str, Any]:
    """Re-verify a downloaded installer in the detached updater process."""
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        manifest_bytes = base64.b64decode(metadata["manifest"], validate=True)
        signature_bytes = base64.b64decode(metadata["signature"], validate=True)
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise UpdateError("Verified installer metadata is invalid") from exc
    manifest = verify_signed_manifest(manifest_bytes, signature_bytes)
    installer_metadata = manifest.get("installer", {})
    expected = str(installer_metadata.get("sha256", "")).casefold()
    if (
        installer_metadata.get("name") != installer.name
        or str(manifest.get("version")) != str(metadata.get("version"))
        or expected != str(metadata.get("sha256", "")).casefold()
    ):
        raise UpdateError("Installer metadata no longer matches the signed manifest")
    if sha256_file(installer) != expected or installer.stat().st_size != int(
        installer_metadata.get("size", 0)
    ):
        raise UpdateError("Installer changed after download")
    return manifest


class UpdateService:
    def __init__(self, settings: AppSettings, current_version: str) -> None:
        self.settings = settings
        self.current_version = current_version
        self._download_task: asyncio.Task[None] | None = None
        self._state: dict[str, Any] = {
            "phase": "idle",
            "bytes_received": 0,
            "bytes_total": 0,
            "progress_percent": 0,
            "error": None,
            "download": None,
            "last_checked_at": None,
        }

    @property
    def updates_dir(self) -> Path:
        return self.settings.data_dir / "updates"

    def _repository(self) -> str:
        repository = self.settings.update_repository
        if not repository:
            raise UpdateError("Update repository is not configured")
        if not REPOSITORY_PATTERN.fullmatch(repository):
            raise UpdateError("Update repository must use the owner/repository format")
        return repository

    def status(self) -> dict[str, Any]:
        return dict(self._state)

    @staticmethod
    async def _small_download(client: httpx.AsyncClient, url: str) -> bytes:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content
        if len(content) > MAX_METADATA_BYTES:
            raise UpdateError("Release metadata exceeds the safety limit")
        return content

    async def check(self) -> dict[str, Any]:
        repository = self._repository()
        url = f"https://api.github.com/repos/{repository}/releases/latest"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Second-Mind/{self.current_version}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True, headers=headers
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                release = response.json()
                assets = {
                    str(asset.get("name")): asset
                    for asset in release.get("assets", [])
                    if asset.get("name") and asset.get("browser_download_url")
                }
                manifest_asset = assets.get(MANIFEST_NAME)
                signature_asset = assets.get(SIGNATURE_NAME)
                if not manifest_asset or not signature_asset:
                    raise UpdateError("Release does not contain signed update metadata")
                manifest_bytes, signature_bytes = await asyncio.gather(
                    self._small_download(
                        client, str(manifest_asset["browser_download_url"])
                    ),
                    self._small_download(
                        client, str(signature_asset["browser_download_url"])
                    ),
                )
        except UpdateError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise UpdateError(f"Cannot check GitHub Releases: {exc}") from exc

        manifest = verify_signed_manifest(manifest_bytes, signature_bytes)
        tag = str(release.get("tag_name", ""))
        version_text = tag.lstrip("v")
        try:
            available = Version(version_text) > Version(self.current_version)
        except InvalidVersion as exc:
            raise UpdateError(f"Release tag is not a valid version: {tag}") from exc
        if manifest.get("repository") != repository or manifest.get("tag") != tag:
            raise UpdateError("Signed manifest does not belong to this repository release")
        if str(manifest.get("version")) != version_text:
            raise UpdateError("Signed manifest version does not match the release tag")
        installer = manifest.get("installer")
        if not isinstance(installer, dict):
            raise UpdateError("Signed manifest does not describe an installer")
        filename = Path(str(installer.get("name", ""))).name
        digest = str(installer.get("sha256", "")).casefold()
        if (
            filename != installer.get("name")
            or not filename.casefold().endswith(".exe")
            or "setup" not in filename.casefold()
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise UpdateError("Signed installer metadata is invalid")
        asset = assets.get(filename)
        if not asset:
            raise UpdateError("The signed installer is missing from the release")
        signed_size = int(installer.get("size", 0))
        asset_size = int(asset.get("size", 0))
        if signed_size <= 0 or signed_size != asset_size or signed_size > MAX_INSTALLER_BYTES:
            raise UpdateError("Signed installer size does not match the release asset")
        github_digest = str(asset.get("digest") or "")
        if github_digest and github_digest.casefold() != f"sha256:{digest}":
            raise UpdateError("GitHub asset digest conflicts with the signed manifest")

        checked_at = datetime.now(UTC).isoformat()
        self._state["last_checked_at"] = checked_at
        return {
            "configured": True,
            "repository": repository,
            "current_version": self.current_version,
            "latest_version": version_text,
            "available": available,
            "release_name": release.get("name") or tag,
            "release_url": release.get("html_url"),
            "published_at": release.get("published_at"),
            "changelog": str(release.get("body") or "").strip(),
            "asset": {
                "name": filename,
                "size": signed_size,
                "url": asset["browser_download_url"],
                "sha256": digest,
            },
            "manifest": base64.b64encode(manifest_bytes).decode("ascii"),
            "signature": base64.b64encode(signature_bytes).decode("ascii"),
            "authenticity": "verified_ed25519_manifest",
            "checked_at": checked_at,
        }

    async def download(self) -> dict[str, Any]:
        self._state.update(
            phase="checking",
            bytes_received=0,
            bytes_total=0,
            progress_percent=0,
            error=None,
            download=None,
        )
        update = await self.check()
        if not update["available"]:
            raise UpdateError("No newer release is available")
        asset = update["asset"]
        filename = Path(str(asset["name"])).name
        expected = str(asset["sha256"]).casefold()
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        destination = self.updates_dir / filename
        temp = destination.with_suffix(destination.suffix + ".download")
        digest = hashlib.sha256()
        size = 0
        total = int(asset["size"])
        self._state.update(phase="downloading", bytes_total=total)
        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(30, read=180), follow_redirects=True
                ) as client,
                client.stream("GET", str(asset["url"])) as response,
            ):
                response.raise_for_status()
                with temp.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_INSTALLER_BYTES or size > total:
                            raise UpdateError("Installer exceeds its signed size")
                        digest.update(chunk)
                        output.write(chunk)
                        self._state.update(
                            bytes_received=size,
                            progress_percent=min(100, int(size * 100 / total)),
                        )
        except asyncio.CancelledError:
            temp.unlink(missing_ok=True)
            self._state.update(phase="cancelled", error="Download cancelled")
            raise
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        if size != total or digest.hexdigest().casefold() != expected:
            temp.unlink(missing_ok=True)
            raise UpdateError("Installer does not match the signed release manifest")
        temp.replace(destination)
        metadata = {
            "schema_version": 1,
            "path": str(destination),
            "sha256": expected,
            "version": update["latest_version"],
            "size": size,
            "repository": update["repository"],
            "manifest": update["manifest"],
            "signature": update["signature"],
            "verified_at": datetime.now(UTC).isoformat(),
        }
        metadata_path = destination.with_suffix(destination.suffix + ".json")
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        metadata["metadata_path"] = str(metadata_path)
        self._state.update(
            phase="ready",
            bytes_received=size,
            progress_percent=100,
            download=metadata,
        )
        return metadata

    def start_download(self) -> dict[str, Any]:
        if self._download_task and not self._download_task.done():
            raise UpdateError("An update download is already running")

        async def run() -> None:
            try:
                await self.download()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._state.update(phase="failed", error=str(exc))

        self._download_task = asyncio.create_task(run())
        return self.status()

    async def cancel_download(self) -> dict[str, Any]:
        if self._download_task and not self._download_task.done():
            self._download_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._download_task
        return self.status()

    async def prepare_install(self, installer: Path) -> dict[str, Any]:
        resolved = installer.resolve()
        root = self.updates_dir.resolve()
        if resolved.parent != root or resolved.suffix.casefold() != ".exe":
            raise UpdateError("Installer must be a verified file in the updates directory")
        metadata_path = resolved.with_suffix(resolved.suffix + ".json")
        if not resolved.exists() or not metadata_path.exists():
            raise UpdateError("Verified installer metadata is missing")
        manifest = await asyncio.to_thread(
            verify_download_metadata, resolved, metadata_path
        )
        expected = str(manifest["installer"]["sha256"]).casefold()
        return {
            "path": str(resolved),
            "metadata_path": str(metadata_path),
            "version": str(manifest["version"]),
            "sha256": expected,
            "authenticity": "verified_ed25519_manifest",
        }
