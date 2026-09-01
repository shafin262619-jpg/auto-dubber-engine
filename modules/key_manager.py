"""Gemini API key management: persistence, health testing and round-robin rotation.

The key pool is persisted to ``config/api_keys.json`` as a list of records:

    [{"key": "AIzaSy...", "status": "active|429|exhausted", "usage_count": 0,
      "last_tested": "ISO-8601 timestamp", "last_error": ""}]

Drivers call :meth:`KeyManager.next_key` to obtain the next healthy key and
:meth:`KeyManager.mark_failed` on 429/quota errors so the next attempt
automatically rotates to a different key.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from google import genai
from google.genai import types

STATUS_ACTIVE = "active"
STATUS_RATE_LIMITED = "429"
STATUS_EXHAUSTED = "exhausted"

_ALL_STATUSES: tuple[str, ...] = (STATUS_ACTIVE, STATUS_RATE_LIMITED, STATUS_EXHAUSTED)

_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "429",
    "too many requests",
    "quota exceeded",
    "resource exhausted",
    "rate limit",
)

_AUTH_MARKERS: tuple[str, ...] = (
    "api key",
    "invalid key",
    "forbidden",
    "permission denied",
    "unauthorized",
    "authentication",
    "access not allowed",
)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_rate_limit_error(exc: Exception) -> bool:
    """Return True if *exc* (typically a google.genai GoogleAPIError) is a 429/quota error."""
    code = getattr(exc, "code", None)
    if code == 429:
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _RATE_LIMIT_MARKERS)


def is_auth_error(exc: Exception) -> bool:
    """Return True if *exc* is an authentication/permission error for the API key."""
    code = getattr(exc, "code", None)
    if code in (400, 401, 403):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _AUTH_MARKERS)


def is_model_error(exc: Exception) -> bool:
    """Return True if *exc* means the requested model is unavailable for the key's project.

    Typical for new ``AQ.``-style keys whose project only has certain models enabled.
    """
    code = getattr(exc, "code", None)
    if code in (404,):
        return True
    message = str(exc).lower()
    return ("model" in message) and any(
        marker in message
        for marker in ("not found", "not supported", "not enabled", "does not exist", "not available")
    )


@dataclass
class APIKeyRecord:
    """One Gemini API key with its health/usage metadata."""

    key: str
    status: str = STATUS_ACTIVE
    usage_count: int = 0
    last_tested: str = ""
    last_error: str = ""
    working_model: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        """Serialize to the persisted JSON shape."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "APIKeyRecord":
        """Deserialize from a JSON record, tolerating missing fields."""
        status = str(data.get("status", STATUS_ACTIVE))
        if status not in _ALL_STATUSES:
            status = STATUS_ACTIVE
        return cls(
            key=str(data.get("key", "")),
            status=status,
            usage_count=max(0, int(data.get("usage_count", 0))),
            last_tested=str(data.get("last_tested", "")),
            last_error=str(data.get("last_error", "")),
            working_model=str(data.get("working_model", "")) or None,
        )

    def mask(self) -> str:
        """Return a display-safe masked form of the key."""
        if len(self.key) <= 10:
            return "***"
        return f"{self.key[:8]}...{self.key[-4:]}"


class AllKeysExhaustedError(RuntimeError):
    """Raised when every key in the pool is unhealthy and no rotation is possible."""


class KeyManager:
    """Owns the API key pool, health checks and round-robin rotation."""

    def __init__(
        self,
        config_path: Path,
        test_model: str = "gemini-3.6-flash",
        test_models: Optional[List[str]] = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.default_model: str = test_model
        self.test_models: List[str] = list(
            test_models
            or [
                test_model,
                "gemini-3.5-flash",
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-1.5-flash",
            ]
        )
        self.keys: List[APIKeyRecord] = []
        self._rr_index: int = 0
        self.load_keys()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def load_keys(self) -> None:
        """Load the key pool from disk. Corrupt/missing files yield an empty pool."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.keys = []
            self.save_keys()
            return
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            records = [APIKeyRecord.from_dict(item) for item in raw if isinstance(item, dict)]
            self.keys = [r for r in records if r.key.strip()]
        except (json.JSONDecodeError, OSError, ValueError):
            self.keys = []

    def save_keys(self) -> None:
        """Persist the current key pool to disk atomically."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload: List[Dict[str, object]] = [record.to_dict() for record in self.keys]
        tmp_path = self.config_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self.config_path)

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #

    def set_keys(self, raw: str) -> int:
        """Add keys pasted one-per-line, skipping duplicates. Returns number added."""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        known = {record.key for record in self.keys}
        added = 0
        for line in lines:
            if line not in known and (
                line.lower().startswith("ai") or line.lower().startswith("aq")
            ):
                self.keys.append(APIKeyRecord(key=line))
                known.add(line)
                added += 1
        self.save_keys()
        return added

    def clear_keys(self) -> None:
        """Remove every key from the pool."""
        self.keys = []
        self.save_keys()

    def remove_key(self, key: str) -> bool:
        """Remove a single key by value. Returns True when it existed."""
        before = len(self.keys)
        self.keys = [record for record in self.keys if record.key != key]
        changed = len(self.keys) != before
        if changed:
            self.save_keys()
        return changed

    def reset_key(self, key: str) -> bool:
        """Reset a single key to ``active``. Returns True when it existed."""
        for record in self.keys:
            if record.key == key:
                record.status = STATUS_ACTIVE
                record.last_error = ""
                self.save_keys()
                return True
        return False

    # ------------------------------------------------------------------ #
    # Health testing
    # ------------------------------------------------------------------ #

    async def test_key(self, record: APIKeyRecord) -> bool:
        """Probe one key against each candidate model and update its status.

        The first model that produces a response marks the key ``active`` and
        is remembered as the key's working model.
        """
        client = genai.Client(api_key=record.key)
        last_error: Optional[Exception] = None
        for model in self.test_models:
            try:
                response = await client.aio.models.generate_content(
                    model=model,
                    contents="ping",
                    config=types.GenerateContentConfig(max_output_tokens=1),
                )
            except Exception as exc:  # network, auth or quota failure
                last_error = exc
                if is_auth_error(exc):
                    break  # key itself is invalid — other models won't help
                continue
            else:
                if response and response.text is not None:
                    record.status = STATUS_ACTIVE
                    record.working_model = model
                    record.last_error = ""
                    record.last_tested = _now_iso()
                    return True
                last_error = RuntimeError("Empty response")

        if last_error is None:
            record.status = STATUS_EXHAUSTED
            record.last_error = "No candidate model produced a response"
        elif is_rate_limit_error(last_error):
            record.status = STATUS_RATE_LIMITED
            record.last_error = f"{type(last_error).__name__}: {last_error}"
        else:
            record.status = STATUS_EXHAUSTED
            record.last_error = f"{type(last_error).__name__}: {last_error}"
        record.last_tested = _now_iso()
        return False

    async def test_all_keys(self) -> Dict[str, str]:
        """Test every key in the pool concurrently and persist results."""
        semaphore = asyncio.Semaphore(8)

        async def _test_one(record: APIKeyRecord) -> tuple[str, str]:
            async with semaphore:
                ok = await self.test_key(record)
                return record.key, "ok" if ok else record.status

        outcomes: Dict[str, str] = {}
        if self.keys:
            results = await asyncio.gather(*(_test_one(record) for record in self.keys))
            outcomes = dict(results)
        self.save_keys()
        return outcomes

    # ------------------------------------------------------------------ #
    # Round-robin rotation
    # ------------------------------------------------------------------ #

    def active_keys(self) -> List[APIKeyRecord]:
        """Return all keys currently eligible for use."""
        return [record for record in self.keys if record.status == STATUS_ACTIVE]

    def next_key(self) -> APIKeyRecord:
        """Return the next active key in round-robin order."""
        pool = self.active_keys()
        if not pool:
            raise AllKeysExhaustedError(
                "No healthy Gemini API keys available. Add keys or test/reset the pool."
            )
        record = pool[self._rr_index % len(pool)]
        self._rr_index += 1
        return record

    def bump_usage(self, record: APIKeyRecord) -> None:
        """Increment the usage counter of a successfully used key."""
        record.usage_count += 1
        record.last_tested = _now_iso()
        self.save_keys()

    def remember_working_model(self, record: APIKeyRecord, model: str) -> None:
        """Persist the model a key successfully generated with."""
        if record.working_model != model:
            record.working_model = model
            self.save_keys()

    def mark_failed(
        self,
        record: APIKeyRecord,
        status: str = STATUS_RATE_LIMITED,
        error: str = "",
    ) -> None:
        """Mark a key failed and persist. The pool then skips it until retested."""
        record.status = status if status in _ALL_STATUSES else STATUS_RATE_LIMITED
        record.last_error = error
        record.last_tested = _now_iso()
        self.save_keys()

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #

    def health_summary(self) -> Dict[str, int]:
        """Return a count of keys per status."""
        summary: Dict[str, int] = {status: 0 for status in _ALL_STATUSES}
        for record in self.keys:
            summary[record.status] += 1
        return summary
