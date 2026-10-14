"""OpenAI client construction with a native-extension-free Windows fallback.

Some managed Windows devices block the ``jiter`` native module used by the
official OpenAI SDK.  The application only needs the Responses and Speech
endpoints, so this module can call those endpoints with Python's standard
library when the SDK cannot be imported.  Normal installations continue to
use the official SDK.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self


def create_openai_client(*, api_key: str):
    """Return the official SDK client, or a REST fallback if its DLL is blocked."""
    try:
        from openai import OpenAI

        return OpenAI(api_key=api_key)
    except (ImportError, OSError) as exc:
        if "jiter" not in str(exc).lower() and "dll load failed" not in str(exc).lower():
            raise
        return _OpenAIRestClient(api_key=api_key)


class _OpenAIRestClient:
    def __init__(self, *, api_key: str) -> None:
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self._transport = _OpenAITransport(api_key=api_key, base_url=base_url)
        self.responses = _ResponsesResource(self._transport)
        self.audio = _AudioResource(self._transport)


class _OpenAITransport:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url

    def request(self, endpoint: str, payload: dict[str, Any], *, accept: str) -> bytes:
        request = urllib.request.Request(
            f"{self.base_url}/{endpoint.lstrip('/')}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": accept,
                "User-Agent": "professor-ai-workspace/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-1200:]
            raise RuntimeError(f"OpenAI request failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not connect to OpenAI: {exc.reason}") from exc


class _ResponsesResource:
    def __init__(self, transport: _OpenAITransport) -> None:
        self._transport = transport

    def create(self, **payload: Any) -> SimpleNamespace:
        raw = self._transport.request("responses", payload, accept="application/json")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("OpenAI returned an unreadable response.") from exc
        return SimpleNamespace(
            status=data.get("status"),
            incomplete_details=data.get("incomplete_details"),
            output_text=_response_output_text(data),
            raw=data,
        )


def _response_output_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    parts: list[str] = []
    for output in data.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if content.get("type") in {"output_text", "text"} and isinstance(text, str):
                parts.append(text)
    return "".join(parts)


class _AudioResource:
    def __init__(self, transport: _OpenAITransport) -> None:
        self.speech = _SpeechResource(transport)


class _SpeechResource:
    def __init__(self, transport: _OpenAITransport) -> None:
        self.with_streaming_response = _StreamingSpeechResource(transport)


class _StreamingSpeechResource:
    def __init__(self, transport: _OpenAITransport) -> None:
        self._transport = transport

    def create(self, **payload: Any) -> _BinaryResponse:
        audio = self._transport.request("audio/speech", payload, accept="audio/wav")
        if not audio:
            raise RuntimeError("OpenAI returned an empty speech response.")
        return _BinaryResponse(audio)


class _BinaryResponse:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def stream_to_file(self, path: str | Path) -> None:
        Path(path).write_bytes(self._content)
