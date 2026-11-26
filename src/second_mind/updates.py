from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

from .config import AppSettings

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class UpdateError(RuntimeError):
    pass


class UpdateService:
    def __init__(self, settings: AppSettings, current_version: str) -> None:
        self.settings = settings
        self.current_version = current_version

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

    async def check(self) -> dict[str, Any]:
        repository = self._repository()
        url = f"https://api.github.com/repos/{repository}/releases/latest"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": f"Second-Mind/{self.current_version}",
                    },
                )
                response.raise_for_status()
                release = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise UpdateError(f"Cannot check GitHub Releases: {exc}") from exc
        tag = str(release.get("tag_name", "")).lstrip("v")
        try:
            available = Version(tag) > Version(self.current_version)
        except InvalidVersion as exc:
            raise UpdateError(f"Release tag is not a valid version: {tag}") from exc
        assets = [
            {
                "name": asset.get("name"),
                "size": asset.get("size"),
                "url": asset.get("browser_download_url"),
                "digest": asset.get("digest"),
            }
            for asset in release.get("assets", [])
            if str(asset.get("name", "")).casefold().endswith(".exe")
            and "setup" in str(asset.get("name", "")).casefold()
        ]
        asset = assets[0] if assets else None
        return {
            "configured": True,
            "repository": repository,
            "current_version": self.current_version,
            "latest_version": tag,
            "available": available,
            "release_name": release.get("name"),
            "release_url": release.get("html_url"),
            "published_at": release.get("published_at"),
            "asset": asset,
            "verified_download_supported": bool(
                asset and str(asset.get("digest") or "").startswith("sha256:")
            ),
        }

    async def download(self) -> dict[str, Any]:
        update = await self.check()
        if not update["available"]:
            raise UpdateError("No newer release is available")
        asset = update.get("asset")
        if not asset or not str(asset.get("digest") or "").startswith("sha256:"):
            raise UpdateError("Release installer has no GitHub SHA-256 digest; download refused")
        expected = str(asset["digest"]).split(":", 1)[1].casefold()
        filename = Path(str(asset["name"])).name
        if not filename.casefold().endswith(".exe"):
            raise UpdateError("Release asset is not a Windows installer")
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        destination = self.updates_dir / filename
        temp = destination.with_suffix(".download")
        digest = hashlib.sha256()
        size = 0
        try:
            async with (
                httpx.AsyncClient(timeout=120, follow_redirects=True) as client,
                client.stream("GET", str(asset["url"])) as response,
            ):
                response.raise_for_status()
                with temp.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 1_000_000_000:
                            raise UpdateError("Installer exceeds the 1 GB safety limit")
                        digest.update(chunk)
                        output.write(chunk)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        if digest.hexdigest().casefold() != expected:
            temp.unlink(missing_ok=True)
            raise UpdateError("Installer SHA-256 does not match the GitHub asset digest")
        temp.replace(destination)
        metadata = {
            "path": str(destination),
            "sha256": expected,
            "version": update["latest_version"],
            "size": size,
        }
        destination.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return metadata

    async def install(self, installer: Path) -> None:
        resolved = installer.resolve()
        root = self.updates_dir.resolve()
        if resolved.parent != root or resolved.suffix.casefold() != ".exe":
            raise UpdateError("Installer must be a verified file in the updates directory")
        metadata_path = resolved.with_suffix(".json")
        if not resolved.exists() or not metadata_path.exists():
            raise UpdateError("Verified installer metadata is missing")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        actual = await asyncio.to_thread(
            lambda: hashlib.sha256(resolved.read_bytes()).hexdigest().casefold()
        )
        if actual != str(metadata.get("sha256", "")).casefold():
            raise UpdateError("Installer changed after download")
        subprocess.Popen([str(resolved)], close_fds=True)
