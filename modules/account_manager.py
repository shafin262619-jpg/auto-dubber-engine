"""Playwright persistent profile management.

Profiles live under ``profiles/<name>/`` as Chromium user-data directories.
Each profile keeps its own cookies/storage so sessions survive across runs and
accounts can be swapped when a Claude limit is reached.

The launcher methods are async and must run inside a dedicated asyncio event
loop (see ``orchestrator.run_orchestrator_in_background``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

# Reasonably modern Chromium user-agent used for stealth.
STEALTH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Chromium flags applied to hide automation signals (anti-Cloudflare).
STEALTH_ARGS: List[str] = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
]

# Injected before any page script runs to remove the webdriver marker.
WEBDRIVER_HIDE_JS = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)


class AccountManager:
    """Create, list and authenticate persistent browser profiles."""

    def __init__(
        self,
        profiles_dir: Path,
        claude_url: str = "https://claude.ai",
        stealth: bool = True,
        headless: bool = False,
    ) -> None:
        self.profiles_dir = Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.claude_url: str = claude_url
        self.stealth: bool = stealth
        self.headless: bool = headless

    # ------------------------------------------------------------------ #
    # Profile listing / creation
    # ------------------------------------------------------------------ #

    def list_profiles(self) -> List[str]:
        """Return sorted names of every profile directory under profiles/."""
        if not self.profiles_dir.exists():
            return []
        return sorted(
            entry.name
            for entry in self.profiles_dir.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )

    def ensure_profile(self, name: str) -> Path:
        """Create (if needed) and return the path of a named profile."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"Invalid profile name: {name!r}")
        path = self.profiles_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def profile_exists(self, name: str) -> bool:
        """Return True when a profile directory already exists."""
        return (self.profiles_dir / name).is_dir()

    def rename_profile(self, old_name: str, new_name: str) -> bool:
        """Rename a profile directory (persists across sessions).

        The new name is validated with the same rules as
        :meth:`ensure_profile`.  Raises ``ValueError`` for invalid or
        conflicting names, ``FileNotFoundError`` when the source is missing.
        Returns True when the rename happened (or was a no-op).
        """
        if old_name == new_name:
            return True  # nothing to do
        if not new_name or "/" in new_name or "\\" in new_name or new_name.startswith("."):
            raise ValueError(f"Invalid profile name: {new_name!r}")
        old_path = self.profiles_dir / old_name
        new_path = self.profiles_dir / new_name
        if not old_path.is_dir():
            raise FileNotFoundError(f"Profile '{old_name}' does not exist.")
        if new_path.exists():
            raise ValueError(f"A profile named '{new_name}' already exists.")
        old_path.rename(new_path)
        return True

    def delete_profile(self, name: str) -> bool:
        """Remove a profile directory recursively. Returns True on success."""
        path = self.profiles_dir / name
        if not path.is_dir():
            return False
        for child in path.iterdir():
            if child.is_dir():
                import shutil

                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        path.rmdir()
        return True

    def next_profile_after(self, current: str, exclude: Optional[List[str]] = None) -> str:
        """Return the next profile after *current* in round-robin order.

        Used to auto-switch Claude accounts when a limit is detected.
        """
        profiles = self.list_profiles()
        if not profiles:
            raise RuntimeError("No Claude profiles available. Create one first.")
        blocked = set(exclude or [])
        blocked.add(current)
        candidates = profiles + profiles  # simulate circular list
        try:
            start = profiles.index(current) + 1
        except ValueError:
            start = 0
        for name in candidates[start : start + len(profiles)]:
            if name not in blocked:
                return name
        raise RuntimeError("No alternate Claude account available for switching.")

    # ------------------------------------------------------------------ #
    # Stealth helpers (shared with ClaudeDriver)
    # ------------------------------------------------------------------ #

    def stealth_args(self) -> List[str]:
        """Return the stealth chromium flags to pass to the browser."""
        return list(STEALTH_ARGS) if self.stealth else []

    def user_agent(self) -> Optional[str]:
        """Return the spoofed user-agent (None when stealth is disabled)."""
        return STEALTH_USER_AGENT if self.stealth else None

    @staticmethod
    def webdriver_hide_js() -> str:
        """Return the init-script that hides ``navigator.webdriver``."""
        return WEBDRIVER_HIDE_JS

    # ------------------------------------------------------------------ #
    # Manual authentication
    # ------------------------------------------------------------------ #

    async def launch_authenticator(self, name: str) -> str:
        """Open a headful persistent browser for manual login.

        The user logs into Claude.ai manually, then closes the window. The
        session cookies are persisted into the profile for later automation.

        Returns a human-readable outcome message.
        """
        profile_path = self.ensure_profile(name)
        pw = await async_playwright().start()
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=False,
                args=self.stealth_args(),
                user_agent=self.user_agent(),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            await context.add_init_script(self.webdriver_hide_js())
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(self.claude_url, wait_until="domcontentloaded", timeout=60000)
            except PlaywrightTimeoutError:
                pass  # page may be slow; login flow still works
            # Block until the user closes every page of the browser window.
            while context.pages:
                await asyncio.sleep(1.0)
            await context.close()
            return f"✅ Profile '{name}' saved. Session cookies persisted for automation."
        finally:
            await pw.stop()
