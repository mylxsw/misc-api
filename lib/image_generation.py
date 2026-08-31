"""Provider adapters for the unified image generation API."""

from __future__ import annotations

import base64
import os
import random
import time
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_TIMEOUT = 120
DEFAULT_MAX_WAIT = 300
DEFAULT_POLL_INTERVAL = 5.0
MAX_IMAGE_BYTES = 32 * 1024 * 1024


class ImageGenerationError(RuntimeError):
    """A provider request failed or returned an invalid result."""


class ImageProvider(ABC):
    name: str

    def __init__(self, api_key: str, api_base: str):
        if not api_key:
            raise ImageGenerationError(f"{self.name} API key is not configured")
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.session = _create_session()

    @abstractmethod
    def generate(self, model: str, prompt: str, size: str | None) -> bytes:
        raise NotImplementedError

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.session.request(method, url, timeout=DEFAULT_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise ImageGenerationError(f"{self.name} request failed: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise ImageGenerationError(
                f"{self.name} returned invalid JSON (HTTP {response.status_code})"
            ) from exc

        if not response.ok:
            raise ImageGenerationError(
                f"{self.name} API error (HTTP {response.status_code}): {_error_message(body)}"
            )
        if isinstance(body, dict) and body.get("error"):
            raise ImageGenerationError(f"{self.name} API error: {_error_message(body)}")
        if not isinstance(body, dict):
            raise ImageGenerationError(f"{self.name} returned an invalid response")
        return body

    def _download_image(self, url: str) -> bytes:
        try:
            response = self.session.get(url, timeout=DEFAULT_TIMEOUT, stream=True)
            response.raise_for_status()
            content_length = int(response.headers.get("Content-Length", "0") or 0)
            if content_length > MAX_IMAGE_BYTES:
                raise ImageGenerationError(f"{self.name} image exceeds the 32 MB limit")

            chunks = []
            total = 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ImageGenerationError(f"{self.name} image exceeds the 32 MB limit")
                chunks.append(chunk)
        except requests.RequestException as exc:
            raise ImageGenerationError(f"failed to download {self.name} image: {exc}") from exc

        image = b"".join(chunks)
        if not image:
            raise ImageGenerationError(f"{self.name} returned an empty image")
        return image


class GeminiProvider(ImageProvider):
    name = "gemini"

    def generate(self, model: str, prompt: str, size: str | None) -> bytes:
        generation_config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        image_config = _gemini_size(size)
        if image_config:
            generation_config["imageConfig"] = image_config

        body = self._request_json(
            "POST",
            f"{self.api_base}/v1beta/models/{quote(model, safe='')}:generateContent",
            params={"key": self.api_key},
            json={
                "contents": [{"parts": [{"text": f"Generate an image: {prompt}"}]}],
                "generationConfig": generation_config,
            },
        )

        try:
            parts = body["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ImageGenerationError("gemini response contains no generated image") from exc

        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                return _decode_base64(inline["data"], self.name)
        raise ImageGenerationError("gemini response contains no generated image")


class XAIProvider(ImageProvider):
    name = "xai"

    def generate(self, model: str, prompt: str, size: str | None) -> bytes:
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
        _apply_size(payload, size, ratio_field="aspect_ratio")
        body = self._request_json(
            "POST",
            f"{self.api_base}/images/generations",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )

        data = body.get("data")
        image = data[0] if isinstance(data, list) and data else None
        if not isinstance(image, dict):
            raise ImageGenerationError("xai response contains no generated image")
        if image.get("b64_json"):
            return _decode_base64(image["b64_json"], self.name)
        if image.get("url"):
            return self._download_image(image["url"])
        raise ImageGenerationError("xai response contains no generated image")


class ArkProvider(ImageProvider):
    name = "ark"

    def generate(self, model: str, prompt: str, size: str | None) -> bytes:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "response_format": "b64_json",
            "watermark": False,
        }
        mapped_size = _ark_size(size)
        if mapped_size:
            payload["size"] = mapped_size

        body = self._request_json(
            "POST",
            f"{self.api_base}/images/generations",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        data = body.get("data")
        image = data[0] if isinstance(data, list) and data else None
        if not isinstance(image, dict):
            raise ImageGenerationError("ark response contains no generated image")
        if image.get("b64_json"):
            return _decode_base64(image["b64_json"], self.name)
        if image.get("url"):
            return self._download_image(image["url"])
        raise ImageGenerationError("ark response contains no generated image")


class AliyunProvider(ImageProvider):
    name = "aliyun"
    synchronous_prefixes = ("qwen-image", "z-image")

    def generate(self, model: str, prompt: str, size: str | None) -> bytes:
        if model.startswith(self.synchronous_prefixes):
            return self._generate_synchronously(model, prompt, size)
        if model.startswith("wan"):
            return self._generate_asynchronously(model, prompt, size)
        raise ImageGenerationError(
            "unsupported aliyun image model; expected a qwen-image, wan, or z-image model"
        )

    def _generate_synchronously(self, model: str, prompt: str, size: str | None) -> bytes:
        body = self._request_json(
            "POST",
            f"{self.api_base}/services/aigc/multimodal-generation/generation",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=self._payload(model, prompt, size),
        )
        self._raise_api_error(body)
        image_url = self._image_url(body)
        if not image_url:
            raise ImageGenerationError("aliyun response contains no generated image")
        return self._download_image(image_url)

    def _generate_asynchronously(self, model: str, prompt: str, size: str | None) -> bytes:
        body = self._request_json(
            "POST",
            f"{self.api_base}/services/aigc/image-generation/generation",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-DashScope-Async": "enable",
            },
            json=self._payload(model, prompt, size),
        )
        self._raise_api_error(body)
        output = body.get("output") or {}
        task_id = output.get("task_id") if isinstance(output, dict) else None
        if not task_id:
            raise ImageGenerationError("aliyun response contains no task id")
        return self._poll(task_id)

    def _poll(self, task_id: str) -> bytes:
        max_wait = float(os.getenv("IMAGE_GENERATION_MAX_WAIT", DEFAULT_MAX_WAIT))
        deadline = time.monotonic() + max_wait
        interval = float(os.getenv("IMAGE_GENERATION_POLL_INTERVAL", DEFAULT_POLL_INTERVAL))
        headers = {"Authorization": f"Bearer {self.api_key}"}

        while time.monotonic() < deadline:
            time.sleep(interval + random.uniform(0, min(1.0, interval / 4)))
            body = self._request_json(
                "GET",
                f"{self.api_base}/tasks/{quote(task_id, safe='')}",
                headers=headers,
            )
            self._raise_api_error(body)
            output = body.get("output") or {}
            status = output.get("task_status") if isinstance(output, dict) else None
            if status == "SUCCEEDED":
                image_url = self._image_url(body)
                if not image_url:
                    raise ImageGenerationError("aliyun completed without an image URL")
                return self._download_image(image_url)
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                raise ImageGenerationError(
                    f"aliyun generation failed: {_error_message(output)}"
                )

        raise ImageGenerationError(f"aliyun generation timed out after {max_wait:g} seconds")

    @staticmethod
    def _payload(model: str, prompt: str, size: str | None) -> dict[str, Any]:
        parameters: dict[str, Any] = {"n": 1}
        mapped_size = _aliyun_size(model, size)
        if mapped_size:
            parameters["size"] = mapped_size
        return {
            "model": model,
            "input": {
                "messages": [{
                    "role": "user",
                    "content": [{"text": prompt}],
                }]
            },
            "parameters": parameters,
        }

    @staticmethod
    def _image_url(body: dict[str, Any]) -> str | None:
        try:
            content = body["output"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        for item in content:
            if isinstance(item, dict) and item.get("image"):
                return item["image"]
        return None

    @staticmethod
    def _raise_api_error(body: dict[str, Any]) -> None:
        if body.get("code"):
            raise ImageGenerationError(f"aliyun API error: {_error_message(body)}")


class AsyncGatewayProvider(ImageProvider, ABC):
    """Shared implementation for OpenAI-like asynchronous image gateways."""

    submit_path = "/images/generations"
    requires_size = False

    def generate(self, model: str, prompt: str, size: str | None) -> bytes:
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
        _apply_size(payload, size, ratio_field="size")
        if self.requires_size and "size" not in payload:
            payload["size"] = "auto"
        body = self._request_json(
            "POST",
            f"{self.api_base}{self.submit_path}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        task_id = self._task_id(body)
        if not task_id:
            raise ImageGenerationError(f"{self.name} response contains no task id")
        return self._poll(task_id)

    @abstractmethod
    def _task_id(self, body: dict[str, Any]) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def _task_url(self, task_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def _task_result(self, body: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
        """Return status, image URL, and error message."""
        raise NotImplementedError

    def _poll(self, task_id: str) -> bytes:
        max_wait = float(os.getenv("IMAGE_GENERATION_MAX_WAIT", DEFAULT_MAX_WAIT))
        deadline = time.monotonic() + max_wait
        interval = float(os.getenv("IMAGE_GENERATION_POLL_INTERVAL", DEFAULT_POLL_INTERVAL))
        headers = {"Authorization": f"Bearer {self.api_key}"}

        while time.monotonic() < deadline:
            time.sleep(interval + random.uniform(0, min(1.0, interval / 4)))
            body = self._request_json("GET", self._task_url(task_id), headers=headers)
            status, image_url, error = self._task_result(body)
            if status == "completed":
                if not image_url:
                    raise ImageGenerationError(f"{self.name} completed without an image URL")
                return self._download_image(image_url)
            if status in {"failed", "cancelled"}:
                raise ImageGenerationError(f"{self.name} generation failed: {error or 'unknown error'}")

        raise ImageGenerationError(f"{self.name} generation timed out after {max_wait:g} seconds")


class APIMartProvider(AsyncGatewayProvider):
    name = "apimart"

    def _task_id(self, body: dict[str, Any]) -> str | None:
        data = body.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0].get("task_id")
        return None

    def _task_url(self, task_id: str) -> str:
        return f"{self.api_base}/tasks/{quote(task_id, safe='')}"

    def _task_result(self, body: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
        data = body.get("data") or {}
        status = data.get("status") if isinstance(data, dict) else None
        image_url = None
        try:
            url = data["result"]["images"][0]["url"]
            image_url = url[0] if isinstance(url, list) else url
        except (KeyError, IndexError, TypeError):
            pass
        return status, image_url, _error_message(data)


class ToAPIsProvider(AsyncGatewayProvider):
    name = "toapis"
    requires_size = True

    def _task_id(self, body: dict[str, Any]) -> str | None:
        return body.get("id")

    def _task_url(self, task_id: str) -> str:
        return f"{self.api_base}/images/generations/{quote(task_id, safe='')}"

    def _task_result(self, body: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
        image_url = None
        try:
            image_url = body["result"]["data"][0]["url"]
        except (KeyError, IndexError, TypeError):
            pass
        return body.get("status"), image_url, _error_message(body)


PROVIDER_CONFIG = {
    "aliyun": (AliyunProvider, "DASHSCOPE_API_KEY", "ALIYUN_API_BASE", "https://dashscope.aliyuncs.com/api/v1"),
    "ark": (ArkProvider, "ARK_API_KEY", "ARK_API_BASE", "https://ark.cn-beijing.volces.com/api/v3"),
    "apimart": (APIMartProvider, "APIMART_API_KEY", "APIMART_API_BASE", "https://api.apimart.ai/v1"),
    "toapis": (ToAPIsProvider, "TOAPIS_API_KEY", "TOAPIS_API_BASE", "https://toapis.com/v1"),
    "gemini": (GeminiProvider, "GEMINI_API_KEY", "GEMINI_API_BASE", "https://generativelanguage.googleapis.com"),
    "xai": (XAIProvider, "X_AI_API_KEY", "X_AI_API_BASE", "https://api.x.ai/v1"),
}


def get_provider(name: str) -> ImageProvider:
    normalized = name.strip().lower()
    config = PROVIDER_CONFIG.get(normalized)
    if not config:
        supported = ", ".join(sorted(PROVIDER_CONFIG))
        raise ValueError(f"unsupported provider '{name}'; supported providers: {supported}")
    provider_class, key_env, base_env, default_base = config
    return provider_class(os.getenv(key_env, ""), os.getenv(base_env, default_base))


def generate_image(provider: str, model: str, prompt: str, size: str | None = None) -> bytes:
    return get_provider(provider).generate(model=model, prompt=prompt, size=size)


def _create_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        # Retrying generation POSTs can create duplicate billable tasks.
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def _apply_size(payload: dict[str, Any], size: str | None, ratio_field: str) -> None:
    if not size:
        return
    if size.lower() in {"0.5k", "1k", "2k", "4k"}:
        payload["resolution"] = size
    else:
        payload[ratio_field] = size


ARK_2K_RATIOS = {
    "1:1": "2048x2048",
    "4:3": "2304x1728",
    "3:4": "1728x2304",
    "16:9": "2848x1600",
    "9:16": "1600x2848",
    "3:2": "2496x1664",
    "2:3": "1664x2496",
    "21:9": "3136x1344",
}


def _ark_size(size: str | None) -> str | None:
    if not size:
        return None
    return ARK_2K_RATIOS.get(size, size.replace("*", "x"))


ALIYUN_1K_RATIOS = {
    "1:1": "1024*1024",
    "2:3": "832*1248",
    "3:2": "1248*832",
    "3:4": "864*1152",
    "4:3": "1152*864",
    "9:16": "720*1280",
    "16:9": "1280*720",
    "21:9": "1344*576",
}

ALIYUN_2K_RATIOS = {
    "1:1": "2048*2048",
    "16:9": "2688*1536",
    "9:16": "1536*2688",
    "4:3": "2368*1728",
    "3:4": "1728*2368",
}


def _aliyun_size(model: str, size: str | None) -> str | None:
    if not size:
        return None
    normalized = size.upper()
    if model.startswith("wan"):
        if normalized in {"1K", "2K", "4K"} or "*" in size:
            return normalized
        return ALIYUN_2K_RATIOS.get(size, size)
    if normalized in {"0.5K", "1K", "2K", "4K"}:
        pixels = {"0.5K": "512*512", "1K": "1024*1024", "2K": "2048*2048", "4K": "4096*4096"}
        return pixels[normalized]
    return ALIYUN_1K_RATIOS.get(size, size)


def _gemini_size(size: str | None) -> dict[str, str]:
    if not size:
        return {}
    if size.lower() in {"0.5k", "1k", "2k", "4k"}:
        return {"imageSize": size.upper()}
    return {"aspectRatio": size}


def _decode_base64(value: str, provider: str) -> bytes:
    try:
        image = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ImageGenerationError(f"{provider} returned invalid base64 image data") from exc
    if not image:
        raise ImageGenerationError(f"{provider} returned an empty image")
    if len(image) > MAX_IMAGE_BYTES:
        raise ImageGenerationError(f"{provider} image exceeds the 32 MB limit")
    return image


def _error_message(body: Any) -> str:
    if not isinstance(body, dict):
        return "unknown error"
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "unknown error")
    if error:
        return str(error)
    return str(body.get("message") or "unknown error")
