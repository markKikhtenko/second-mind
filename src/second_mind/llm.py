from __future__ import annotations

from typing import Any

import httpx

from .config import AppSettings, SecretStore


class LLMUnavailable(RuntimeError):
    pass


class LMStudioProvider:
    """Supported LM Studio Runtime adapter; no undocumented Bionic app APIs are used."""

    def __init__(self, settings: AppSettings, secrets: SecretStore) -> None:
        self.settings = settings
        self.secrets = secrets

    def _headers(self) -> dict[str, str]:
        token = self.secrets.get("llm_api_token")
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def list_models(self) -> list[dict[str, Any]]:
        timeout = min(self.settings.llm_timeout_seconds, 10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(
                    f"{self.settings.llm_base_url}/api/v1/models", headers=self._headers()
                )
                response.raise_for_status()
                payload = response.json()
                return list(payload.get("models", []))
            except (httpx.HTTPError, ValueError):
                try:
                    response = await client.get(
                        f"{self.settings.llm_base_url}/v1/models", headers=self._headers()
                    )
                    response.raise_for_status()
                    return [
                        {"key": item.get("id"), "display_name": item.get("id"), "type": "llm"}
                        for item in response.json().get("data", [])
                    ]
                except (httpx.HTTPError, ValueError) as exc:
                    raise LLMUnavailable(
                        f"LM Studio Runtime is unavailable at {self.settings.llm_base_url}"
                    ) from exc

    async def resolve_model(self) -> str:
        if self.settings.llm_model:
            return self.settings.llm_model
        models = await self.list_models()
        candidates = [model for model in models if model.get("type", "llm") == "llm"]
        if not candidates:
            raise LLMUnavailable("No language model is available in LM Studio Runtime")
        loaded = [model for model in candidates if model.get("loaded_instances")]
        selected = (loaded or candidates)[0]
        return str(selected.get("key") or selected.get("id"))

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        model = await self.resolve_model()
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "tools": tools,
            "tool_choice": "auto",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url}/v1/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
                return dict(payload["choices"][0]["message"])
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            detail = str(exc)
            if isinstance(exc, httpx.HTTPStatusError):
                detail = exc.response.text[:1000]
            raise LLMUnavailable(f"LM Studio inference failed: {detail}") from exc

    async def health(self) -> dict[str, Any]:
        try:
            models = await self.list_models()
            loaded = [item for item in models if item.get("loaded_instances")]
            selected = self.settings.llm_model
            if not selected and (loaded or models):
                model = (loaded or models)[0]
                selected = str(model.get("key") or model.get("id") or model.get("display_name"))
            return {
                "available": True,
                "models": len(models),
                "current_model": selected,
                "error": None,
            }
        except LLMUnavailable as exc:
            return {
                "available": False,
                "models": 0,
                "current_model": self.settings.llm_model,
                "error": str(exc),
            }
