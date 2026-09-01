"""Task DAG pipeline runner that bridges the Streamlit UI and the engine drivers.

The orchestrator is fully async and is executed inside a dedicated background
thread with its own ``asyncio`` event loop (see
:func:`run_orchestrator_in_background`), so Streamlit's synchronous UI never
blocks and no "event loop is closed" errors occur.

Key behaviours:

* **Strict step-by-step account handover** — every Claude step opens ONLY the
  account assigned to it in the Pipeline Builder, runs, then closes that
  browser.  The next step opens its own assigned account fresh; output is
  handed over via ``{previous_output}`` / ``{file_path}``.
* **Mid-task quota failover** — if an account hits a limit *before* finishing,
  the partial text/files are captured, the account is marked exhausted, the
  next healthy account is opened with the partial context, and the step is
  completed there.
* **Manual upload steps** — steps of type ``manual`` pause the pipeline and
  wait for the Streamlit UI to drop files into ``manual_state`` (e.g. the
  source video, the Turboscribe SRT, the OmniVoice audio), then resume.
* **GitHub single source of truth** — Claude agent steps can reference an
  instruction markdown file on GitHub (``github_instruction_url``); the raw
  file is fetched (and cached locally) before the prompt is sent to Claude.
* **QA / Fix loop** — a ``qa`` step collects a structured error list from the
  finished video, and a ``fix`` step receives that error list as context so a
  fresh Claude account can correct the issues.
* **Dynamic split failover (split strategy)** — if a ``fix`` step exhausts
  Claude's token/rate limits, the orchestrator splits the remaining work into
  2 parallel chunks on 2 separate accounts (then 3 if limits are hit again)
  and runs them concurrently.
* **Output attribution** — every step output (UI + export file) is annotated
  with which account(s) and model(s) generated it, and the orchestrator tracks
  every account that hit a limit during the run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .account_manager import AccountManager
from .claude_driver import ClaudeDriver, LimitReachedError
from .gemini_driver import GeminiDriver
from .key_manager import KeyManager

# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #

ENGINE_CLAUDE = "claude"
ENGINE_GEMINI = "gemini"
ENGINES: tuple[str, ...] = (ENGINE_CLAUDE, ENGINE_GEMINI)

# extended step types (auto-dubbing pipeline)
STEP_TYPE_MANUAL = "manual"
STEP_TYPE_QA = "qa"
STEP_TYPE_FIX = "fix"
STEP_TYPE_SPLIT = "split"
STEP_TYPES: tuple[str, ...] = (
    ENGINE_CLAUDE,
    ENGINE_GEMINI,
    STEP_TYPE_MANUAL,
    STEP_TYPE_QA,
    STEP_TYPE_FIX,
    STEP_TYPE_SPLIT,
)

# file-passing modes
FILES_NONE = "none"
FILES_PREVIOUS_DOWNLOADS = "downloads"
FILES_PREVIOUS_EXPORTS = "exports"
FILE_MODES: tuple[str, ...] = (FILES_NONE, FILES_PREVIOUS_DOWNLOADS, FILES_PREVIOUS_EXPORTS)


@dataclass
class PipelineStep:
    """One node of the pipeline DAG.

    ``step_type`` extends the classic claude/gemini engines with the
    auto-dubbing special steps:

    * ``manual`` — pause and wait for the UI to upload a file
      (``manual_label``, ``expected_extensions``, ``file_key``).
    * ``qa`` — run a QA Claude that reviews the produced video and emits a
      structured error list (stored under ``error_context_key``).
    * ``fix`` — run a fix Claude that receives the QA error list as context.
    * ``split`` — dynamic split strategy; if the previous step exhausted
      limits, split the remaining work across ``split_parallel_accounts``.
    """

    step_id: int
    engine: str  # ENGINE_CLAUDE, ENGINE_GEMINI or STEP_TYPE_*
    target: str  # Claude profile name, or Gemini model name
    prompt_template: str  # supports {previous_output}, {file_path}, {file_paths}
    pass_files: str = FILES_NONE  # FILE_MODES
    max_retries: int = 3
    timeout_sec: int = 600
    model_name: str = ""  # Claude UI model (e.g. "Sonnet 5")
    performance_style: str = ""  # Claude UI performance/style option
    effort: str = ""  # Claude effort level: Low/Medium/High/Extra/Max
    thinking: bool = True  # Claude Thinking toggle state
    step_type: str = ENGINE_CLAUDE  # STEP_TYPES

    # manual-upload fields (step_type == "manual")
    manual_label: str = ""  # e.g. "Upload Source Video (.mp4)"
    expected_extensions: str = ".mp4,.mov,.avi,.mkv,.webm"  # comma-separated
    file_key: str = ""  # e.g. "source_video" / "turboscribe_srt" / "omnivoice_audio"

    # GitHub single-source-of-truth field (claude agent steps)
    github_instruction_url: str = ""
    # Multiple instruction URLs — all are fetched and combined (single-claude mode)
    github_instruction_urls: List[str] = field(default_factory=list)

    # QA / Fix fields
    error_context_key: str = ""  # where the QA error list is stored
    fixed_step_id: int = 0  # the QA step this fix step corrects

    # split-strategy fields (step_type == "split")
    split_parallel_accounts: int = 2  # initial fan-out on limit hit
    split_retry_accounts: int = 3  # fan-out on a second limit hit
    split_chunk_index: int = 0  # 0 = whole task, 1..n = sub-chunk
    split_total_chunks: int = 1


@dataclass
class StepResult:
    """Output of one executed pipeline step."""

    step_id: int
    engine: str
    target: str
    output: str
    files: List[str] = field(default_factory=list)
    account_switches: int = 0
    model_name: str = ""
    performance_style: str = ""
    effort: str = ""
    thinking: bool = True
    accounts_used: List[str] = field(default_factory=list)
    step_type: str = ENGINE_CLAUDE
    error_list: str = ""  # QA steps: the structured error list found
    split_results: List["StepResult"] = field(default_factory=list)


@dataclass
class ManualUploadDef:
    """Definition of one manual pause in the auto-dubbing pipeline."""

    step_id: int
    label: str
    file_key: str
    expected_extensions: str = ".mp4,.mov,.avi,.mkv,.webm"
    description: str = ""


class PipelineCancelled(RuntimeError):
    """Raised when the user requests the running pipeline to stop."""


def _dedupe(items: Sequence[str]) -> List[str]:
    """Return a de-duplicated list preserving order."""
    seen: set = set()
    result: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# --------------------------------------------------------------------------- #
# Auto-dubbing 9-step pipeline factory
# --------------------------------------------------------------------------- #


def create_video_dubbing_pipeline(
    claude_accounts: Sequence[str],
    translation_instructions_url: str = "",
    sync_instructions_url: str = "",
    subtitles_instructions_url: str = "",
    model_name: str = "Sonnet 5",
    effort: str = "High",
    thinking: bool = True,
) -> List[PipelineStep]:
    """Build the 9-step semi-automated video dubbing pipeline.

    Step layout (manual steps keep ``step_type="manual"`` and pause the UI):

    1. manual   — Upload Source Video
    2. manual   — Upload Turboscribe SRT
    3. claude   — Agent 1: dialogue screenshots + Hindi translation
                  (fetches ``video_dialogue_screenshot_hindi_translation_instructions.md``)
    4. manual   — Upload OmniVoice Audio (.wav/.mp3)
    5. claude   — Agent 2: sync images + audio + SRT
                  (fetches ``video_sync_instructions.md``)
    6. claude   — Agent 3: Hindi subtitles + Effect Ads
    7. qa       — Agent 4: QA check of the final video (writes error list)
    8. fix      — Agent 5: fixes the QA errors
    9. split    — Dynamic failover: if Step 8 hit limits, split into 2 (then 3)
                  parallel Claude accounts (Steps 10/11 behaviour).

    The account list is consumed round-robin; you can pass the same account
    for multiple steps or a distinct account per step.
    """
    n = len(claude_accounts) if claude_accounts else 1

    def account(i: int) -> str:
        return claude_accounts[i % n] if claude_accounts else "account_1"

    steps: List[PipelineStep] = [
        # 1 — Source video (manual)
        PipelineStep(
            step_id=1,
            engine=STEP_TYPE_MANUAL,
            target="",
            prompt_template="",
            step_type=STEP_TYPE_MANUAL,
            manual_label="Upload Source Video",
            expected_extensions=".mp4,.mov,.avi,.mkv,.webm",
            file_key="source_video",
        ),
        # 2 — Turboscribe SRT (manual)
        PipelineStep(
            step_id=2,
            engine=STEP_TYPE_MANUAL,
            target="",
            prompt_template="",
            step_type=STEP_TYPE_MANUAL,
            manual_label="Upload Turboscribe SRT",
            expected_extensions=".srt",
            file_key="turboscribe_srt",
        ),
        # 3 — Agent 1: dialogue screenshots + Hindi translation
        PipelineStep(
            step_id=3,
            engine=ENGINE_CLAUDE,
            target=account(0),
            prompt_template=(
                "Follow the instruction file exactly. First ask for/confirm the "
                "video and SRT inputs, then execute the full dialogue-screenshot + "
                "Hindi translation workflow step by step.\n\n"
                "INPUTS AVAILABLE:\n"
                "- Video: {source_video}\n"
                "- SRT: {turboscribe_srt}\n"
                "- Previous output: {previous_output}\n\n"
                "Deliver: the screenshots ZIP and the Hindi translation .md file "
                "as downloadable artifacts."
            ),
            pass_files=FILES_PREVIOUS_DOWNLOADS,
            model_name=model_name,
            effort=effort,
            thinking=thinking,
            step_type=ENGINE_CLAUDE,
            github_instruction_url=translation_instructions_url,
        ),
        # 4 — OmniVoice audio (manual)
        PipelineStep(
            step_id=4,
            engine=STEP_TYPE_MANUAL,
            target="",
            prompt_template="",
            step_type=STEP_TYPE_MANUAL,
            manual_label="Upload OmniVoice Audio",
            expected_extensions=".wav,.mp3,.m4a,.ogg",
            file_key="omnivoice_audio",
        ),
        # 5 — Agent 2: sync images + audio + SRT
        PipelineStep(
            step_id=5,
            engine=ENGINE_CLAUDE,
            target=account(1),
            prompt_template=(
                "Follow the instruction file exactly. You receive: the audio file, "
                "the SRT (with timestamps), and the screenshots ZIP produced in "
                "Step 3. Build the synced video as described.\n\n"
                "INPUTS AVAILABLE:\n"
                "- Audio: {omnivoice_audio}\n"
                "- SRT: {turboscribe_srt}\n"
                "- Screenshots ZIP (from Step 3): {file_paths}\n\n"
                "Deliver: the synced video file as a downloadable artifact."
            ),
            pass_files=FILES_PREVIOUS_DOWNLOADS,
            model_name=model_name,
            effort=effort,
            thinking=thinking,
            step_type=ENGINE_CLAUDE,
            github_instruction_url=sync_instructions_url,
        ),
        # 6 — Agent 3: Hindi subtitles + Effect Ads
        PipelineStep(
            step_id=6,
            engine=ENGINE_CLAUDE,
            target=account(2),
            prompt_template=(
                "Generate the Hindi subtitle track and the Effect Ads layer for "
                "the synced video produced in Step 5. Use the companion playbook "
                "and renderer scripts referenced by the instruction file.\n\n"
                "INPUTS AVAILABLE:\n"
                "- Synced video (from Step 5): {file_paths}\n"
                "- Hindi translation (from Step 3): {previous_output}\n\n"
                "Deliver: the final rendered video with Hindi subtitles + effect "
                "ads as a downloadable artifact."
            ),
            pass_files=FILES_PREVIOUS_DOWNLOADS,
            model_name=model_name,
            effort=effort,
            thinking=thinking,
            step_type=ENGINE_CLAUDE,
            github_instruction_url=subtitles_instructions_url,
        ),
        # 7 — QA agent
        PipelineStep(
            step_id=7,
            engine=ENGINE_CLAUDE,
            target=account(3),
            prompt_template=(
                "You are the QA reviewer. Inspect the final rendered video from "
                "Step 6 (and its source SRT/audio) and list every synchronization "
                "or generation error you can find.\n\n"
                "INPUTS AVAILABLE:\n"
                "- Final video (from Step 6): {file_paths}\n"
                "- SRT: {turboscribe_srt}\n"
                "- Audio: {omnivoice_audio}\n\n"
                "OUTPUT FORMAT (exactly):\n"
                "ERRORS:\n"
                "1. [timestamp range] description\n"
                "2. ...\n"
                "If everything is correct, output exactly:\n"
                "ERRORS:\nNONE"
            ),
            pass_files=FILES_PREVIOUS_DOWNLOADS,
            model_name=model_name,
            effort=effort,
            thinking=thinking,
            step_type=STEP_TYPE_QA,
            error_context_key="qa_errors",
        ),
        # 8 — Fix agent
        PipelineStep(
            step_id=8,
            engine=ENGINE_CLAUDE,
            target=account(4),
            prompt_template=(
                "You are the fix agent. The QA reviewer found the following errors "
                "in the final video. Correct them and re-render the affected "
                "sections (fix locally, do not rebuild the whole timeline).\n\n"
                "QA ERROR LIST:\n{qa_errors}\n\n"
                "INPUTS AVAILABLE:\n"
                "- Final video (from Step 6): {file_paths}\n"
                "- SRT: {turboscribe_srt}\n"
                "- Audio: {omnivoice_audio}\n\n"
                "Deliver: the corrected final video as a downloadable artifact."
            ),
            pass_files=FILES_PREVIOUS_DOWNLOADS,
            model_name=model_name,
            effort=effort,
            thinking=thinking,
            step_type=STEP_TYPE_FIX,
            error_context_key="qa_errors",
            fixed_step_id=7,
        ),
        # 9 — Dynamic split failover (Steps 10/11 behaviour)
        PipelineStep(
            step_id=9,
            engine=STEP_TYPE_SPLIT,
            target="",
            prompt_template="",
            step_type=STEP_TYPE_SPLIT,
            split_parallel_accounts=2,
            split_retry_accounts=3,
            fixed_step_id=8,
        ),
    ]
    return steps


def create_single_claude_pipeline(
    claude_account: str,
    translation_instructions_url: str = "",
    sync_instructions_url: str = "",
    subtitles_instructions_url: str = "",
    model_name: str = "Sonnet 5",
    effort: str = "High",
    thinking: bool = True,
) -> List[PipelineStep]:
    """Build a minimal 4-step pipeline where ONE Claude does everything.

    Step layout:

    1. manual   — Upload Source Video
    2. manual   — Upload Turboscribe SRT
    3. manual   — Upload OmniVoice Audio
    4. claude   — ONE Claude account receives ALL instruction files (fetched
                  together from GitHub) plus ALL documents (video, SRT, audio)
                  in a single conversation, and executes the complete workflow:
                  screenshots + translation → sync → subtitles + effects → QA
                  → fixes (self-QA), delivering the final video.

    The ``github_instruction_urls`` list on step 4 makes the orchestrator fetch
    every instruction markdown and join them into one prompt, so the single
    Claude always has the full workflow context at once.
    """
    instruction_urls: List[str] = []
    for url in (translation_instructions_url, sync_instructions_url, subtitles_instructions_url):
        if url:
            instruction_urls.append(url)

    steps: List[PipelineStep] = [
        PipelineStep(
            step_id=1,
            engine=STEP_TYPE_MANUAL,
            target="",
            prompt_template="",
            step_type=STEP_TYPE_MANUAL,
            manual_label="Upload Source Video",
            expected_extensions=".mp4,.mov,.avi,.mkv,.webm",
            file_key="source_video",
        ),
        PipelineStep(
            step_id=2,
            engine=STEP_TYPE_MANUAL,
            target="",
            prompt_template="",
            step_type=STEP_TYPE_MANUAL,
            manual_label="Upload Turboscribe SRT",
            expected_extensions=".srt",
            file_key="turboscribe_srt",
        ),
        PipelineStep(
            step_id=3,
            engine=STEP_TYPE_MANUAL,
            target="",
            prompt_template="",
            step_type=STEP_TYPE_MANUAL,
            manual_label="Upload OmniVoice Audio",
            expected_extensions=".wav,.mp3,.m4a,.ogg",
            file_key="omnivoice_audio",
        ),
        PipelineStep(
            step_id=4,
            engine=ENGINE_CLAUDE,
            target=claude_account,
            prompt_template=(
                "You are the full-stack video dubbing agent. Below are ALL the "
                "instruction files for this project — read them all carefully, "
                "then execute the complete workflow end-to-end in one continuous "
                "pass, following each instruction in order.\n\n"
                "INSTRUCTION 1 — Dialogue screenshots + Hindi translation.\n"
                "INSTRUCTION 2 — Video sync (images + audio + SRT).\n"
                "INSTRUCTION 3 — Hindi subtitles + Effect Ads render.\n\n"
                "After producing the final video, run the QA checklist from the "
                "instructions, fix any issues you find, and deliver the final "
                "corrected video as a downloadable artifact.\n\n"
                "ALL INPUTS AVAILABLE (attached):\n"
                "- Video: {source_video}\n"
                "- SRT: {turboscribe_srt}\n"
                "- Audio: {omnivoice_audio}\n\n"
                "DELIVERABLES:\n"
                "1. Screenshots ZIP\n"
                "2. Hindi translation .md\n"
                "3. Synced video\n"
                "4. Final video with Hindi subtitles + effect ads (QA'd and fixed)"
            ),
            pass_files=FILES_PREVIOUS_DOWNLOADS,
            model_name=model_name,
            effort=effort,
            thinking=thinking,
            step_type=ENGINE_CLAUDE,
            github_instruction_urls=instruction_urls,
        ),
    ]
    return steps


def render_pipeline_template(
    template: str,
    context: Dict[str, str],
    previous_output: str = "",
    previous_files: Optional[Sequence[str]] = None,
) -> str:
    """Substitute ``{key}`` placeholders from *context* plus the standard ones."""
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{" + key + "}", value or "")
    rendered = rendered.replace("{previous_output}", previous_output or "")
    prev_files = list(previous_files or [])
    first_file = prev_files[0] if prev_files else ""
    rendered = rendered.replace("{file_path}", first_file)
    rendered = rendered.replace("{file_paths}", "\n".join(prev_files))
    rendered = rendered.replace("{previous_files}", "\n".join(prev_files))
    return rendered


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


class Orchestrator:
    """Sequential DAG runner that dispatches steps to Claude or Gemini."""

    def __init__(
        self,
        settings: dict,
        key_manager: KeyManager,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.settings: dict = settings
        self.key_manager: KeyManager = key_manager
        self.log: Callable[[str], None] = log or (lambda msg: None)

        self.account_manager = AccountManager(
            profiles_dir=Path(str(settings.get("profiles_dir", "profiles"))),
            claude_url=str(settings.get("claude_url", "https://claude.ai")),
            stealth=bool(settings.get("stealth", True)),
            headless=bool(settings.get("headless", False)),
        )
        self.gemini = GeminiDriver(
            key_manager, fallback_models=list(settings.get("gemini_test_models", []))
        )

        self._stop_event: Optional[threading.Event] = None
        self.limit_accounts: List[str] = []
        self.manual_state: Optional[Dict[str, object]] = None
        self._context: Dict[str, str] = {}  # shared file/context values by key
        self._github_cache_dir: Path = Path(
            str(settings.get("github_cache_dir", "workspace/.github_cache"))
        )

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    async def run(
        self,
        steps: Sequence[PipelineStep],
        stop_event: Optional[threading.Event] = None,
        manual_state: Optional[Dict[str, object]] = None,
    ) -> List[StepResult]:
        """Execute all steps in order and return their results.

        ``manual_state`` is a shared dict with the Streamlit UI::

            {
                "active": bool,          # a manual step is waiting
                "step_id": int,
                "label": str,
                "expected_extensions": str,
                "file_key": str,
                "uploaded_paths": [..],  # UI writes absolute paths here
                "ready": threading.Event # UI sets when files are dropped
            }

        Strict handover: each Claude step opens only its assigned account and
        closes it when done, so a completed account is never reused by a later
        step.
        """
        self._stop_event = stop_event
        self.manual_state = manual_state
        self.limit_accounts = []
        previous_output: str = ""
        previous_files: List[str] = []
        self._run_files: List[str] = []  # accumulates ALL files across the run
        results: List[StepResult] = []
        self._context = {}

        for step in steps:
            self._check_cancelled()
            self.log(f"🔄 Step {step.step_id}: {step.engine} → {step.target or '(manual)'}")

            if step.step_type == STEP_TYPE_MANUAL:
                files = await self._run_manual_step(step)
                self._context[step.file_key] = "\n".join(files)
                self._run_files = _dedupe(self._run_files + files)
                previous_files = self._run_files
                results.append(
                    StepResult(
                        step_id=step.step_id,
                        engine=STEP_TYPE_MANUAL,
                        target="user",
                        output="\n".join(files) or f"[{step.manual_label} uploaded]",
                        files=files,
                        step_type=STEP_TYPE_MANUAL,
                    )
                )
                continue

            if step.step_type == STEP_TYPE_QA or step.step_type == STEP_TYPE_FIX:
                result = await self._run_qa_fix_step(step, previous_output, previous_files)
                results.append(result)
                previous_output = result.output
                self._run_files = _dedupe(self._run_files + result.files)
                previous_files = self._run_files
                self._export(step, result)
                continue

            if step.step_type == STEP_TYPE_SPLIT:
                split_results = await self._run_split_strategy(step, previous_output, previous_files)
                results.extend(split_results)
                for sr in split_results:
                    self._export(sr, sr)
                    self._run_files = _dedupe(self._run_files + sr.files)
                previous_files = self._run_files
                continue

            # classic claude / gemini agent step
            prompt = self._render_prompt(step.prompt_template, previous_output, previous_files)

            # GitHub single source of truth: fetch & prepend the instruction.
            instruction = await self._fetch_all_instructions(step)
            if instruction:
                prompt = f"{instruction}\n\n---\n\n{prompt}"

            files = self._resolve_files(step.pass_files, previous_files)

            if step.engine == ENGINE_CLAUDE:
                result = await self._run_claude_step(step, prompt, files)
            else:
                result = await self._run_gemini_step(step, prompt, files)

            results.append(result)
            previous_output = result.output
            self._run_files = _dedupe(self._run_files + result.files)
            previous_files = self._run_files
            self._export(step, result)

        self.log(f"🏁 Pipeline finished: {len(results)} step(s) executed")
        if self.limit_accounts:
            self.log(
                "⚠️ Accounts that hit limits during this run: "
                + ", ".join(self.limit_accounts)
            )
        return results

    # ------------------------------------------------------------------ #
    #  GitHub single source of truth
    # ------------------------------------------------------------------ #

    async def fetch_github_instruction(self, raw_url: str) -> str:
        """Fetch an instruction markdown file from a GitHub raw URL.

        The content is cached under ``workspace/.github_cache/`` (keyed by URL
        hash) so a later run still works if GitHub is unreachable.
        """
        try:
            self._github_cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self._github_cache_dir / (hashlib.sha256(raw_url.encode()).hexdigest() + ".md")

            if cache_file.is_file():
                self.log(f"📥 Using cached GitHub instruction for {raw_url}")
                return cache_file.read_text(encoding="utf-8")

            # Safely URL-encode the path: unquote first (to handle any
            # already-encoded chars), then re-quote (to catch raw spaces).
            parsed = urllib.parse.urlparse(raw_url)
            safe_path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/")
            encoded_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, safe_path, parsed.params, parsed.query, parsed.fragment))

            def _fetch() -> str:
                req = urllib.request.Request(
                    encoded_url, headers={"User-Agent": "AutoDubber-Orchestrator/1.0"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — user-configured URL
                    return resp.read().decode("utf-8")

            content = await asyncio.to_thread(_fetch)
            cache_file.write_text(content, encoding="utf-8")
            self.log(f"📥 Fetched GitHub instruction: {raw_url}")
            return content
        except Exception as exc:  # noqa: BLE001 — never kill the pipeline on a fetch miss
            self.log(f"⚠️ GitHub instruction fetch failed ({exc}); continuing without it.")
            return ""

    async def _fetch_all_instructions(self, step: PipelineStep) -> str:
        """Fetch every instruction referenced by a step and join them.

        Supports both the legacy single ``github_instruction_url`` and the new
        ``github_instruction_urls`` list (single-Claude mode) — the list wins.
        """
        urls: List[str] = []
        if step.github_instruction_urls:
            urls = list(step.github_instruction_urls)
        elif step.github_instruction_url:
            urls = [step.github_instruction_url]
        if not urls:
            return ""

        blocks: List[str] = []
        for i, url in enumerate(urls, 1):
            content = await self.fetch_github_instruction(url)
            if content:
                blocks.append(f"### Instruction {i}/{len(urls)}\n\n{content}")
        if not blocks:
            return ""
        return "\n\n---\n\n".join(blocks)

    # ------------------------------------------------------------------ #
    #  Manual steps
    # ------------------------------------------------------------------ #

    async def _run_manual_step(self, step: PipelineStep) -> List[str]:
        """Pause until the UI drops the requested file(s) into manual_state."""
        if self.manual_state is None:
            self.manual_state = {}

        ready = self.manual_state.get("ready")
        if not isinstance(ready, threading.Event):
            ready = threading.Event()
            self.manual_state["ready"] = ready

        self.manual_state["active"] = True
        self.manual_state["step_id"] = step.step_id
        self.manual_state["label"] = step.manual_label
        self.manual_state["file_key"] = step.file_key
        self.manual_state["expected_extensions"] = step.expected_extensions
        self.manual_state["uploaded_paths"] = []
        ready.clear()

        self.log(
            f"⏸️ Manual pause: {step.manual_label} "
            f"(expected: {step.expected_extensions})"
        )

        # Wait (with stop/cancel support) until the UI sets "ready".
        while not ready.is_set():
            self._check_cancelled()
            await asyncio.sleep(0.5)

        paths = [str(p) for p in (self.manual_state.get("uploaded_paths") or []) if p]
        self.manual_state["active"] = False
        if not paths:
            raise RuntimeError(f"Manual step {step.step_id} completed without a file.")
        self.log(f"📁 Received {len(paths)} file(s) for '{step.manual_label}'")
        return paths

    # ------------------------------------------------------------------ #
    #  QA / Fix steps
    # ------------------------------------------------------------------ #

    async def _run_qa_fix_step(
        self, step: PipelineStep, previous_output: str, previous_files: List[str]
    ) -> StepResult:
        """Run a QA check or a fix step on its assigned Claude account.

        * QA  — the structured error list is stored in ``_context`` under
          ``step.error_context_key`` and attached to the StepResult.
        * Fix — the error list from the referenced QA step is injected into
          the prompt as ``{qa_errors}``.
        """
        assigned: str = step.target
        switches = 0
        used_accounts: List[str] = [assigned]
        step_files: List[str] = list(previous_files)

        if step.step_type == STEP_TYPE_FIX and step.error_context_key:
            qa_errors = self._context.get(step.error_context_key, "")
            if not qa_errors:
                qa_errors = "(No explicit error list — review the final video yourself.)"
            step.prompt_template = step.prompt_template.replace("{qa_errors}", qa_errors)

        prompt = self._render_prompt(step.prompt_template, previous_output, previous_files)
        instruction = await self._fetch_all_instructions(step)
        if instruction:
            prompt = f"{instruction}\n\n---\n\n{prompt}"

        driver = self._new_claude_driver()
        try:
            await driver.open_account(assigned)
            for _attempt in range(max(1, step.max_retries)):
                self._check_cancelled()
                try:
                    result = await driver.send_message(
                        prompt,
                        file_paths=step_files,
                        step_id=step.step_id,
                        model_name=step.model_name,
                        performance_style=step.performance_style,
                        effort=step.effort,
                        thinking=step.thinking,
                    )
                except LimitReachedError as exc:
                    switches += 1
                    self.limit_accounts.append(exc.account)
                    self.log(
                        f"⚠️ Account '{exc.account}' limit exhausted during "
                        f"{step.step_type.upper()} step. Captured partial output. "
                        f"Switching to next healthy account..."
                    )
                    if exc.partial_text:
                        driver.inject_context(
                            f"[Partial output from account '{exc.account}']\n"
                            f"{exc.partial_text}"
                        )
                    if exc.partial_files:
                        step_files = _dedupe(step_files + exc.partial_files)
                    next_account = self.account_manager.next_profile_after(
                        current=exc.account, exclude=used_accounts
                    )
                    used_accounts.append(next_account)
                    await driver.switch_account(next_account)
                    continue

                output = self._with_attribution(
                    result.text,
                    engine=ENGINE_CLAUDE,
                    accounts=used_accounts,
                    model=step.model_name or "Claude (default model)",
                    style=step.performance_style,
                    effort=step.effort,
                )

                error_list = ""
                if step.step_type == STEP_TYPE_QA:
                    error_list = self._extract_error_list(result.text)
                    self._context[step.error_context_key] = error_list
                    self.log(
                        f"🔎 QA step {step.step_id}: {len(error_list.splitlines())} "
                        f"line(s) of findings."
                    )

                return StepResult(
                    step_id=step.step_id,
                    engine=step.step_type,
                    target=assigned,
                    output=output,
                    files=result.downloads,
                    account_switches=switches,
                    model_name=step.model_name,
                    performance_style=step.performance_style,
                    effort=step.effort,
                    thinking=step.thinking,
                    accounts_used=list(used_accounts),
                    step_type=step.step_type,
                    error_list=error_list,
                )
            raise RuntimeError(
                f"Step {step.step_id} failed after exhausting all available accounts."
            )
        finally:
            await driver.close()

    @staticmethod
    def _extract_error_list(text: str) -> str:
        """Pull the structured ``ERRORS:`` block out of a QA response."""
        lines = text.splitlines()
        start = -1
        for i, line in enumerate(lines):
            if line.strip().upper().startswith("ERRORS:"):
                start = i
                break
        if start == -1:
            return text.strip()
        block = "\n".join(lines[start:]).strip()
        if block.upper().replace("ERRORS:", "").strip().upper() == "NONE":
            return "NONE"
        return block

    # ------------------------------------------------------------------ #
    #  Dynamic split strategy (Steps 10/11 behaviour)
    # ------------------------------------------------------------------ #

    async def _run_split_strategy(
        self,
        step: PipelineStep,
        previous_output: str,
        previous_files: List[str],
    ) -> List[StepResult]:
        """If the referenced fix step hit limits, split into 2 parallel Claude
        accounts (Step 10); if limits are hit again, split into 3 (Step 11).

        Each chunk gets the same instruction but a scoped instruction to only
        process its portion of the timeline, and runs on a distinct account in
        parallel.
        """
        fix_had_limits = bool(self.limit_accounts)  # limits accumulated so far
        if not fix_had_limits:
            self.log("✅ Split strategy: no limits hit — nothing to split.")
            return []

        chunk_count = step.split_parallel_accounts
        accounts = self.account_manager.list_profiles()
        healthy = [a for a in accounts if a not in self.limit_accounts]
        if len(healthy) < chunk_count:
            self.log(
                f"⚠️ Not enough healthy accounts for {chunk_count} chunks "
                f"({len(healthy)} available) — falling back to single fix pass."
            )
            return []

        self.log(
            f"🔀 LIMIT DETECTED → splitting fix task into {chunk_count} parallel "
            f"chunks on separate Claude accounts (Step 10 behaviour)."
        )

        chunk_targets = healthy[:chunk_count]
        base_fix_prompt = (
            "You are the split fix agent for chunk {chunk} of {chunks}. Apply the "
            "same correction instructions as the original fix pass, but ONLY for "
            "your assigned portion of the timeline. Do not touch other chunks.\n\n"
            "CONTEXT:\n"
            "{previous_output}\n\n"
            "INPUTS AVAILABLE:\n"
            "{file_paths}\n\n"
            "Deliver: your corrected chunk's output as a downloadable artifact."
        )

        async def run_chunk(idx: int, account: str) -> StepResult:
            self._check_cancelled()
            self.log(f"🔀 Chunk {idx + 1}/{chunk_count} → account '{account}'")
            chunk_prompt = render_pipeline_template(
                base_fix_prompt,
                context={"chunk": str(idx + 1), "chunks": str(chunk_count)},
                previous_output=previous_output,
                previous_files=previous_files,
            )
            sub_step = PipelineStep(
                step_id=step.step_id * 10 + idx + 1,  # 91 / 92 / 93 …
                engine=ENGINE_CLAUDE,
                target=account,
                prompt_template=chunk_prompt,
                pass_files=FILES_NONE,
                model_name=step.model_name,
                effort=step.effort,
                thinking=step.thinking,
                step_type=ENGINE_CLAUDE,
                split_chunk_index=idx + 1,
                split_total_chunks=chunk_count,
            )
            try:
                return await self._run_claude_step(sub_step, chunk_prompt, list(previous_files))
            except Exception as exc:  # noqa: BLE001
                self.log(f"🔀 Chunk {idx + 1} failed: {exc}")
                return StepResult(
                    step_id=sub_step.step_id,
                    engine=ENGINE_CLAUDE,
                    target=account,
                    output=f"<chunk {idx + 1} failed: {exc}>",
                    step_type=ENGINE_CLAUDE,
                )

        results = await asyncio.gather(
            *(run_chunk(i, acc) for i, acc in enumerate(chunk_targets))
        )

        # Second-level failover: if the parallel pass itself hit more limits,
        # retry the failing chunks split into 3 (Step 11 behaviour).
        still_limited = [r for r in results if not r.files and r.output.startswith("<chunk")]
        if still_limited and step.split_retry_accounts > chunk_count:
            self.log(
                f"🔀 Secondary limits detected — re-splitting into "
                f"{step.split_retry_accounts} chunks (Step 11 behaviour)."
            )
            retry_targets = [a for a in healthy if a not in chunk_targets]
            retry_targets = (retry_targets + healthy)[: step.split_retry_accounts]
            async def retry_chunk(idx: int, account: str) -> StepResult:
                retry_prompt = render_pipeline_template(
                    base_fix_prompt,
                    context={"chunk": str(idx + 1), "chunks": str(step.split_retry_accounts)},
                    previous_output=previous_output,
                    previous_files=previous_files,
                )
                sub_step = PipelineStep(
                    step_id=step.step_id * 100 + idx + 1,
                    engine=ENGINE_CLAUDE,
                    target=account,
                    prompt_template=retry_prompt,
                    pass_files=FILES_NONE,
                    model_name=step.model_name,
                    effort=step.effort,
                    thinking=step.thinking,
                    step_type=ENGINE_CLAUDE,
                    split_chunk_index=idx + 1,
                    split_total_chunks=step.split_retry_accounts,
                )
                try:
                    return await self._run_claude_step(
                        sub_step, retry_prompt, list(previous_files)
                    )
                except Exception as exc:  # noqa: BLE001
                    return StepResult(
                        step_id=sub_step.step_id,
                        engine=ENGINE_CLAUDE,
                        target=account,
                        output=f"<chunk {idx + 1} failed: {exc}>",
                        step_type=ENGINE_CLAUDE,
                    )

            results = await asyncio.gather(
                *(retry_chunk(i, acc) for i, acc in enumerate(retry_targets))
            )

        return list(results)

    # ------------------------------------------------------------------ #
    #  Engine runners
    # ------------------------------------------------------------------ #

    async def _run_claude_step(
        self, step: PipelineStep, prompt: str, files: List[str]
    ) -> StepResult:
        """Run one step on its ASSIGNED Claude account, with mid-task failover.

        * Starts with ``step.target`` only — never a leftover account.
        * If a limit interrupts the step, partial output is captured, the
          account is logged as exhausted, and the next healthy account takes
          over with the partial context until the step completes.
        * The driver (browser) is always closed when the step ends — strict
          handover to the next step's assigned account.
        """
        assigned: str = step.target
        switches = 0
        used_accounts: List[str] = [assigned]
        step_files: List[str] = list(files)

        driver = self._new_claude_driver()
        try:
            await driver.open_account(assigned)

            for _attempt in range(max(1, step.max_retries)):
                self._check_cancelled()
                try:
                    result = await driver.send_message(
                        prompt,
                        file_paths=step_files,
                        step_id=step.step_id,
                        model_name=step.model_name,
                        performance_style=step.performance_style,
                        effort=step.effort,
                        thinking=step.thinking,
                    )
                except LimitReachedError as exc:
                    switches += 1
                    self.limit_accounts.append(exc.account)
                    self.log(
                        f"⚠️ Account '{exc.account}' limit exhausted. "
                        f"Captured partial output ({len(exc.partial_text)} chars, "
                        f"{len(exc.partial_files)} file(s)). "
                        f"Switching to next healthy account..."
                    )
                    # Hand the partial context/files to the next account.
                    if exc.partial_text:
                        driver.inject_context(
                            f"[Partial output from account '{exc.account}']\n"
                            f"{exc.partial_text}"
                        )
                    if exc.partial_files:
                        step_files = _dedupe(step_files + exc.partial_files)
                    next_account = self.account_manager.next_profile_after(
                        current=exc.account, exclude=used_accounts
                    )
                    used_accounts.append(next_account)
                    await driver.switch_account(next_account)
                    continue  # resume the same step on the new account

                # Success — attribute, wrap and return.
                output = self._with_attribution(
                    result.text,
                    engine=ENGINE_CLAUDE,
                    accounts=used_accounts,
                    model=step.model_name or "Claude (default model)",
                    style=step.performance_style,
                    effort=step.effort,
                )
                return StepResult(
                    step_id=step.step_id,
                    engine=ENGINE_CLAUDE,
                    target=assigned,
                    output=output,
                    files=result.downloads,
                    account_switches=switches,
                    model_name=step.model_name,
                    performance_style=step.performance_style,
                    effort=step.effort,
                    thinking=step.thinking,
                    accounts_used=list(used_accounts),
                    step_type=step.step_type,
                )

            raise RuntimeError(
                f"Step {step.step_id} failed after exhausting all available accounts."
            )
        finally:
            await driver.close()  # strict handover: never reuse this browser

    async def _run_gemini_step(
        self, step: PipelineStep, prompt: str, files: List[str]
    ) -> StepResult:
        """Run one step against the Gemini API with automatic key rotation."""
        self._check_cancelled()
        text = await self.gemini.generate(prompt, model=step.target, file_paths=files)
        output = self._with_attribution(
            text,
            engine=ENGINE_GEMINI,
            accounts=[],
            model=step.target,
            style=step.performance_style,
        )
        return StepResult(
            step_id=step.step_id,
            engine=ENGINE_GEMINI,
            target=step.target,
            output=output,
            files=files,
            model_name=step.target,
            performance_style=step.performance_style,
            effort=step.effort,
            thinking=step.thinking,
            accounts_used=[],
            step_type=step.step_type,
        )

    # ------------------------------------------------------------------ #
    #  Attribution
    # ------------------------------------------------------------------ #

    @staticmethod
    def _with_attribution(
        text: str,
        engine: str,
        accounts: List[str],
        model: str,
        style: str = "",
        effort: str = "",
    ) -> str:
        """Append a provenance footer identifying the generator account(s)/model(s)."""
        if engine == ENGINE_CLAUDE:
            actors = ", ".join(accounts) if accounts else "unknown account"
            footer = f"[Generated by: {actors} using {model}"
            if effort:
                footer += f" · effort: {effort}"
            if style:
                footer += f" · style: {style}"
            footer += "]"
        else:
            footer = f"[Generated by: Gemini API using {model}]"
        return f"{text}\n\n--- \n{footer}"

    # ------------------------------------------------------------------ #
    #  Templating / file resolution
    # ------------------------------------------------------------------ #

    def _render_prompt(self, template: str, previous_output: str, previous_files: List[str]) -> str:
        """Substitute {previous_output}, {file_path}, {file_paths} and any
        shared pipeline context keys ({source_video}, {turboscribe_srt}, …).

        Delegates to the module-level :func:`render_pipeline_template` so both
        the run loop and the split-strategy chunks share one rendering path.
        """
        return render_pipeline_template(
            template,
            context=self._context,
            previous_output=previous_output,
            previous_files=previous_files,
        )

    @staticmethod
    def _resolve_files(mode: str, previous_files: List[str]) -> List[str]:
        """Return the file paths to pass to a step based on its file-passing mode."""
        if mode == FILES_PREVIOUS_DOWNLOADS or mode == FILES_PREVIOUS_EXPORTS:
            return list(previous_files)
        return []

    # ------------------------------------------------------------------ #
    #  Export
    # ------------------------------------------------------------------ #

    def _export(self, step: PipelineStep, result: StepResult) -> Path:
        """Persist a step's output + attribution metadata to workspace/exports/."""
        exports_dir = Path(str(self.settings.get("exports_dir", "workspace/exports")))
        exports_dir.mkdir(parents=True, exist_ok=True)
        out_path = exports_dir / f"step_{step.step_id}.txt"
        payload = {
            "step_id": step.step_id,
            "engine": result.engine,
            "target": result.target,
            "model_name": result.model_name,
            "performance_style": result.performance_style,
            "effort": result.effort,
            "thinking": result.thinking,
            "accounts_used": result.accounts_used,
            "account_switches": result.account_switches,
            "files": result.files,
            "output": result.output,
            "step_type": result.step_type,
            "error_list": result.error_list,
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self.log(f"💾 Exported step {step.step_id} → {out_path}")
        return out_path

    # ------------------------------------------------------------------ #
    #  Misc
    # ------------------------------------------------------------------ #

    def _new_claude_driver(self) -> ClaudeDriver:
        return ClaudeDriver(self.account_manager, self.settings, log=self.log)

    def _check_cancelled(self) -> None:
        if self._stop_event is not None and self._stop_event.is_set():
            self.log("⛔ Pipeline stop requested.")
            raise PipelineCancelled("Pipeline cancelled by user.")


# --------------------------------------------------------------------------- #
# Async/sync bridge
# --------------------------------------------------------------------------- #


def run_orchestrator_in_background(
    orchestrator: Orchestrator,
    steps: Sequence[PipelineStep],
    log_sink: Callable[[str], None],
    done_sink: Callable[[List[StepResult]], None],
    error_sink: Callable[[Exception], None],
    stop_event: Optional[threading.Event] = None,
    manual_state: Optional[Dict[str, object]] = None,
) -> threading.Thread:
    """Run the async orchestrator in a dedicated daemon thread.

    A fresh asyncio event loop is created inside the thread so the synchronous
    Streamlit process never shares or closes the Playwright/Gemini loop.
    """
    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                orchestrator.run(steps, stop_event=stop_event, manual_state=manual_state)
            )
            done_sink(results)
        except Exception as exc:  # noqa: BLE001 — surface every failure to the UI
            error_sink(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=_runner, name="orchestrator-thread", daemon=True)
    thread.start()
    return thread
