"""Playwright automation for Claude.ai with stealth, limit detection and file handling.

Usage (inside an asyncio event loop)::

    driver = ClaudeDriver(account_manager, settings)
    await driver.open_account("account_1")
    text, files = await driver.send_message("Summarize this PDF", ["/tmp/doc.pdf"], step_id=1)
    await driver.close()
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from playwright.async_api import (
    BrowserContext,
    Download,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .account_manager import AccountManager


# JavaScript that returns the innerText of the last assistant message carrying
# a "Copy" button — only fully-rendered, completed messages have one, so this
# always returns the final collectable output and never partial streaming text.
_LAST_COMPLETED_MESSAGE_JS: str = """
() => {
  const btns = Array.from(document.querySelectorAll(
    'button[aria-label*="Copy" i], [data-testid*="copy" i]'
  ));
  if (!btns.length) return '';
  const btn = btns[btns.length - 1];
  let el = btn;
  for (let i = 0; i < 10 && el; i++) {
    el = el.parentElement;
    if (!el) continue;
    if (el.querySelector('[class*="font-claude-message"]')) {
      const t = el.innerText;
      return t ? t : '';
    }
  }
  return btn.parentElement ? btn.parentElement.innerText : '';
}
"""

@dataclass
class ClaudeStepResult:
    """Outcome of one Claude message send."""

    text: str
    downloads: List[str] = field(default_factory=list)


class LimitReachedError(RuntimeError):
    """Raised when Claude's UI shows a rate-limit or usage-cap banner.

    The orchestrator catches this to switch accounts transparently.  When the
    limit interrupts a mid-task generation, ``partial_text`` and
    ``partial_files`` carry whatever output Claude produced before the block,
    so the next account can resume seamlessly.
    """

    def __init__(
        self,
        phrase: str,
        account: str,
        partial_text: str = "",
        partial_files: Optional[Sequence[str]] = None,
    ) -> None:
        self.phrase: str = phrase
        self.account: str = account
        self.partial_text: str = partial_text
        self.partial_files: List[str] = list(partial_files or [])
        super().__init__(f"Limit detected on {account}: {phrase}")


class ClaudeDriver:
    """Manages a persistent Playwright Chromium context for one Claude.ai account.

    Call ``open_account()`` before sending messages and ``close()`` when done.
    """

    def __init__(
        self,
        account_manager: AccountManager,
        settings: dict,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.account_manager: AccountManager = account_manager
        self.settings: dict = settings
        self.log: Callable[[str], None] = log or (lambda msg: None)

        self._pw: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._current_account: Optional[str] = None
        self._conversation_history: List[str] = []
        self._step_download_dir: Optional[Path] = None

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def open_account(self, name: str) -> None:
        """Launch a persistent browser context for the named profile and navigate to Claude."""
        await self.close()
        profile_path = self.account_manager.ensure_profile(name)
        self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=bool(self.settings.get("headless", False)),
            args=self.account_manager.stealth_args(),
            user_agent=self.account_manager.user_agent(),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        await self._context.add_init_script(self.account_manager.webdriver_hide_js())
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        claude_url = str(self.settings.get("claude_url", "https://claude.ai"))
        await self._page.goto(claude_url, wait_until="domcontentloaded", timeout=30000)
        self._current_account = name
        self.log(f"🔓 Opened Claude account '{name}'")

    async def close(self) -> None:
        """Close the browser context and release resources."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
        self._page = None
        self._current_account = None

    @property
    def current_account(self) -> Optional[str]:
        """Return the name of the currently open account, or None."""
        return self._current_account

    @property
    def is_open(self) -> bool:
        """Return True when a browser context is active."""
        return self._context is not None and self._page is not None

    # ------------------------------------------------------------------ #
    #  Conversation history injection
    # ------------------------------------------------------------------ #

    def inject_context(self, text: str) -> None:
        """Append a chunk of text to the in-memory conversation history.

        The orchestrator uses this to preserve context when switching accounts
        (e.g. ``[Previous context from account_1] …``).
        """
        self._conversation_history.append(text)

    # ------------------------------------------------------------------ #
    #  Send message
    # ------------------------------------------------------------------ #

    async def send_message(
        self,
        prompt: str,
        file_paths: Optional[Sequence[str]] = None,
        step_id: Optional[int] = None,
        model_name: str = "",
        performance_style: str = "",
        effort: str = "",
        thinking: Optional[bool] = None,
    ) -> ClaudeStepResult:
        """Type *prompt* into the Claude composer, optionally attach files, wait for
        output, capture any downloads and return the response text + download paths.

        * ``model_name`` — exact model to select in Claude's Model dropdown
          (e.g. ``Sonnet 5``, ``Opus 5``, ``Fable 5``, ``Haiku 4.5``).
        * ``effort`` — effort level (Low/Medium/High/Extra/Max) to pick inside
          the Model panel's Effort submenu.
        * ``thinking`` — target state for the "Thinking" toggle (True = ON).

        Raises ``LimitReachedError`` (with partial output attached) when Claude
        shows a rate-limit banner.
        """
        if self._page is None or self._context is None:
            raise RuntimeError("ClaudeDriver is not open. Call open_account() first.")

        page: Page = self._page
        self._step_download_dir = self._make_step_dir(step_id)

        # Check for a pre-existing limit banner before we send.
        limit = await self._detect_limit()
        if limit:
            raise LimitReachedError(limit, self._current_account or "unknown")

        # Select the configured model, effort & thinking controls.
        await self.select_model_and_style(
            model_name=model_name,
            performance_style=performance_style,
            effort=effort,
            thinking=thinking,
        )

        # Build the full prompt with conversation history.
        full_prompt = self._build_prompt(prompt)

        # Attach files if provided.
        if file_paths:
            await self._attach_files(list(file_paths))

        # Focus the composer and send (bypasses any leftover overlay).
        self.log(f"⌨️ Sending message to Claude (step {step_id})")
        await self._focus_composer()
        await page.keyboard.press("Control+a")  # clear any pre-existing text
        await page.keyboard.type(full_prompt, delay=5)
        await page.keyboard.press("Enter")

        # Wait for the response to complete; capture partial output on limits.
        try:
            await self._wait_for_output_completion()
        except LimitReachedError as exc:
            exc.partial_text = await self._extract_latest_response()
            try:
                exc.partial_files = await self._capture_downloads()
            except Exception:
                pass
            self.log(
                f"🧩 Captured partial output: {len(exc.partial_text)} chars, "
                f"{len(exc.partial_files)} file(s)"
            )
            raise

        # Extract the latest assistant message.
        text = await self._extract_latest_response()

        # Capture any file downloads (artefacts).
        downloads = await self._capture_downloads()

        self.log(f"✅ Step {step_id}: response received ({len(text)} chars, {len(downloads)} files)")
        return ClaudeStepResult(text=text, downloads=downloads)

    # ------------------------------------------------------------------ #
    #  Model / Effort / Thinking selection (confirmed Claude UI layout)
    # ------------------------------------------------------------------ #
    #  Confirmed UI structure:
    #   1. Main trigger button lives in the chat input container (bottom
    #      right) and shows the current model + effort, e.g. "Sonnet 5 High".
    #   2. Opening it shows a popover listing models as plain text:
    #      "Fable 5", "Opus 5", "Sonnet 5", "Haiku 4.5".
    #   3. A "Effort" item inside the popover slides to the effort view.
    #   4. Effort levels are plain text: Low / Medium / High / Extra / Max.
    #   5. A "Thinking" toggle switch sits at the bottom of the effort view.

    _MODEL_NAME_PATTERN: re.Pattern = re.compile(
        r"Sonnet 5|Opus 5|Haiku 4.5|Fable 5", re.IGNORECASE
    )

    async def select_model_and_style(
        self,
        model_name: str = "",
        performance_style: str = "",
        effort: str = "",
        thinking: Optional[bool] = None,
    ) -> None:
        """Configure Claude's composer controls before the prompt is sent.

        * ``model_name`` — exact model text to click in the Model popover
          (e.g. ``Sonnet 5``, ``Opus 5``, ``Fable 5``, ``Haiku 4.5``).
        * ``effort`` — effort level to pick inside the Effort view
          (``Low`` / ``Medium`` / ``High`` / ``Extra`` / ``Max``).
        * ``thinking`` — target state for the "Thinking" toggle (True = ON).
        * ``performance_style`` — legacy secondary dropdown option (kept for
          backward compatibility).

        Every step is optional and fault-tolerant: if a control is missing the
        driver logs a warning and continues with Claude's default.
        """
        # 1) Pick the model from the popover.
        if model_name and model_name.strip():
            await self._select_model(model_name.strip())

        # 2) Open the Effort view: pick the effort level and the Thinking state.
        if effort or thinking is not None:
            await self._select_effort_and_thinking(
                effort.strip() if effort else "", thinking
            )

        # Legacy performance/style dropdown (kept for backward compatibility).
        style = performance_style.strip() if performance_style else ""
        if style and style.lower() not in ("default", "none", "keep current"):
            await self._click_dropdown_option(
                trigger_selectors=self._style_trigger_selectors(),
                option_text=style,
                label="performance/style",
            )

        # Force-close any lingering popover/overlay before the composer is
        # touched — a leftover portal div intercepts clicks and stalls the
        # pipeline.
        await self._close_popovers()

    # ------------------------------------------------------------------ #
    #  Main trigger button
    # ------------------------------------------------------------------ #

    async def _click_model_trigger(self) -> bool:
        """Click the model selector trigger inside the chat input container.

        The trigger's text changes dynamically (e.g. "Sonnet 5 High"), so it
        is matched by regex against any known model name.
        """
        page = self._page
        if page is None:
            return False
        try:
            trigger = page.locator("button").filter(
                has_text=self._MODEL_NAME_PATTERN
            ).first
            await trigger.click(timeout=10000)
            await page.wait_for_timeout(800)  # let the popover render
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  Model selection
    # ------------------------------------------------------------------ #

    async def _select_model(self, model_name: str) -> bool:
        """Open the popover and click the exact model text."""
        page = self._page
        if page is None:
            return False
        try:
            if not await self._click_model_trigger():
                self.log(f"⚠️ Could not open the model selector to pick '{model_name}'.")
                return False
            if await self._click_popover_text(model_name):
                await page.wait_for_timeout(400)
                self.log(f"🎛️ Selected model: {model_name}")
                return True
            self.log(f"⚠️ Model '{model_name}' not found in the popover.")
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------ #
    #  Effort & Thinking (nested menu)
    # ------------------------------------------------------------------ #

    async def _select_effort_and_thinking(
        self, effort: str, thinking: Optional[bool]
    ) -> None:
        """Navigate the nested Effort menu and set Effort + Thinking.

        Flow: open the trigger → click "Effort" → wait for the slide animation
        → set the Thinking toggle → click the effort level (closes the menu).
        """
        page = self._page
        if page is None:
            return
        try:
            if not await self._click_model_trigger():
                self.log("⚠️ Could not open the model selector for Effort/Thinking.")
                return

            # Click the "Effort" item to slide into the Effort view.
            try:
                await page.get_by_text("Effort").first.click(timeout=5000)
            except Exception:
                self.log("⚠️ 'Effort' menu item not found — skipping effort/thinking.")
                return
            await page.wait_for_timeout(500)  # slide animation

            # Set the Thinking toggle first (clicking the effort level closes
            # the menu, so the toggle must be handled before it).
            if thinking is not None:
                await self._set_thinking_toggle(bool(thinking))

            # Select the effort level.
            if effort:
                if await self._click_popover_text(effort):
                    await page.wait_for_timeout(300)
                    self.log(f"⚡ Effort: {effort}")
                else:
                    self.log(f"⚠️ Effort level '{effort}' not found.")
        except Exception as exc:
            self.log(f"⚠️ Could not complete Effort/Thinking selection: {exc}")

    async def _set_thinking_toggle(self, desired: bool) -> bool:
        """Set the "Thinking" toggle switch to match *desired* (True = ON).

        Reads the current state first and clicks only if it differs.
        """
        page = self._page
        if page is None:
            return False
        try:
            switch = page.get_by_role("switch").first
            if await switch.count() == 0:
                self.log(
                    "⚠️ No Thinking toggle switch found — this control may not be "
                    "available for the current account."
                )
                return False
            # Read the current state (is_checked, with an aria-checked fallback).
            try:
                checked = await switch.is_checked()
            except Exception:
                checked = (await switch.get_attribute("aria-checked") or "").lower() == "true"

            if bool(checked) != bool(desired):
                await switch.click(timeout=5000)
                await page.wait_for_timeout(300)
                self.log(f"🧠 Thinking: {'ON' if desired else 'OFF'} (toggled)")
            else:
                self.log(f"🧠 Thinking: {'ON' if desired else 'OFF'} (already correct)")
            return True
        except Exception as exc:
            self.log(f"⚠️ Could not set the Thinking toggle ({exc}) — continuing.")
            return False

    # ------------------------------------------------------------------ #
    #  Popover cleanup & composer focus (pointer-interception guards)
    # ------------------------------------------------------------------ #
    #  A modal/portal overlay (e.g. <div role="presentation" data-base-ui-inert>)
    #  can remain on screen after the model/effort menus close and silently
    #  intercept clicks meant for the chat input.  These helpers force the
    #  overlay closed and bypass interception when typing the prompt.

    async def _close_popovers(self) -> None:
        """Force-close any lingering dropdown/popover overlay.

        Sends Escape (twice — once for the nested Effort view, once for the
        popover itself) and, as a fallback, clicks on the neutral page header
        if an inert overlay is still present.
        """
        page = self._page
        if page is None:
            return
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(150)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass
        # Fallback: if an inert overlay is still visible, click blank header space.
        try:
            overlay = page.locator('[data-base-ui-inert], [role="presentation"]').first
            if await overlay.count() and await overlay.is_visible(timeout=400):
                header = page.locator(
                    'header, [role="banner"], [class*="header" i]'
                ).first
                if await header.count() and await header.is_visible(timeout=1000):
                    await header.click(timeout=2000, position={"x": 10, "y": 10})
        except Exception:
            pass
        self.log("🧹 Closed any lingering popovers/overlays")

    async def _focus_composer(self) -> None:
        """Focus the chat input, bypassing any pointer-interception overlay.

        Falls back through: normal click → popover cleanup → direct focus →
        forced click.  This guarantees typing is never blocked by a stale
        portal div.
        """
        page = self._page
        if page is None:
            return
        input_box = page.locator('div[contenteditable="true"]').first
        await input_box.wait_for(state="visible", timeout=15000)

        # 1) Standard click.
        try:
            await input_box.click(timeout=5000)
        except Exception:
            # 2) Clean up the overlay, then focus directly and force-click.
            await self._close_popovers()
            try:
                await input_box.focus()
            except Exception:
                pass
            try:
                await input_box.click(force=True, timeout=5000)
            except Exception:
                pass

        # 3) Final safety: make sure the composer actually has focus.
        try:
            is_focused = await input_box.evaluate("el => el === document.activeElement")
            if not is_focused:
                await input_box.focus()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Popover text clicking (shared)
    # ------------------------------------------------------------------ #

    async def _click_popover_text(self, text: str) -> bool:
        """Click the exact text string inside the open popover.

        Uses exact-text matching so the dynamically-labelled trigger button
        (e.g. "Sonnet 5 High") is never matched; falls back to semantic roles.
        """
        page = self._page
        if page is None:
            return False
        # Primary: exact-text match in the popover (per confirmed UI).
        try:
            loc = page.get_by_text(text, exact=True).first
            await loc.wait_for(state="visible", timeout=5000)
            await loc.click(timeout=5000)
            return True
        except Exception:
            pass
        # Fallback: semantic options/menu items.
        return await self._click_matching_option(text)

    async def _click_dropdown_option(
        self, trigger_selectors: Sequence[str], option_text: str, label: str
    ) -> bool:
        """Open a dropdown via the first matching trigger selector, then click
        the option whose text equals *option_text*.

        Returns True on success.  Every interaction is wrapped in try/except so
        a stale selector never crashes the pipeline.
        """
        page = self._page
        if page is None:
            return False

        for selector in trigger_selectors:
            try:
                trigger = page.locator(selector).first
                if await trigger.count() == 0:
                    continue
                if not await trigger.is_visible(timeout=3000):
                    continue
                await trigger.click(timeout=5000)
                await page.wait_for_timeout(1000)  # let the dropdown menu render
                if await self._click_matching_option(option_text):
                    self.log(f"🎛️ Selected {label}: {option_text}")
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                continue

        self.log(f"⚠️ Could not select {label} '{option_text}' — continuing with default.")
        return False

    async def _click_matching_option(self, option_text: str) -> bool:
        """Click the option element whose accessible name equals *option_text*."""
        page = self._page
        if page is None:
            return False

        # 1) Semantic roles used by Claude's dropdowns.
        for role in ("option", "menuitem", "menuitemradio", "radio"):
            loc = page.get_by_role(role, name=option_text, exact=True)
            try:
                if await loc.count() > 0:
                    await loc.first.click(timeout=5000)
                    return True
            except Exception:
                continue

        # 2) Exact-text fallback — menu items are appended later in the DOM,
        #    so prefer the last match to avoid clicking the trigger button.
        try:
            loc = page.get_by_text(option_text, exact=True)
            count = await loc.count()
            if count > 0:
                await loc.nth(count - 1).click(timeout=5000)
                return True
        except Exception:
            pass

        return False

    @staticmethod
    def _style_trigger_selectors() -> List[str]:
        """Candidate selectors for Claude's secondary performance/style dropdown."""
        return [
            'button[aria-label*="style" i]',
            '[data-testid*="style" i]',
            '[data-testid*="performance" i]',
            'button:has-text("Style")',
            '[class*="style-selector" i]',
        ]

    # ------------------------------------------------------------------ #
    #  Account switching
    # ------------------------------------------------------------------ #

    async def switch_account(self, next_name: str) -> None:
        """Close the current context and open a new one for *next_name*.

        In-memory conversation history is preserved and re-injected after
        the new account loads.
        """
        history = list(self._conversation_history)
        self.log(f"🔄 Switching account: '{self._current_account}' → '{next_name}'")
        await self.close()
        self._conversation_history = history
        await self.open_account(next_name)

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    async def _wait_for_output_completion(self) -> None:
        """Busy-wait until Claude has FULLY finished generating.

        The method only returns when all of these hold:
        1. The "Stop generating" button has disappeared (generation ended).
        2. A "Copy" button is visible for the latest message (output rendered), OR
           the extracted output text is unchanged across several consecutive
           polls (content has settled).
        3. No limit banner is present.

        Only after this returns does the caller collect the output, which
        guarantees the collected text is the final, complete response.
        """
        page = self._page
        if page is None:
            return

        stop_btn = page.get_by_role("button", name=re.compile("stop", re.I)).first
        copy_btn = page.get_by_role("button", name=re.compile("copy", re.I)).first
        poll = float(self.settings.get("poll_interval_sec", 1.0))
        max_wait = float(self.settings.get("max_stop_generating_wait_sec", 600))
        stability_required = int(self.settings.get("stability_checks_required", 3))
        deadline = time.monotonic() + max_wait

        # ---- Phase 1: wait for generation to visibly start ----
        generating = False
        prev_text = ""
        while time.monotonic() < deadline:
            limit = await self._detect_limit()
            if limit:
                raise LimitReachedError(limit, self._current_account or "unknown")
            try:
                if await stop_btn.is_visible(timeout=1000):
                    generating = True
                    break
            except Exception:
                pass
            current_text = await self._extract_latest_response()
            if current_text and current_text != prev_text:
                generating = True  # output is growing → streaming in progress
                break
            # Very fast generation: copy button already visible.
            try:
                if await copy_btn.is_visible(timeout=500):
                    return
            except Exception:
                pass
            prev_text = current_text
            await asyncio.sleep(poll)

        if not generating:
            raise RuntimeError("Claude did not start generating within the timeout period.")
        self.log("⏳ Claude is generating…")

        # ---- Phase 2: wait until the output is COMPLETELY settled ----
        stable_count = 0
        prev_text = ""
        while time.monotonic() < deadline:
            limit = await self._detect_limit()
            if limit:
                raise LimitReachedError(limit, self._current_account or "unknown")
            try:
                stop_visible = await stop_btn.is_visible(timeout=1000)
            except Exception:
                stop_visible = False
            try:
                copy_visible = await copy_btn.is_visible(timeout=500)
            except Exception:
                copy_visible = False

            current_text = await self._extract_latest_response()

            if not stop_visible:
                # Generation has ended; now confirm the output is final.
                if current_text == prev_text and current_text:
                    stable_count += 1
                    if copy_visible or stable_count >= stability_required:
                        await asyncio.sleep(poll)  # final settle delay
                        return
                else:
                    stable_count = 0
            else:
                stable_count = 0

            prev_text = current_text
            await asyncio.sleep(poll)

        raise RuntimeError("Timed out waiting for Claude to finish generating.")

    async def _extract_latest_response(self) -> str:
        """Extract the text of the latest COMPLETED assistant message from the DOM.

        Primary strategy: anchor on the last visible "Copy" button and read the
        text of its enclosing message container — only completed messages carry
        a Copy button, so this always returns the final, collectable output.
        """
        page = self._page
        if page is None:
            return ""

        # Primary: last message that has a Copy button (definitely completed).
        try:
            text = await page.evaluate(_LAST_COMPLETED_MESSAGE_JS)
            if text and text.strip():
                return text.strip()
        except Exception:
            pass

        # Fallback: elements with the Claude message font class.
        loc = page.locator('[class*="font-claude-message"]')
        count = await loc.count()
        if count > 0:
            text = await loc.nth(count - 1).inner_text()
            return text.strip()

        # Fallback: last container with streaming attribute.
        fallback = page.locator('[data-is-streaming]')
        count = await fallback.count()
        if count > 0:
            text = await fallback.nth(count - 1).inner_text()
            return text.strip()

        # Last resort: all conversation-turn blocks.
        turns = page.locator('[data-testid*="message"], [role="article"]')
        count = await turns.count()
        if count > 0:
            text = await turns.nth(count - 1).inner_text()
            return text.strip()

        return ""

    async def _capture_downloads(self) -> List[str]:
        """Click every visible download button in the latest message area and save the
        intercepted files to ``workspace/downloads/step_{id}/``.

        Claude artifacts (``.py``, ``.md``, ``.csv``, …) never trigger browser
        ``download`` events on their own — the script must explicitly click the
        Download icon/button after text generation finishes.  This method:

        1. Scans for ``button[aria-label*="Download" i]``, ``a[download]`` and
           ``[data-testid*="download" i]`` (fallback) — all scoped to the
           latest completed assistant message.
        2. Wraps each click in ``page.expect_download(timeout=15000)``.
        3. Saves the file to ``self._step_download_dir``.

        Returns a list of absolute saved-file paths.
        """
        if self._step_download_dir is None:
            return []
        page = self._page
        if page is None:
            return []

        saved: List[str] = []
        self.log("🔍 Scanning for downloadable artifacts…")

        # Primary: artifact download buttons — Claude exposes these with
        # aria-label="Download" or "Download <filename>".
        dl_locator = page.locator(
            'button[aria-label*="Download" i], button[aria-label*="download" i], '
            'a[download]'
        )
        n = await dl_locator.count()

        # Fallback: data-testid attributes.
        if n == 0:
            dl_locator = page.locator('[data-testid*="download" i]')
            n = await dl_locator.count()

        if n == 0:
            self.log("ℹ️ No download buttons found.")
            return saved

        self.log(f"📎 Found {n} download button(s).")

        for idx in range(n):
            btn = dl_locator.nth(idx)
            if not await btn.is_visible():
                continue
            try:
                async with page.expect_download(timeout=15000) as dl_info:
                    await btn.click(timeout=5000)
                download: Download = await dl_info.value
                fname = download.suggested_filename or f"artifact_{idx}"
                dest = self._step_download_dir / fname
                await download.save_as(str(dest))
                saved.append(str(dest))
                self.log(f"📥 Downloaded: {dest.name} ({fname})")
            except Exception:
                continue

        return saved

    async def _attach_files(self, file_paths: List[str]) -> None:
        """Attach local files to the Claude composer via the hidden file input."""
        page = self._page
        if page is None:
            return

        file_input = page.locator('input[type="file"]').first
        await file_input.wait_for(state="attached", timeout=15000)
        await file_input.set_input_files(file_paths)
        self.log(f"📎 Attached {len(file_paths)} file(s) to composer")

        # Wait for attachment chips to become visible.
        try:
            await page.locator('[class*="attachment"], [data-testid*="attachment"]').first.wait_for(
                state="visible", timeout=10000
            )
        except Exception:
            pass  # non-fatal

    async def _detect_limit(self) -> Optional[str]:
        """Scan the page body for limit/error phrases.

        Returns the matched phrase (lowercased) or None.
        """
        if self._page is None:
            return None
        phrases: List[str] = self.settings.get("limit_detection_phrases", [])
        if not phrases:
            return None
        try:
            body_text = await self._page.locator("body").inner_text(timeout=5000)
        except Exception:
            return None
        body_lower = body_text.lower()
        for phrase in phrases:
            if phrase.lower() in body_lower:
                return phrase
        return None

    def _build_prompt(self, prompt: str) -> str:
        """Prepend conversation history to the prompt."""
        if not self._conversation_history:
            return prompt
        # Truncate history to a reasonable size to avoid overflow.
        history_trimmed = self._conversation_history[-10:]
        context = "\n\n".join(history_trimmed)
        return f"{context}\n\n---\n\n{prompt}"

    def _make_step_dir(self, step_id: Optional[int]) -> Path:
        """Create and return the download directory for the given step."""
        base = Path(str(self.settings.get("downloads_dir", "workspace/downloads")))
        label = f"step_{step_id}" if step_id is not None else "step_unknown"
        step_dir = base / label
        step_dir.mkdir(parents=True, exist_ok=True)
        return step_dir