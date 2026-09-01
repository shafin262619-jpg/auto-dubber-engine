"""Async wrapper around the google-genai SDK with automatic key rotation.

Every generation call pulls the next healthy key from the :class:`KeyManager`
pool. If the SDK raises a 429/quota error, the key is marked failed and the
request is retried immediately with the next healthy key in the pool.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import List, Optional, Sequence

from google import genai
from google.genai import types

from .key_manager import (
    AllKeysExhaustedError,
    KeyManager,
    STATUS_EXHAUSTED,
    STATUS_RATE_LIMITED,
    is_model_error,
    is_rate_limit_error,
)


class GeminiGenerationError(RuntimeError):
    """Raised when a Gemini request fails in a non-rotatable way."""


class GeminiDriver:
    """Async client for Gemini generation with automatic key rotation."""

    def __init__(
        self,
        key_manager: KeyManager,
        fallback_models: Optional[List[str]] = None,
    ) -> None:
        self.key_manager: KeyManager = key_manager
        self.fallback_models: List[str] = list(fallback_models or [])

    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        file_paths: Optional[Sequence[str]] = None,
        system_instruction: Optional[str] = None,
        max_output_tokens: int = 8192,
    ) -> str:
        """Generate text for *prompt*, optionally with local *file_paths*.

        On 429/quota errors the request retries with the next healthy key.
        If a key's project lacks the requested model (404 not supported), the
        driver falls back to that key's known working model or the fallback
        model list before rotating.
        """
        model = model or self.key_manager.default_model
        contents = self._build_contents(prompt, file_paths)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            max_output_tokens=max_output_tokens,
        )

        while True:
            record = self.key_manager.next_key()
            client = genai.Client(api_key=record.key)
            candidates = self._candidate_models(model, record)

            rotated = False
            last_model_error: Optional[Exception] = None
            for candidate in candidates:
                try:
                    response = await client.aio.models.generate_content(
                        model=candidate, contents=contents, config=config
                    )
                except Exception as exc:
                    if is_rate_limit_error(exc):
                        self.key_manager.mark_failed(
                            record, STATUS_RATE_LIMITED, f"{type(exc).__name__}: {exc}"
                        )
                        rotated = True  # rotate to the next healthy key
                        break
                    if is_model_error(exc):
                        last_model_error = exc
                        continue  # try the next candidate model on this key
                    raise GeminiGenerationError(f"{type(exc).__name__}: {exc}") from exc

                self.key_manager.remember_working_model(record, candidate)
                self.key_manager.bump_usage(record)
                if not response.text:
                    raise GeminiGenerationError("Gemini returned an empty response.")
                return response.text

            if rotated:
                continue  # next key in the pool

            if last_model_error is not None:
                # The key could not serve any candidate model.
                self.key_manager.mark_failed(
                    record,
                    STATUS_EXHAUSTED,
                    f"{type(last_model_error).__name__}: {last_model_error}",
                )

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _candidate_models(self, requested: str, record) -> List[str]:
        """Ordered, de-duplicated list of models to try for a key."""
        candidates: List[str] = []
        for name in (requested, record.working_model or "", *self.fallback_models):
            if name and name not in candidates:
                candidates.append(name)
        return candidates

    @staticmethod
    def _build_contents(
        prompt: str, file_paths: Optional[Sequence[str]]
    ) -> List[types.Content]:
        """Build the contents payload: file parts first, then the text prompt."""
        parts: List[types.Part] = []
        for raw_path in file_paths or []:
            path = Path(raw_path)
            if not path.is_file():
                raise FileNotFoundError(f"File not found for Gemini: {path}")
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        parts.append(types.Part.from_text(text=prompt))
        return [types.Content(role="user", parts=parts)]
