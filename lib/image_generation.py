"""Provider adapters for the unified image generation API."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import math
import os
import random
import socket
import time
from abc import ABC, abstractmethod
from io import BytesIO
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunsplit

import requests
import urllib3
from PIL import Image, UnidentifiedImageError
from requests.adapters import HTTPAdapter
from urllib3 import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.util.retry import Retry


DEFAULT_TIMEOUT = 120
DEFAULT_MAX_WAIT = 300
DEFAULT_POLL_INTERVAL = 5.0
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_INPUT_IMAGE_BYTES = 20 * 1024 * 1024
MAX_INPUT_IMAGES = 3
MAX_IMAGE_REDIRECTS = 3
COMMON_INPUT_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


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
    def generate(
        self,
        model: str,
        prompt: str,
        size: str | None,
        images: list[str] | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> bytes:
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
            with self.session.get(url, timeout=DEFAULT_TIMEOUT, stream=True) as response:
                response.raise_for_status()
                content_length = _content_length(response.headers, self.name)
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

    def _inline_image(self, source: str) -> dict[str, Any]:
        data, mime_type = _load_input_image(source, self.name)
        return {
            "inline_data": {
                "mime_type": mime_type,
                "data": base64.b64encode(data).decode("ascii"),
            }
        }


class GeminiProvider(ImageProvider):
    name = "gemini"

    def generate(
        self,
        model: str,
        prompt: str,
        size: str | None,
        images: list[str] | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> bytes:
        images = images or []
        generation_config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        image_config = _gemini_size(size, aspect_ratio, resolution)
        if image_config:
            generation_config["imageConfig"] = image_config

        instruction = (
            f"Edit or generate an image using the provided reference image(s): {prompt}"
            if images
            else f"Generate an image: {prompt}"
        )
        parts = [{"text": instruction}, *(self._inline_image(image) for image in images)]

        body = self._request_json(
            "POST",
            f"{self.api_base}/v1beta/models/{quote(model, safe='')}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": generation_config,
            },
        )

        try:
            parts = body["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ImageGenerationError(
                f"gemini response contains no generated image: {_gemini_failure_message(body)}"
            ) from exc

        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                return _decode_base64(inline["data"], self.name)
        raise ImageGenerationError(
            f"gemini response contains no generated image: {_gemini_failure_message(body)}"
        )


class XAIProvider(ImageProvider):
    name = "xai"

    def generate(
        self,
        model: str,
        prompt: str,
        size: str | None,
        images: list[str] | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> bytes:
        images = images or []
        # Returning the image inline avoids a second request to xAI's temporary
        # image CDN. Some hosting-provider egress IPs are rejected by that CDN
        # even though the generation request itself succeeds.
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "response_format": "b64_json",
        }
        _apply_size(
            payload,
            size,
            ratio_field="aspect_ratio",
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        path = "/images/generations"
        if images:
            path = "/images/edits"
            image_items = [{"type": "image_url", "url": image} for image in images]
            if len(image_items) == 1:
                payload["image"] = image_items[0]
            else:
                payload["images"] = image_items
        body = self._request_json(
            "POST",
            f"{self.api_base}{path}",
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

    def generate(
        self,
        model: str,
        prompt: str,
        size: str | None,
        images: list[str] | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> bytes:
        images = images or []
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "response_format": "b64_json",
            "watermark": False,
        }
        mapped_size = _ark_size(size, aspect_ratio, resolution)
        if mapped_size:
            payload["size"] = mapped_size
        if images:
            payload["image"] = images

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

    def generate(
        self,
        model: str,
        prompt: str,
        size: str | None,
        images: list[str] | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> bytes:
        images = images or []
        if images and (
            model.startswith("z-image") or model == "wan2.5-t2i-preview"
        ):
            raise ImageGenerationError(
                f"aliyun model {model} does not support input images"
            )
        if model.startswith(self.synchronous_prefixes):
            return self._generate_synchronously(
                model, prompt, size, images, aspect_ratio, resolution
            )
        if model.startswith("wan"):
            return self._generate_asynchronously(
                model, prompt, size, images, aspect_ratio, resolution
            )
        raise ImageGenerationError(
            "unsupported aliyun image model; expected a qwen-image, wan, or z-image model"
        )

    def _generate_synchronously(
        self,
        model: str,
        prompt: str,
        size: str | None,
        images: list[str],
        aspect_ratio: str | None,
        resolution: str | None,
    ) -> bytes:
        body = self._request_json(
            "POST",
            f"{self.api_base}/services/aigc/multimodal-generation/generation",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=self._payload(
                model, prompt, size, images, aspect_ratio, resolution
            ),
        )
        self._raise_api_error(body)
        image_url = self._image_url(body)
        if not image_url:
            raise ImageGenerationError("aliyun response contains no generated image")
        return self._download_image(image_url)

    def _generate_asynchronously(
        self,
        model: str,
        prompt: str,
        size: str | None,
        images: list[str],
        aspect_ratio: str | None,
        resolution: str | None,
    ) -> bytes:
        body = self._request_json(
            "POST",
            f"{self.api_base}/services/aigc/image-generation/generation",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-DashScope-Async": "enable",
            },
            json=self._payload(
                model, prompt, size, images, aspect_ratio, resolution
            ),
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
    def _payload(
        model: str,
        prompt: str,
        size: str | None,
        images: list[str] | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {"n": 1}
        mapped_size = _aliyun_size(model, size, aspect_ratio, resolution)
        if mapped_size:
            parameters["size"] = mapped_size
        content = [{"image": image} for image in (images or [])]
        content.append({"text": prompt})
        return {
            "model": model,
            "input": {
                "messages": [{
                    "role": "user",
                    "content": content,
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

    input_images_field = "image_urls"
    public_image_urls_only = False

    def generate(
        self,
        model: str,
        prompt: str,
        size: str | None,
        images: list[str] | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> bytes:
        images = images or []
        if self.public_image_urls_only and any(not _is_http_url(image) for image in images):
            raise ImageGenerationError(
                f"{self.name} input images must use public http(s) URLs"
            )
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
        _apply_size(
            payload,
            size,
            ratio_field="size",
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        if "size" not in payload and self._use_auto_size(model, images):
            payload["size"] = "auto"
        self._normalize_payload(model, payload)
        if images:
            payload[self.input_images_field] = images
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

    def _use_auto_size(self, model: str, images: list[str]) -> bool:
        return False

    def _normalize_payload(self, model: str, payload: dict[str, Any]) -> None:
        pass

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

    def _use_auto_size(self, model: str, images: list[str]) -> bool:
        # GPT Image editing inherits the source dimensions only when size is omitted.
        return not images or not model.lower().startswith("gpt-image-2")

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
    public_image_urls_only = True

    def _normalize_payload(self, model: str, payload: dict[str, Any]) -> None:
        if not model.startswith("gpt-image-2") and "resolution" in payload:
            payload["metadata"] = {"resolution": payload.pop("resolution")}

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


IMAGE_MODEL_CATALOG_UPDATED_AT = "2026-09-01"

IMAGE_MODEL_CATALOG = {
    "aliyun": {
        "models": [
            "qwen-image-3.0-pro",
            "qwen-image-3.0",
            "wan2.7-image-pro",
            "wan2.7-image",
            "wan2.6-image",
            "wan2.6-t2i",
            "wan2.5-t2i-preview",
            "z-image-turbo",
        ],
        "note": "Model availability depends on the Alibaba Cloud region and workspace.",
    },
    "ark": {
        "models": [
            "doubao-seedream-5-0-260128",
            "doubao-seedream-5-0-lite-260128",
            "doubao-seedream-4-5-251128",
            "doubao-seedream-4-0-250828",
        ],
        "note": "Ark Endpoint IDs are also accepted and may be used instead of public Model IDs.",
    },
    "apimart": {
        "models": [
            "gpt-image-2",
            "gpt-image-2-ext",
            "gemini-3.1-flash-image-preview",
            "gemini-3.1-flash-image-preview-official",
            "nano-banana-2-ext",
            "nano-banana-2",
            "gemini-3-pro-image-preview",
            "gemini-3-pro-image-preview-official",
            "nano-banana-pro-ext",
            "nano-banana-pro",
        ],
    },
    "toapis": {
        "models": [
            "gpt-image-2",
            "gemini-3.1-flash-image-preview",
            "gemini-3-pro-image-preview",
            "nano-banana-pro",
        ],
    },
    "gemini": {
        "models": [
            "gemini-3.1-flash-image",
            "gemini-3-pro-image",
            "gemini-3.1-flash-image-preview",
            "gemini-3-pro-image-preview",
        ],
        "note": "Preview IDs are retained as legacy aliases; availability depends on the API key and region.",
    },
    "xai": {
        "models": [
            "grok-imagine-image-2.0",
            "grok-imagine-image",
        ],
        "note": "grok-imagine-image is retained as a legacy alias; prefer grok-imagine-image-2.0.",
    },
}


PROVIDER_CONFIG = {
    "aliyun": (AliyunProvider, "DASHSCOPE_API_KEY", "ALIYUN_API_BASE", "https://dashscope.aliyuncs.com/api/v1"),
    "ark": (ArkProvider, "ARK_API_KEY", "ARK_API_BASE", "https://ark.cn-beijing.volces.com/api/v3"),
    "apimart": (APIMartProvider, "APIMART_API_KEY", "APIMART_API_BASE", "https://api.apimart.ai/v1"),
    "toapis": (ToAPIsProvider, "TOAPIS_API_KEY", "TOAPIS_API_BASE", "https://toapis.com/v1"),
    "gemini": (GeminiProvider, "GEMINI_API_KEY", "GEMINI_API_BASE", "https://generativelanguage.googleapis.com"),
    "xai": (XAIProvider, "X_AI_API_KEY", "X_AI_API_BASE", "https://api.x.ai/v1"),
}


def get_image_model_catalog() -> dict[str, Any]:
    providers = []
    for provider_id in PROVIDER_CONFIG:
        catalog = IMAGE_MODEL_CATALOG.get(provider_id, {"models": []})
        item = {
            "provider": provider_id,
            "models": list(catalog.get("models", [])),
        }
        if catalog.get("note"):
            item["note"] = catalog["note"]
        providers.append(item)
    return {
        "updated_at": IMAGE_MODEL_CATALOG_UPDATED_AT,
        "providers": providers,
    }


def get_provider(name: str) -> ImageProvider:
    normalized = name.strip().lower()
    config = PROVIDER_CONFIG.get(normalized)
    if not config:
        supported = ", ".join(sorted(PROVIDER_CONFIG))
        raise ValueError(f"unsupported provider '{name}'; supported providers: {supported}")
    provider_class, key_env, base_env, default_base = config
    return provider_class(os.getenv(key_env, ""), os.getenv(base_env, default_base))


def generate_image(
    provider: str,
    model: str,
    prompt: str,
    size: str | None = None,
    images: list[str] | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
) -> bytes:
    normalized_images = normalize_input_images(images, provider=provider, model=model)
    return generate_normalized_image(
        provider,
        model,
        prompt,
        size,
        normalized_images,
        aspect_ratio,
        resolution,
    )


def generate_normalized_image(
    provider: str,
    model: str,
    prompt: str,
    size: str | None,
    images: list[str],
    aspect_ratio: str | None = None,
    resolution: str | None = None,
) -> bytes:
    """Generate using image references already validated at the HTTP boundary."""
    size, aspect_ratio = normalize_image_aspect_ratio(
        provider, model, size, aspect_ratio
    )
    return get_provider(provider).generate(
        model=model,
        prompt=prompt,
        size=size,
        images=images,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )


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


# Keep the legacy classifier byte-for-byte compatible with the original public
# `size` behavior. Provider-specific tiers such as Ark's 1.5K and 3K should use
# the explicit `resolution` field in new requests.
RESOLUTION_TIERS = frozenset({"0.5k", "1k", "2k", "4k"})
SUPPORTED_ASPECT_RATIOS = (
    "1:1", "1:2", "1:3", "1:4", "1:8", "2:1", "2:3", "2.35:1",
    "3:1", "3:2", "3:4", "4:1", "4:3", "4:5", "5:2", "5:4", "8:1",
    "9:16", "9:19.5", "9:20", "9:21", "16:9", "19.5:9", "20:9", "21:9",
)
COMMON_PROVIDER_RATIOS = frozenset({
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
})
EXTREME_GEMINI_RATIOS = COMMON_PROVIDER_RATIOS | {"1:4", "1:8", "4:1", "8:1"}
APIMART_GPT_RATIOS = frozenset({
    "1:1", "1:2", "1:3", "2:1", "2:3", "3:1", "3:2", "3:4", "4:3",
    "4:5", "5:4", "9:16", "9:21", "16:9", "21:9",
})
TOAPIS_GPT_RATIOS = frozenset({
    "1:1", "1:2", "2:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
    "9:16", "9:21", "16:9", "21:9",
})
ARK_RATIOS = frozenset({"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"})
XAI_RATIOS = frozenset({
    "1:1", "1:2", "2:1", "2:3", "3:2", "3:4", "4:3", "5:2", "9:16",
    "9:19.5", "9:20", "16:9", "19.5:9", "20:9", "21:9",
})


def normalize_image_aspect_ratio(
    provider: str,
    model: str,
    size: str | None,
    aspect_ratio: str | None,
) -> tuple[str | None, str | None]:
    """Map public ratios to the perceptually closest ratio supported upstream."""
    provider = provider.strip().lower()
    model = model.strip().lower()
    if aspect_ratio:
        aspect_ratio = _closest_supported_ratio(provider, model, aspect_ratio)
    if size and ":" in size and size.lower() not in RESOLUTION_TIERS:
        size = _closest_supported_ratio(provider, model, size)
    return size, aspect_ratio


def _closest_supported_ratio(provider: str, model: str, ratio: str) -> str:
    if ratio not in SUPPORTED_ASPECT_RATIOS:
        supported = ", ".join(SUPPORTED_ASPECT_RATIOS)
        raise ImageGenerationError(f"unsupported aspect ratio '{ratio}'; use one of: {supported}")
    provider_ratios = _provider_aspect_ratios(provider, model)
    if ratio in provider_ratios:
        return ratio
    target = _ratio_value(ratio)
    candidates = [item for item in SUPPORTED_ASPECT_RATIOS if item in provider_ratios]
    return min(
        candidates,
        key=lambda candidate: abs(math.log(_ratio_value(candidate) / target)),
    )


def _provider_aspect_ratios(provider: str, model: str) -> frozenset[str]:
    normalized_model = model.lower()
    if provider == "gemini":
        return EXTREME_GEMINI_RATIOS if "3.1-flash-image" in normalized_model else COMMON_PROVIDER_RATIOS
    if provider == "apimart":
        if normalized_model.startswith("gpt-image-2"):
            return APIMART_GPT_RATIOS
        if "3.1-flash-image" in normalized_model or "nano-banana-2" in normalized_model:
            return EXTREME_GEMINI_RATIOS
        return COMMON_PROVIDER_RATIOS
    if provider == "toapis":
        if normalized_model.startswith("gpt-image-2"):
            return TOAPIS_GPT_RATIOS
        if "3.1-flash-image" in normalized_model:
            return EXTREME_GEMINI_RATIOS
        return COMMON_PROVIDER_RATIOS
    if provider == "xai":
        return XAI_RATIOS
    if provider == "ark":
        return ARK_RATIOS
    return COMMON_PROVIDER_RATIOS


def _ratio_value(ratio: str) -> float:
    width, height = ratio.split(":", 1)
    return float(width) / float(height)


def _size_parts(
    size: str | None,
    aspect_ratio: str | None,
    resolution: str | None,
) -> tuple[str | None, str | None]:
    """Resolve legacy size into independent fields, then apply new overrides."""
    legacy_ratio = None
    legacy_resolution = None
    if size:
        if size.lower() in RESOLUTION_TIERS:
            legacy_resolution = size
        else:
            legacy_ratio = size
    return aspect_ratio or legacy_ratio, resolution or legacy_resolution


def _apply_size(
    payload: dict[str, Any],
    size: str | None,
    ratio_field: str,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
) -> None:
    resolved_ratio, resolved_resolution = _size_parts(
        size, aspect_ratio, resolution
    )
    if resolved_ratio:
        payload[ratio_field] = resolved_ratio
    if resolved_resolution:
        payload["resolution"] = resolved_resolution


def normalize_input_images(
    images: list[str] | None,
    provider: str | None = None,
    model: str | None = None,
) -> list[str]:
    """Validate and canonicalize unified input image references.

    Public HTTP(S) URLs are preserved. Base64 data URIs and bare Base64 are
    validated, size-limited, and converted to canonical data URIs so every
    provider receives an explicit MIME type.
    """
    if images is None:
        return []
    if not isinstance(images, list):
        raise ImageGenerationError("input images must be an array")
    if not images:
        return []
    if len(images) > MAX_INPUT_IMAGES:
        raise ImageGenerationError(
            f"at most {MAX_INPUT_IMAGES} input images are supported"
        )

    max_bytes = _input_image_limit(provider, model)
    max_base64_chars = ((max_bytes + 2) // 3) * 4
    max_mb = max_bytes // (1024 * 1024)
    normalized = []
    for index, source in enumerate(images, start=1):
        if not isinstance(source, str) or not source.strip():
            raise ImageGenerationError(f"input image {index} must be a non-empty string")
        source = source.strip()
        if _is_http_url(source):
            if len(source) > 8192:
                raise ImageGenerationError(f"input image {index} URL is too long")
            _resolve_public_image_addresses(source, provider or "image")
            normalized.append(source)
            continue

        encoded = source
        if source.startswith("data:"):
            header, separator, encoded = source.partition(",")
            if not separator or ";base64" not in header.lower():
                raise ImageGenerationError(
                    f"input image {index} must use a base64 data URI"
                )
        if len(encoded) > max_base64_chars:
            raise ImageGenerationError(f"input image {index} exceeds the {max_mb} MB limit")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageGenerationError(
                f"input image {index} is not a valid URL or base64 image"
            ) from exc
        mime_type = _input_image_mime_type(data)
        if mime_type not in COMMON_INPUT_IMAGE_MIME_TYPES:
            raise ImageGenerationError(f"input image {index} has an unsupported format")
        if len(data) > max_bytes:
            raise ImageGenerationError(f"input image {index} exceeds the {max_mb} MB limit")
        _verify_input_image(data, index=index)
        normalized.append(
            f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"
        )
    return normalized


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _input_image_mime_type(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in {b"II*\x00", b"MM\x00*"}:
        return "image/tiff"
    return None


def _input_image_limit(provider: str | None, model: str | None) -> int:
    if provider == "toapis":
        return 10 * 1024 * 1024
    if provider == "aliyun" and (
        (model or "").startswith("qwen-image")
        or (model or "").startswith("wan2.6")
    ):
        return 10 * 1024 * 1024
    return MAX_INPUT_IMAGE_BYTES


def _verify_input_image(
    data: bytes,
    index: int | None = None,
    provider: str | None = None,
) -> None:
    label = f"input image {index}" if index is not None else f"{provider} input image"
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageGenerationError(f"{label} is corrupt or incomplete") from exc


def _content_length(headers: Any, provider: str) -> int:
    value = headers.get("Content-Length", "0") or "0"
    try:
        length = int(value)
    except (TypeError, ValueError) as exc:
        raise ImageGenerationError(
            f"{provider} returned an invalid Content-Length header"
        ) from exc
    if length < 0:
        raise ImageGenerationError(
            f"{provider} returned an invalid Content-Length header"
        )
    return length


def _resolve_public_image_addresses(url: str, provider: str) -> tuple[Any, list[str]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageGenerationError(f"{provider} input image URL is invalid")
    if parsed.username or parsed.password:
        raise ImageGenerationError(f"{provider} input image URL must not contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ImageGenerationError(f"{provider} input image URL has an invalid port") from exc
    if port not in {None, 80, 443}:
        raise ImageGenerationError(
            f"{provider} input image URL must use port 80 or 443"
        )
    effective_port = port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            effective_port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ImageGenerationError(
            f"{provider} input image host could not be resolved"
        ) from exc
    if not addresses:
        raise ImageGenerationError(f"{provider} input image host could not be resolved")
    public_addresses = []
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ImageGenerationError(
                f"{provider} input image URL must resolve to a public address"
            )
        value = str(ip)
        if value not in public_addresses:
            public_addresses.append(value)
    return parsed, public_addresses


def _pinned_image_request(url: str, provider: str):
    parsed, addresses = _resolve_public_image_addresses(url, provider)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    default_port = 443 if parsed.scheme == "https" else 80
    host_header = parsed.hostname if port == default_port else f"{parsed.hostname}:{port}"
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    last_error = None

    for address in addresses:
        pool_class = (
            HTTPSConnectionPool if parsed.scheme == "https" else HTTPConnectionPool
        )
        kwargs: dict[str, Any] = {
            "host": address,
            "port": port,
            "timeout": DEFAULT_TIMEOUT,
            "retries": False,
        }
        if parsed.scheme == "https":
            kwargs.update(
                assert_hostname=parsed.hostname,
                cert_reqs="CERT_REQUIRED",
                server_hostname=parsed.hostname,
            )
        pool = pool_class(**kwargs)
        try:
            response = pool.request(
                "GET",
                target,
                headers={"Host": host_header, "Accept": "image/*"},
                preload_content=False,
                redirect=False,
            )
            return response, pool
        except urllib3.exceptions.HTTPError as exc:
            last_error = exc
            pool.close()

    raise ImageGenerationError(
        f"failed to download {provider} input image: {last_error or 'connection failed'}"
    )


def _load_input_image(source: str, provider: str) -> tuple[bytes, str]:
    if not _is_http_url(source):
        _, _, encoded = source.partition(",")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageGenerationError(f"{provider} input image has invalid base64 data") from exc
        mime_type = _input_image_mime_type(data)
        if mime_type not in COMMON_INPUT_IMAGE_MIME_TYPES:
            raise ImageGenerationError(f"{provider} input image has an unsupported format")
        _verify_input_image(data, provider=provider)
        return data, mime_type

    current_url = source
    for redirect_count in range(MAX_IMAGE_REDIRECTS + 1):
        response = None
        pool = None
        try:
            response, pool = _pinned_image_request(current_url, provider)
            if response.status in {301, 302, 303, 307, 308}:
                if redirect_count >= MAX_IMAGE_REDIRECTS:
                    raise ImageGenerationError(
                        f"{provider} input image exceeded the redirect limit"
                    )
                location = response.headers.get("Location")
                if not location:
                    raise ImageGenerationError(
                        f"{provider} input image redirect has no location"
                    )
                current_url = urljoin(current_url, location)
                continue
            if response.status >= 400:
                raise ImageGenerationError(
                    f"failed to download {provider} input image: HTTP {response.status}"
                )
            content_length = _content_length(response.headers, provider)
            if content_length > MAX_INPUT_IMAGE_BYTES:
                raise ImageGenerationError(f"{provider} input image exceeds the 20 MB limit")
            chunks = []
            total = 0
            for chunk in response.stream(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_INPUT_IMAGE_BYTES:
                    raise ImageGenerationError(
                        f"{provider} input image exceeds the 20 MB limit"
                    )
                chunks.append(chunk)
        except urllib3.exceptions.HTTPError as exc:
            raise ImageGenerationError(
                f"failed to download {provider} input image: {exc}"
            ) from exc
        finally:
            if response is not None:
                response.release_conn()
            if pool is not None:
                pool.close()
        data = b"".join(chunks)
        mime_type = _input_image_mime_type(data)
        if mime_type not in COMMON_INPUT_IMAGE_MIME_TYPES:
            raise ImageGenerationError(f"{provider} input image has an unsupported format")
        _verify_input_image(data, provider=provider)
        return data, mime_type

    raise ImageGenerationError(f"{provider} input image could not be downloaded")


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

ARK_RESOLUTION_SCALES = {
    "1K": 0.5,
    "1.5K": 0.75,
    "2K": 1.0,
    "3K": 1.5,
    "4K": 2.0,
}


def _ark_size(
    size: str | None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
) -> str | None:
    resolved_ratio, resolved_resolution = _size_parts(
        size, aspect_ratio, resolution
    )
    if resolved_ratio and resolved_resolution:
        base_size = ARK_2K_RATIOS.get(resolved_ratio)
        scale = ARK_RESOLUTION_SCALES.get(resolved_resolution.upper())
        if not base_size or scale is None:
            raise ImageGenerationError(
                "ark cannot combine the requested aspect_ratio and resolution; "
                "use a supported ratio and 1K, 1.5K, 2K, 3K, or 4K"
            )
        width, height = (int(value) for value in base_size.split("x"))
        return f"{_round_to_multiple(width * scale, 8)}x{_round_to_multiple(height * scale, 8)}"
    if resolved_ratio:
        return ARK_2K_RATIOS.get(resolved_ratio, resolved_ratio.replace("*", "x"))
    if not resolved_resolution:
        return None
    return resolved_resolution.replace("*", "x")


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

ALIYUN_4K_RATIOS = {
    "1:1": "4096*4096",
    "16:9": "4096*2304",
    "9:16": "2304*4096",
    "4:3": "4096*3072",
    "3:4": "3072*4096",
}

ALIYUN_WAN_T2I_1K_RATIOS = {
    "1:1": "1280*1280",
    "16:9": "1696*960",
    "9:16": "960*1696",
    "4:3": "1472*1104",
    "3:4": "1104*1472",
}


def _aliyun_size(
    model: str,
    size: str | None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
) -> str | None:
    resolved_ratio, resolved_resolution = _size_parts(
        size, aspect_ratio, resolution
    )
    if resolved_ratio and resolved_resolution:
        return _aliyun_combined_size(model, resolved_ratio, resolved_resolution)
    selected = resolved_ratio or resolved_resolution
    if not selected:
        return None
    if "*" in selected or "x" in selected.lower():
        return selected.lower().replace("x", "*")
    normalized = selected.upper()
    if model.startswith("wan"):
        if normalized in {"1K", "2K", "4K"} or "*" in selected:
            return normalized
        return ALIYUN_2K_RATIOS.get(
            selected, _ratio_dimensions(selected, 2048, "*", 16)
        )
    if normalized in {"0.5K", "1K", "2K", "4K"}:
        pixels = {"0.5K": "512*512", "1K": "1024*1024", "2K": "2048*2048", "4K": "4096*4096"}
        return pixels[normalized]
    return ALIYUN_1K_RATIOS.get(
        selected, _ratio_dimensions(selected, 1024, "*", 16)
    )


def _aliyun_combined_size(model: str, aspect_ratio: str, resolution: str) -> str:
    normalized_resolution = resolution.upper()
    if normalized_resolution == "2K" and aspect_ratio in ALIYUN_2K_RATIOS:
        return ALIYUN_2K_RATIOS[aspect_ratio]
    if normalized_resolution == "4K" and aspect_ratio in ALIYUN_4K_RATIOS:
        return ALIYUN_4K_RATIOS[aspect_ratio]
    if (
        normalized_resolution == "1K"
        and model in {"wan2.6-image", "wan2.6-t2i", "wan2.5-t2i-preview"}
        and aspect_ratio in ALIYUN_WAN_T2I_1K_RATIOS
    ):
        return ALIYUN_WAN_T2I_1K_RATIOS[aspect_ratio]
    if normalized_resolution == "1K" and aspect_ratio in ALIYUN_1K_RATIOS:
        return ALIYUN_1K_RATIOS[aspect_ratio]

    square_side = {
        "0.5K": 512,
        "1K": 1280 if model.startswith(("wan2.5", "wan2.6")) else 1024,
        "2K": 2048,
        "4K": 4096,
    }.get(normalized_resolution)
    if square_side is None:
        raise ImageGenerationError(
            "aliyun cannot combine the requested aspect_ratio and resolution; "
            "use 0.5K, 1K, 2K, or 4K"
        )
    return _ratio_dimensions(aspect_ratio, square_side, "*", 16)


def _gemini_size(
    size: str | None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
) -> dict[str, str]:
    resolved_ratio, resolved_resolution = _size_parts(
        size, aspect_ratio, resolution
    )
    config = {}
    if resolved_ratio:
        config["aspectRatio"] = resolved_ratio
    if resolved_resolution:
        config["imageSize"] = resolved_resolution.upper()
    return config


def _round_to_multiple(value: float, multiple: int) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def _ratio_dimensions(
    aspect_ratio: str,
    square_side: int,
    separator: str,
    multiple: int,
) -> str:
    try:
        width_ratio, height_ratio = (
            float(value) for value in aspect_ratio.split(":", maxsplit=1)
        )
        if width_ratio <= 0 or height_ratio <= 0:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ImageGenerationError(
            f"invalid aspect_ratio '{aspect_ratio}'; expected width:height"
        ) from exc
    ratio = width_ratio / height_ratio
    width = _round_to_multiple(square_side * math.sqrt(ratio), multiple)
    height = _round_to_multiple(square_side / math.sqrt(ratio), multiple)
    return f"{width}{separator}{height}"


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


def _gemini_failure_message(body: dict[str, Any]) -> str:
    prompt_feedback = body.get("promptFeedback") or {}
    candidates = body.get("candidates") or []
    candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    finish_reason = candidate.get("finishReason")
    finish_message = candidate.get("finishMessage")
    if finish_reason and finish_message:
        return f"{finish_reason}: {finish_message}"
    return str(
        finish_message
        or finish_reason
        or prompt_feedback.get("blockReason")
        or _error_message(body)
    )


def _error_message(body: Any) -> str:
    if not isinstance(body, dict):
        return "unknown error"
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "unknown error")
    if error:
        return str(error)
    return str(body.get("message") or "unknown error")
