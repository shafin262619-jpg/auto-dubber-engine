"""Streamlit dashboard for the Hybrid Multi-Agent Workstation.

A Claude.ai-inspired chat interface.  The app is driven by a single
``st.session_state["view"]`` router:

- ``"home"``            – the New Task screen: queued-step chips, the composer
                          pills (engine / account-model / advanced) and the
                          composer input.  This is where Pipeline Builder lives.
- ``"running"``         – the active (or just-finished) run, rendered as a
                          chat thread.  This is where Execution lives.
- ``"history:<file>"``  – a past run from ``workspace/exports/``, rendered
                          read-only with the same thread layout.
- ``"settings"``        – a full-page Settings view (opened via the sidebar
                          gear) containing API Keys, Claude Profiles, Data and
                          Preferences.

All long-running async work (Playwright, Gemini SDK) executes in a dedicated
background thread with its own asyncio event loop so the synchronous Streamlit
UI never blocks.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from string import Template
from typing import Callable, Dict, List, Optional

import pandas as pd
import streamlit as st

from modules.account_manager import AccountManager
from modules.key_manager import KeyManager, STATUS_ACTIVE
from modules.orchestrator import (
    ENGINE_CLAUDE,
    ENGINE_GEMINI,
    FILE_MODES,
    FILES_NONE,
    FILES_PREVIOUS_DOWNLOADS,
    FILES_PREVIOUS_EXPORTS,
    STEP_TYPE_MANUAL,
    STEP_TYPE_QA,
    STEP_TYPE_FIX,
    STEP_TYPE_SPLIT,
    Orchestrator,
    PipelineStep,
    StepResult,
    create_video_dubbing_pipeline,
    run_orchestrator_in_background,
)

# --------------------------------------------------------------------------- #
#  Paths & settings
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
KEYS_PATH = CONFIG_DIR / "api_keys.json"


def load_settings() -> dict:
    """Load config/settings.json, falling back to sane defaults."""
    defaults: Dict[str, object] = {
        "claude_url": "https://claude.ai",
        "headless": False,
        "stealth": True,
        "gemini_default_model": "gemini-3.6-flash",
        "gemini_test_model": "gemini-3.6-flash",
        "stability_checks_required": 3,
        "profiles_dir": str(BASE_DIR / "profiles"),
        "downloads_dir": str(BASE_DIR / "workspace" / "downloads"),
        "exports_dir": str(BASE_DIR / "workspace" / "exports"),
        "known_gemini_models": [
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
        ],
        # GitHub single source of truth for instruction files
        "github_instructions_repo": "https://raw.githubusercontent.com/shafin262619-jpg/auto-dubber-engine/main",
        "github_instructions_branch": "main",
        "github_instruction_files": {
            "translation": "Update data/video_dialogue_screenshot_hindi_translation_instructions.md",
            "sync": "Update data/video_sync_instructions.md",
            "subtitles": "Update data/Hindi subtitles+Effect Ad/PLAYBOOK.md",
        },
        # Dynamic split strategy defaults
        "split_strategy": {
            "max_parallel_accounts": 3,
            "min_chunk_duration_sec": 30,
            "initial_split_count": 2,
            "secondary_split_count": 3,
        },
    }
    try:
        if SETTINGS_PATH.exists():
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                defaults.update({k: v for k, v in loaded.items() if v is not None})
    except (json.JSONDecodeError, OSError):
        pass
    return defaults


SETTINGS: dict = load_settings()


def run_coro(coro_factory: Callable[[], object]) -> object:
    """Run a coroutine in a fresh event loop (for short-lived async calls)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
#  Session singletons
# --------------------------------------------------------------------------- #

@st.cache_resource
def get_key_manager() -> KeyManager:
    return KeyManager(
        KEYS_PATH,
        test_model=str(SETTINGS.get("gemini_test_model", "gemini-2.5-flash")),
        test_models=list(SETTINGS.get("gemini_test_models", [])),
    )


@st.cache_resource
def get_account_manager() -> AccountManager:
    return AccountManager(
        profiles_dir=Path(str(SETTINGS.get("profiles_dir", "profiles"))),
        claude_url=str(SETTINGS.get("claude_url", "https://claude.ai")),
        stealth=bool(SETTINGS.get("stealth", True)),
        headless=bool(SETTINGS.get("headless", False)),
    )


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def masked_key(key: str) -> str:
    """Return a display-safe masked key."""
    if len(key) <= 10:
        return "***"
    return f"{key[:8]}...{key[-4:]}"


def status_color(status: str) -> str:
    """Return a CSS color for a key status."""
    return {
        STATUS_ACTIVE: "#0a7d33",
        "429": "#b45309",
        "exhausted": "#b91c1c",
    }.get(status, "#6b7280")


def styled_keys_frame(key_manager: KeyManager) -> Optional["pd.io.formats.style.Styler"]:
    """Build a color-coded dataframe of the key pool.

    Returns None when the pool is empty (caller decides how to display).
    """
    rows = [
        {
            "key": masked_key(rec.key),
            "status": rec.status,
            "model": rec.working_model or "-",
            "usage_count": rec.usage_count,
            "last_tested": rec.last_tested or "-",
            "last_error": (rec.last_error[:80] if rec.last_error else "-"),
        }
        for rec in key_manager.keys
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    styler = df.style

    def _color(status_value: str) -> str:
        return f"color: white; background-color: {status_color(status_value)}; font-weight: 600;"

    mapper = getattr(styler, "map", None) or styler.applymap
    return mapper(_color, subset=["status"])


def drain_queue(q: "queue.Queue[object]") -> List[object]:
    """Drain a thread-safe queue into a list."""
    items: List[object] = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            break
    return items


def build_steps_from_session() -> List[PipelineStep]:
    """Convert the session-state step definitions into PipelineStep objects."""
    steps: List[PipelineStep] = []
    for idx, definition in enumerate(st.session_state.get("steps", [])):
        engine = str(definition.get("engine", ENGINE_CLAUDE))
        target = str(definition.get("target", "")).strip()
        prompt = str(definition.get("prompt", "")).strip()
        step_type = str(definition.get("step_type", engine))
        if step_type != STEP_TYPE_MANUAL and (not target or not prompt):
            continue
        steps.append(
            PipelineStep(
                step_id=idx + 1,
                engine=engine,
                target=target,
                prompt_template=prompt,
                pass_files=str(definition.get("pass_files", FILES_NONE)),
                max_retries=int(definition.get("max_retries", 3)),
                model_name=str(definition.get("model_name", "")),
                performance_style=str(definition.get("performance_style", "")),
                effort=str(definition.get("effort", "Medium")),
                thinking=bool(definition.get("thinking", True)),
                step_type=step_type,
                manual_label=str(definition.get("manual_label", "")),
                expected_extensions=str(
                    definition.get("expected_extensions", ".mp4,.mov,.avi,.mkv,.webm")
                ),
                file_key=str(definition.get("file_key", "")),
                github_instruction_url=str(definition.get("github_instruction_url", "")),
                error_context_key=str(definition.get("error_context_key", "")),
                fixed_step_id=int(definition.get("fixed_step_id", 0)),
            )
        )
    return steps


def make_manual_state() -> dict:
    """Return a shared manual-state dict the orchestrator and UI hand files through."""
    return {
        "active": False,
        "step_id": None,
        "label": "",
        "file_key": "",
        "expected_extensions": "",
        "uploaded_paths": [],
        "ready": threading.Event(),
    }


def github_raw_url(rel_path: str) -> str:
    """Build the raw.githubusercontent.com URL for a repo-relative instruction file.

    ``settings.github_instructions_repo`` is the raw base URL, e.g.
    ``https://raw.githubusercontent.com/your-org/your-repo/main``.
    """
    repo = str(SETTINGS.get("github_instructions_repo", "")).strip().rstrip("/")
    if not repo:
        return ""
    if rel_path.strip():
        return f"{repo}/{rel_path.strip('/')}"
    return repo


def start_video_pipeline() -> None:
    """Build and launch the 9-step auto-dubbing pipeline."""
    accounts = account_manager.list_profiles()
    if not accounts:
        st.error("No Claude accounts connected. Add one in ⚙️ Settings → Claude Profiles first.")
        return

    instruction_files = SETTINGS.get("github_instruction_files", {})
    base_url = github_raw_url("")

    steps = create_video_dubbing_pipeline(
        claude_accounts=accounts,
        translation_instructions_url=github_raw_url(
            str(instruction_files.get("translation", ""))
        ),
        sync_instructions_url=github_raw_url(str(instruction_files.get("sync", ""))),
        subtitles_instructions_url=github_raw_url(
            str(instruction_files.get("subtitles", ""))
        ),
        model_name=str(SETTINGS.get("video_pipeline_model", "Sonnet 5")),
        effort=str(SETTINGS.get("video_pipeline_effort", "High")),
        thinking=bool(SETTINGS.get("video_pipeline_thinking", True)),
    )

    log_q: "queue.Queue[str]" = queue.Queue()
    events_q: "queue.Queue[tuple[str, object]]" = queue.Queue()
    stop_evt = threading.Event()
    manual_state = make_manual_state()

    orchestrator = Orchestrator(SETTINGS, key_manager, log=log_q.put)
    thread = run_orchestrator_in_background(
        orchestrator,
        steps,
        log_sink=log_q.put,
        done_sink=lambda results: events_q.put(("done", results)),
        error_sink=lambda exc: events_q.put(("error", exc)),
        stop_event=stop_evt,
        manual_state=manual_state,
    )
    st.session_state["exec_state"] = {
        "running": True,
        "thread": thread,
        "log_queue": log_q,
        "events": events_q,
        "stop_event": stop_evt,
        "orchestrator": orchestrator,
        "manual_state": manual_state,
        "log_lines": [],
        "results": None,
        "error": None,
        "started_at": time.time(),
    }
    st.session_state["view"] = "running"
    st.rerun()


# --------------------------------------------------------------------------- #
#  Page config, theme state & CSS generation
# --------------------------------------------------------------------------- #

st.set_page_config(page_title="Claude Workstation", layout="wide")

st.session_state.setdefault("theme", "light")
theme: str = st.session_state["theme"]

_THEME_TOKENS: Dict[str, Dict[str, str]] = {
    "light": {
        "bg_app": "#F9F7F4",
        "bg_sidebar": "#F4F2EC",
        "bg_composer": "#FFFFFF",
        "bg_user_bubble": "#F0EEE6",
        "bg_pill": "#F0EEE6",
        "border_color": "#E8E6DD",
        "text_primary": "#30302E",
        "text_secondary": "#87867F",
        "accent": "#CC785C",
        "accent_hover": "#B5674E",
        "accent_interactive": "#2F6FED",
        "accent_brand": "#D97757",
        "success": "#16A34A",
        "danger": "#DC2626",
    },
    "dark": {
        "bg_app": "#262624",
        "bg_sidebar": "#21201E",
        "bg_composer": "#2F2E2B",
        "bg_user_bubble": "#38362F",
        "bg_pill": "#38362F",
        "border_color": "#3A3934",
        "text_primary": "#ECE8DF",
        "text_secondary": "#9C9A93",
        "accent": "#D97757",
        "accent_hover": "#E08A6C",
        "accent_interactive": "#5B9BFF",
        "accent_brand": "#D97757",
        "success": "#22C55E",
        "danger": "#F87171",
    },
}

_CSS_TEMPLATE: str = """<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Work+Sans:wght@400;500;600;700;800&display=swap');

:root {
    --primary-color: $accent_interactive;
    --background-color: $bg_app;
    --secondary-background-color: $bg_sidebar;
    --text-color: $text_primary;
    --font: 'Inter', 'Work Sans', -apple-system, 'Segoe UI', system-ui, sans-serif;
    --bg-app: $bg_app;
    --bg-sidebar: $bg_sidebar;
    --bg-composer: $bg_composer;
    --bg-user-bubble: $bg_user_bubble;
    --bg-pill: $bg_pill;
    --border-color: $border_color;
    --text-primary: $text_primary;
    --text-secondary: $text_secondary;
    --accent: $accent;
    --accent-hover: $accent_hover;
    --accent-interactive: $accent_interactive;
    --accent-brand: $accent_brand;
    --success: $success;
    --danger: $danger;
    --radius-lg: 24px;
    --radius-md: 14px;
    --radius-full: 9999px;
}

.stApp {
    background-color: var(--bg-app) !important;
    color: var(--text-primary) !important;
    font-family: var(--font) !important;
    -webkit-font-smoothing: antialiased;
}

/* Hide Streamlit's own header/toolbar chrome — now safe to remove the
   header element entirely since the sidebar toggle is driven by our own
   columns-based panel, not Streamlit's native sidebar. */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }
[data-testid="stToolbar"] { visibility: hidden; }
header[data-testid="stHeader"] { display: none !important; }

/* Chat-like centered column (no fixed/absolute positioning) */
div.block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 5rem !important;
    padding-left: 1.25rem !important;
    padding-right: 2rem !important;
    max-width: 1180px !important;
    margin-left: auto !important;
    margin-right: auto !important;
}

.stCaption, div[data-testid="stCaptionContainer"] p {
    color: var(--text-secondary) !important;
}

/* ---------------- Custom side-panel toggle (« / ») ---------------- */
button[title="Collapse sidebar"], button[title="Expand sidebar"] {
    background: transparent !important;
    border: none !important;
    color: var(--text-secondary) !important;
    box-shadow: none !important;
    font-size: 1.15rem !important;
    padding: 4px 10px !important;
    min-height: 0 !important;
    width: auto !important;
    margin: 0 0 8px 0 !important;
    line-height: 1 !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
}
button[title="Collapse sidebar"]:hover, button[title="Expand sidebar"]:hover {
    background: var(--bg-pill) !important;
    color: var(--text-primary) !important;
}

/* Side panel column — same visual treatment as the old native sidebar */
section[data-testid="stMain"] div[data-testid="stVerticalBlock"]:has(button[title="Collapse sidebar"]) {
    background: var(--bg-sidebar) !important;
    border-right: 1px solid var(--border-color) !important;
    padding: 14px 10px !important;
    border-radius: 0 var(--radius-lg) var(--radius-lg) 0 !important;
    min-height: 92vh;
}

/* ---------------- Composer placeholder legibility ---------------- */
.stTextArea textarea::placeholder,
textarea::placeholder {
    color: var(--text-secondary) !important;
    opacity: 1 !important;
}
.brand-wordmark {
    display: flex; align-items: center; gap: 8px;
    font-weight: 800; font-size: 1.05rem; color: var(--text-primary);
    padding: 2px 4px 12px;
}
.brand-starburst { color: var(--accent-brand); font-size: 1.2rem; }
.history-group-label {
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--text-secondary);
    margin: 12px 0 4px;
}

/* ---------------- Chat messages ---------------- */
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {
    display: none !important;
}
[data-testid="stChatMessage"] {
    padding: 4px 2px !important;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: var(--bg-user-bubble) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: var(--radius-md) !important;
    padding: 12px 16px !important;
    margin: 8px 0 !important;
}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] p {
    color: var(--text-primary) !important;
    font-size: 15px !important;
    line-height: 1.6 !important;
}

/* ---------------- Empty-state hero ---------------- */
.empty-hero {
    text-align: center; padding-top: 9vh;
}
.empty-hero .hero-title {
    font-size: 2.1rem; font-weight: 800; color: var(--text-primary);
    margin: 0 0 6px;
}
.empty-hero .hero-sub { color: var(--text-secondary); font-size: 0.95rem; }

/* ---------------- Composer card ---------------- */
.attach-icon {
    color: var(--text-secondary); font-size: 1rem; text-align: center;
    padding-top: 10px;
}
.attach-icon.active { color: var(--accent-interactive); }
button[title="Send step"] {
    border-radius: 50% !important;
    width: 44px !important; height: 44px !important;
    min-width: 44px !important; min-height: 44px !important;
    padding: 0 !important; margin: 0 auto !important;
    background: var(--accent) !important; color: #fff !important;
    border: none !important;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.15rem !important;
    box-shadow: none !important;
}
button[title="Send step"]:hover { background: var(--accent-hover) !important; }
button[title="Stop Pipeline"] {
    border-radius: var(--radius-full) !important;
    padding: 10px 22px !important;
    background: var(--danger) !important; color: #fff !important;
    border: none !important; box-shadow: none !important;
    font-weight: 700 !important;
    display: block; margin: 0 auto !important;
}
button[title="Stop Pipeline"]:hover { filter: brightness(.92) !important; }

/* ---------------- Pills (popover triggers) ---------------- */
[data-testid="stPopoverButton"] {
    background: var(--bg-pill) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: var(--radius-full) !important;
    padding: 8px 16px !important;
    color: var(--text-primary) !important;
    font-weight: 600 !important;
    box-shadow: none !important;
}
[data-testid="stPopover"] [data-testid="stPopoverBody"] {
    background: var(--bg-composer) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: var(--radius-md) !important;
    padding: 12px !important;
}
[data-testid="stPopover"] hr { border-color: var(--border-color) !important; }

/* Popover option lists (engine / effort) — vertical rows with blue check */
[data-testid="stPopover"] div[role="radiogroup"] { flex-direction: column; gap: 0; padding: 0; }
[data-testid="stPopover"] label[data-baseweb="radio"] {
    display: flex; align-items: center;
    padding: 9px 4px; cursor: pointer;
    color: var(--text-primary); font-weight: 500;
    border-bottom: 1px solid var(--border-color) !important;
}
[data-testid="stPopover"] label[data-baseweb="radio"]:last-child { border-bottom: none !important; }
[data-testid="stPopover"] label[data-baseweb="radio"]:hover { background: var(--bg-pill); }
[data-testid="stPopover"] label[data-baseweb="radio"] [data-baseweb="radio-circle"],
[data-testid="stPopover"] label[data-baseweb="radio"] [data-baseweb="radio-input"] {
    display: none !important;
}
[data-testid="stPopover"] label[data-baseweb="radio"]:has(input:checked)::after {
    content: "✓"; color: var(--accent-interactive); font-weight: 700; margin-left: auto;
}

/* ---------------- Settings view: vertical left nav ---------------- */
.settings-nav-title {
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--text-secondary);
    padding: 4px 10px 10px;
}
div[data-testid="stHorizontalBlock"] div[data-testid="stVerticalBlock"]:has(.settings-nav-title) div[role="radiogroup"] {
    flex-direction: column; gap: 2px; padding: 0;
}
div[data-testid="stHorizontalBlock"] div[data-testid="stVerticalBlock"]:has(.settings-nav-title) label[data-baseweb="radio"] {
    display: flex; align-items: center;
    padding: 9px 12px; border-radius: 10px; cursor: pointer;
    color: var(--text-secondary); font-weight: 500; font-size: 0.9rem;
    border-bottom: none !important;
    transition: background-color .15s ease;
}
div[data-testid="stHorizontalBlock"] div[data-testid="stVerticalBlock"]:has(.settings-nav-title) label[data-baseweb="radio"]:hover { background: var(--bg-pill); }
div[data-testid="stHorizontalBlock"] div[data-testid="stVerticalBlock"]:has(.settings-nav-title) label[data-baseweb="radio"]:has(input:checked) {
    background: var(--bg-pill); color: var(--text-primary); font-weight: 600;
}
div[data-testid="stHorizontalBlock"] div[data-testid="stVerticalBlock"]:has(.settings-nav-title) label[data-baseweb="radio"] [data-baseweb="radio-circle"],
div[data-testid="stHorizontalBlock"] div[data-testid="stVerticalBlock"]:has(.settings-nav-title) label[data-baseweb="radio"] [data-baseweb="radio-input"] {
    display: none !important;
}

/* ---------------- Buttons ---------------- */
.stButton button,
[data-testid="stFormSubmitButton"] button,
[data-testid="stDownloadButton"] button {
    border-radius: var(--radius-md) !important;
    padding: 8px 16px !important;
    font-weight: 600 !important;
    border: 1px solid var(--border-color) !important;
    background: var(--bg-pill) !important;
    color: var(--text-primary) !important;
    box-shadow: none !important;
    outline: none !important;
    transition: all .2s ease !important;
}
.stButton button:hover,
[data-testid="stFormSubmitButton"] button:hover,
[data-testid="stDownloadButton"] button:hover {
    background: var(--border-color) !important;
    color: var(--text-primary) !important;
    box-shadow: none !important;
}
.stButton button:focus, .stButton button:active,
[data-testid="stFormSubmitButton"] button:focus,
[data-testid="stDownloadButton"] button:focus {
    box-shadow: none !important; outline: none !important;
}
.stButton button[kind="primary"] {
    background: var(--accent) !important; border-color: var(--accent) !important;
    color: #ffffff !important;
}
.stButton button[kind="primary"]:hover {
    background: var(--accent-hover) !important; color: #ffffff !important;
}

/* Delete button exception (strict) */
button[title="Delete this step"], button[title="Remove account"],
button[title="Clear pool"], button[title="Clear all steps"],
button[title="Delete exports"], button[title="Delete everything"] {
    background-color: $danger !important; color: #ffffff !important;
    border: none !important; box-shadow: none !important; text-shadow: none !important;
    filter: none !important;
}
button[title="Delete this step"]:hover, button[title="Remove account"]:hover,
button[title="Clear pool"]:hover, button[title="Clear all steps"]:hover,
button[title="Delete exports"]:hover, button[title="Delete everything"]:hover {
    background-color: #b91c1c !important; color: #ffffff !important;
    border: none !important; box-shadow: none !important; filter: none !important;
}

/* ---------------- Inputs / cards / tables ---------------- */
.stTextInput input, .stTextArea textarea, .stNumberInput input,
div[data-baseweb="select"] > div {
    background-color: var(--bg-pill) !important;
    color: var(--text-primary) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: var(--radius-md) !important;
    font-size: .95rem !important;
}
.stTextInput input:focus, .stTextArea textarea:focus, .stNumberInput input:focus,
div[data-baseweb="select"] > div:focus-within {
    border-color: var(--accent) !important;
    outline: none !important;
}
div[data-baseweb="popover"] [data-baseweb="menu"] {
    background-color: var(--bg-composer) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: var(--radius-md) !important;
}
div[data-baseweb="popover"] li[role="option"] { color: var(--text-primary) !important; }
div[data-baseweb="popover"] li[role="option"]:hover { background: var(--bg-pill) !important; }

div[data-testid="stVerticalBlockBorderWrapper"],
div[data-testid="stForm"],
div[data-testid="stMetric"],
div[data-testid="stExpander"] {
    background: var(--bg-composer) !important;
    border: 1px solid var(--border-color) !important;
    border-radius: var(--radius-lg) !important;
    padding: 20px !important;
    color: var(--text-primary) !important;
}
div[data-testid="stExpander"] { padding: 12px 20px !important; }
div[data-testid="stExpander"] button[data-testid="stExpanderHeader"] {
    padding: 8px 0 !important; font-weight: 600 !important; color: var(--text-primary) !important;
}
/* Explicit dark text on light cards, explicit secondary for captions inside */
[data-testid="stExpanderDetails"] { color: var(--text-primary) !important; }
[data-testid="stExpanderDetails"] p,
[data-testid="stExpanderDetails"] div { color: inherit; }
[data-testid="stExpanderDetails"] .stCaption,
[data-testid="stExpanderDetails"] [data-testid="stCaptionContainer"] p {
    color: var(--text-secondary) !important;
}

div[data-testid="stMetric"] label,
div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
    color: var(--text-secondary) !important; font-size: .85rem !important; font-weight: 500 !important;
}
div[data-testid="stMetricValue"] {
    font-size: 2rem !important; font-weight: 800 !important; color: var(--text-primary) !important;
}

[data-testid="stDataFrame"] {
    border: 1px solid var(--border-color) !important;
    border-radius: var(--radius-md) !important;
    overflow: hidden !important;
    background: var(--bg-composer) !important;
}

[data-testid="stAlert"] { border-radius: var(--radius-md) !important; }
div[data-testid="stCheckbox"] label { color: var(--text-primary) !important; }

/* ---------------- Cleanup: hide branding + deploy ---------------- */
footer, div[data-testid="stFooter"] { display: none !important; }
[data-testid="stMainMenuPopover"] footer,
[data-testid="stMainMenuPopover"] a[href="https://streamlit.io"] { display: none !important; }
header[data-testid="stHeader"] [data-testid="stAppDeployButton"],
header[data-testid="stHeader"] [data-testid="stToolbar"] button[data-testid="stAppDeployButton"] {
    display: none !important;
}
</style>"""


def build_theme_css(theme_name: str) -> str:
    return Template(_CSS_TEMPLATE).substitute(**_THEME_TOKENS[theme_name])


st.markdown(build_theme_css(theme), unsafe_allow_html=True)

key_manager = get_key_manager()
account_manager = get_account_manager()

# --------------------------------------------------------------------------- #
#  Module-level state & constants
# --------------------------------------------------------------------------- #

health = key_manager.health_summary()
profiles = account_manager.list_profiles()
exec_state_global = st.session_state.get("exec_state")
exports_dir = Path(str(SETTINGS.get("exports_dir", "workspace/exports")))
downloads_dir = Path(str(SETTINGS.get("downloads_dir", "workspace/downloads")))

CLAUDE_MODEL_OPTIONS: List[str] = ["Fable 5", "Opus 5", "Sonnet 5", "Haiku 4.5"]
EFFORT_OPTIONS: List[str] = ["Low", "Medium", "High", "Extra", "Max"]
FILE_MODE_LABELS: Dict[str, str] = {
    FILES_NONE: "No files",
    FILES_PREVIOUS_DOWNLOADS: "Files downloaded in the previous step",
    FILES_PREVIOUS_EXPORTS: "Exported outputs from the previous step",
}


# --------------------------------------------------------------------------- #
#  History helpers (sidebar list)
# --------------------------------------------------------------------------- #

def list_export_files() -> List[Path]:
    if not exports_dir.exists():
        return []
    return sorted(
        exports_dir.glob("step_*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _step_index(fp: Path) -> int:
    m = re.match(r"step_(\d+)\.txt", fp.name)
    return int(m.group(1)) if m else 0


def group_exports_by_run(files: List[Path]) -> List[List[Path]]:
    """Group consecutive step files (by name & close mtime) into pipeline runs."""
    ordered = sorted(files, key=lambda p: _step_index(p))
    runs: List[List[Path]] = []
    current: List[Path] = []
    prev_idx: Optional[int] = None
    prev_mtime: Optional[float] = None
    for fp in ordered:
        idx = _step_index(fp)
        mt = fp.stat().st_mtime
        gap = prev_mtime is not None and (
            idx != (prev_idx or 0) + 1 or mt - (prev_mtime or 0) > 120
        )
        if current and gap:
            runs.append(current)
            current = []
        current.append(fp)
        prev_idx = idx
        prev_mtime = mt
    if current:
        runs.append(current)
    return runs


def run_label(run: List[Path]) -> str:
    t = datetime.fromtimestamp(run[0].stat().st_mtime)
    n = len(run)
    return f"Run · {n} step{'s' if n != 1 else ''} · {t:%H:%M}"


def _run_for_file(fp: Path) -> List[Path]:
    for run in group_exports_by_run(list_export_files()):
        if any(f.name == fp.name for f in run):
            return run
    return [fp]


def history_row_label(fp: Path) -> str:
    """Short auto-derived label for a history row."""
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return f"Step {data.get('step_id')} · {data.get('engine')} → {data.get('target')}"
    except Exception:
        return fp.name


def date_bucket_label(fp: Path) -> str:
    """Bucket an export file by recency for the sidebar history groups."""
    d = datetime.fromtimestamp(fp.stat().st_mtime)
    today = datetime.now().date()
    day = d.date()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    if (today - day).days <= 7:
        return "Previous 7 Days"
    if (today - day).days <= 30:
        return "Previous 30 Days"
    return "Older"


# --------------------------------------------------------------------------- #
#  Chat thread rendering (shared by live run + history)
# --------------------------------------------------------------------------- #

def split_logs_by_step(log_lines: List[str]) -> List[tuple]:
    """Group flat orchestrator log lines into per-step segments."""
    segments: List[tuple] = []
    current_label: Optional[str] = None
    current_lines: List[str] = []
    for line in log_lines:
        m = re.match(r"^🔄 Step (\d+):", line)
        if m:
            if current_label is not None:
                segments.append((current_label, current_lines))
            current_label = f"Step {m.group(1)}"
            current_lines = [line]
        else:
            if current_label is not None:
                current_lines.append(line)
    if current_label is not None:
        segments.append((current_label, current_lines))
    return segments


def render_saved_data(data: dict) -> None:
    """Render one saved step (from an export JSON) as a chat block."""
    with st.chat_message("assistant"):
        header = f"**Step {data.get('step_id')}** · {data.get('engine')} · {data.get('target')}"
        if data.get("model_name"):
            header += f" · {data.get('model_name')}"
        if data.get("effort"):
            header += f" · Effort: {data.get('effort')}"
        st.markdown(header)
        st.markdown(data.get("output", ""))
        accounts = data.get("accounts_used") or []
        if accounts:
            st.caption("Accounts used: " + ", ".join(accounts))
        files = data.get("files") or []
        if files:
            st.caption("Downloaded files:")
            for idx, fp in enumerate(files):
                if os.path.isfile(fp):
                    try:
                        with open(fp, "rb") as fh:
                            file_data = fh.read()
                        st.download_button(
                            label=Path(fp).name,
                            data=file_data,
                            file_name=Path(fp).name,
                            mime="application/octet-stream",
                            key=f"histdl_{data.get('step_id')}_{idx}",
                        )
                    except OSError as exc:
                        st.warning(f"Could not read `{fp}`: {exc}")
                else:
                    st.warning(f"File missing: `{fp}`")


def render_result_block(r: StepResult) -> None:
    """Render a completed live StepResult as a chat block, with inline video preview."""
    step_type_label = r.step_type.upper() if r.step_type != ENGINE_CLAUDE else "CLAUDE"
    with st.chat_message("assistant"):
        header = f"**Step {r.step_id}** · {step_type_label} · {r.target}"
        if r.model_name:
            header += f" · {r.model_name}"
        if r.effort:
            header += f" · Effort: {r.effort}"
        st.markdown(header)
        st.markdown(r.output)
        if r.error_list:
            with st.expander("🔎 QA Error List"):
                st.code(r.error_list, language="text")
        if r.accounts_used:
            st.caption("Accounts used: " + ", ".join(r.accounts_used))
        if r.files:
            # Show inline video if any file is a video
            video_files = [fp for fp in r.files if Path(fp).suffix.lower() in (".mp4", ".mov", ".avi", ".mkv", ".webm") and os.path.isfile(fp)]
            if video_files:
                for vf in video_files:
                    st.video(vf)
            st.caption("Downloaded files:")
            for idx, fp in enumerate(r.files):
                if os.path.isfile(fp):
                    try:
                        with open(fp, "rb") as fh:
                            file_data = fh.read()
                        st.download_button(
                            label=Path(fp).name,
                            data=file_data,
                            file_name=Path(fp).name,
                            mime="application/octet-stream",
                            key=f"dl_{r.step_id}_{idx}",
                        )
                    except OSError as exc:
                        st.warning(f"Could not read `{fp}`: {exc}")
                else:
                    st.warning(f"File missing: `{fp}`")
        if r.split_results:
            with st.expander(f"🔀 Split results ({len(r.split_results)} chunks)"):
                for sr in r.split_results:
                    st.markdown(f"**Chunk {sr.step_id}** · {sr.target}")
                    st.markdown(sr.output)


# --------------------------------------------------------------------------- #
#  Settings view (API Keys + Claude Profiles + Data + Preferences)
#  Rendered as a full-page view driven by st.session_state["view"], so closing
#  and reopening is fully reliable (no modal/overlay quirks).
# --------------------------------------------------------------------------- #

def render_settings_view() -> None:
    nav_col, content_col = st.columns([0.9, 3.1])
    with nav_col:
        st.markdown(
            '<div class="settings-nav-title">Settings</div>',
            unsafe_allow_html=True,
        )
        section = st.radio(
            "Settings",
            ["Gemini API Keys", "Claude Profiles", "Data", "Preferences"],
            key="settings_section",
            label_visibility="collapsed",
        )
    with content_col:
        if section == "Gemini API Keys":
            render_api_keys()
        elif section == "Claude Profiles":
            render_claude_accounts()
        elif section == "Data":
            render_data_section()
        else:
            render_preferences()
    if st.button("← Back", key="settings_close"):
        st.session_state["view"] = st.session_state.get("settings_return_view", "home")
        st.rerun()


def render_api_keys() -> None:
    """Gemini API key management (relocated from the old API Keys tab)."""
    st.subheader("Gemini API Keys")
    st.caption("Paste one API key per line. Keys are stored locally in `config/api_keys.json`.")

    existing_keys = "\n".join(rec.key for rec in key_manager.keys)
    keys_text = st.text_area(
        "API keys (one per line)",
        value=existing_keys,
        height=180,
        key="keys_textarea",
        placeholder="AIzaSy...\nAIzaSy...",
    )

    col_save, col_clear, _ = st.columns([1.2, 1, 3])
    with col_save:
        save_clicked = st.button("💾 Save & Test All Keys", type="primary", width="stretch")
    with col_clear:
        if st.button("🗑️ Clear pool", help="Clear pool", width="stretch"):
            key_manager.clear_keys()
            st.rerun()

    if save_clicked:
        added = key_manager.set_keys(keys_text)
        if added:
            st.success(f"Added {added} new key(s).")
        result_q: "queue.Queue[Dict[str, str]]" = queue.Queue()
        tester = threading.Thread(
            target=lambda: result_q.put(dict(run_coro(key_manager.test_all_keys))),
            daemon=True,
        )
        tester.start()
        with st.spinner("Testing all keys against the Gemini API..."):
            while tester.is_alive():
                time.sleep(0.2)
        results = result_q.get() if not result_q.empty() else {}
        ok_count = sum(1 for v in results.values() if v == "ok")
        st.success(f"Test complete: {ok_count}/{len(results)} keys healthy.")
        st.rerun()

    st.divider()
    st.subheader("Key Pool")
    frame = styled_keys_frame(key_manager)
    if frame is None:
        st.info("No API keys saved yet.")
    else:
        st.dataframe(frame, width="stretch", hide_index=True)

        cols = st.columns([1.2, 1.2, 3])
        with cols[0]:
            if st.button("🔄 Retest all keys", width="stretch"):
                result_q: "queue.Queue[Dict[str, str]]" = queue.Queue()
                tester = threading.Thread(
                    target=lambda: result_q.put(dict(run_coro(key_manager.test_all_keys))),
                    daemon=True,
                )
                tester.start()
                with st.spinner("Retesting keys..."):
                    while tester.is_alive():
                        time.sleep(0.2)
                result_q.get()
                st.rerun()
        with cols[1]:
            if st.button("♻️ Reset failed keys", width="stretch"):
                for rec in key_manager.keys:
                    if rec.status != STATUS_ACTIVE:
                        key_manager.reset_key(rec.key)
                st.rerun()


def render_claude_accounts() -> None:
    """Claude profile management (relocated from the old Claude Accounts tab)."""
    st.subheader("👤 Claude Accounts")
    st.caption(
        "Each account is a persistent browser profile. Log in once manually; "
        "automation reuses the saved session and switches accounts on limits."
    )

    with st.expander("➕ Add a New Claude Account", expanded=not bool(profiles)):
        st.caption(
            "Enter a name for the new account, then click **Create & Login**. "
            "A browser window will open — log into Claude manually, then close "
            "the window. The session is saved automatically."
        )
        col_name, col_btn = st.columns([2, 1])
        new_name = col_name.text_input(
            "Account name",
            placeholder="e.g. account_2",
            key="new_profile_name",
            label_visibility="collapsed",
        )
        if col_btn.button(
            "Create & Login", type="primary", width="stretch"
        ):
            name = new_name.strip()
            if not name:
                st.error("Please enter a name for the account.")
            else:
                account_manager.ensure_profile(name)
                auth_result: "queue.Queue[str]" = queue.Queue()
                auth_thread = threading.Thread(
                    target=lambda n=name, q=auth_result: q.put(
                        str(run_coro(lambda: account_manager.launch_authenticator(n)))
                    ),
                    daemon=True,
                )
                auth_thread.start()
                st.session_state["auth_thread"] = auth_thread
                st.session_state["auth_result"] = auth_result
                st.session_state["auth_running"] = name
                st.rerun()

    auth_running = st.session_state.get("auth_running")
    if auth_running:
        auth_thread = st.session_state.get("auth_thread")
        if auth_thread is not None and auth_thread.is_alive():
            st.info(
                f"🔓 Browser open for **{auth_running}** — complete the login, "
                "then **close the browser window** to save the session."
            )
            time.sleep(0.5)
            st.rerun()  # keep polling until the login flow finishes
        else:
            result_q = st.session_state.get("auth_result")
            message = result_q.get() if result_q and not result_q.empty() else "Done."
            st.session_state["auth_running"] = None
            st.session_state["auth_thread"] = None
            st.success(message)
            st.rerun()  # instantly refresh — new account now in the list below

    st.markdown("### 🔗 Connected Accounts")

    if not profiles:
        st.info("No accounts connected yet. Use the section above to add one.")
    else:
        for name in profiles:
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 1.2, 1, 1])
                c1.markdown(f"**{name}**")
                c2.markdown(
                    f"<span style='color: #22c55e; font-size: 0.9rem;'>● Connected</span>",
                    unsafe_allow_html=True,
                )
                if c3.button(
                    "✏️ Rename", key=f"rename_{name}", width="stretch"
                ):
                    st.session_state[f"rename_open_{name}"] = True
                    st.rerun()
                if c4.button(
                    "🗑️ Remove",
                    help="Remove account",
                    key=f"delete_{name}",
                    width="stretch",
                ):
                    account_manager.delete_profile(name)
                    st.rerun()

                if st.session_state.get(f"rename_open_{name}"):
                    col_in, col_save, col_cancel = st.columns([2.4, 1, 1])
                    new_name = col_in.text_input(
                        "New name",
                        value=name,
                        key=f"rename_input_{name}",
                        label_visibility="collapsed",
                    )
                    if col_save.button(
                        "💾 Save", key=f"rename_save_{name}", width="stretch"
                    ):
                        new_name = new_name.strip()
                        if not new_name:
                            st.error("The new name cannot be empty.")
                        elif new_name == name:
                            st.session_state[f"rename_open_{name}"] = False
                            st.rerun()
                        else:
                            try:
                                account_manager.rename_profile(name, new_name)
                            except (ValueError, FileNotFoundError) as exc:
                                st.error(str(exc))
                            else:
                                st.session_state[f"rename_open_{name}"] = False
                                st.success(f"Account renamed to '{new_name}'.")
                                st.rerun()
                    if col_cancel.button(
                        "Cancel", key=f"rename_cancel_{name}", width="stretch"
                    ):
                        st.session_state[f"rename_open_{name}"] = False
                        st.rerun()


def render_data_section() -> None:
    """Downloaded-files list and Clear History controls (from the old History tab)."""
    st.subheader("Data")
    download_files = (
        sorted(
            [p for p in downloads_dir.rglob("*") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if downloads_dir.exists()
        else []
    )
    if download_files:
        with st.expander(f"📥 Downloaded files ({len(download_files)})"):
            for fp in download_files:
                try:
                    file_data = fp.read_bytes()
                except OSError:
                    st.warning(f"Could not read `{fp.name}`")
                else:
                    st.download_button(
                        label=fp.name,
                        data=file_data,
                        file_name=fp.name,
                        mime="application/octet-stream",
                        key=f"hisdl_{fp.as_posix()}",
                    )
    else:
        st.caption("**📥 Downloaded files**  —  No files yet.")

    st.divider()
    st.markdown("**🧹 Clear History**")
    confirm = st.checkbox(
        "I understand — delete all exports and downloaded files permanently.",
        key="history_confirm",
    )
    col_clr1, col_clr2, _ = st.columns([1.2, 1.2, 3])
    if col_clr1.button(
        "🗑️ Delete Exports Only",
        disabled=not confirm,
        help="Delete exports",
        width="stretch",
    ):
        for fp in list_export_files():
            fp.unlink(missing_ok=True)
        st.success("Exports cleared.")
        st.rerun()
    if col_clr2.button(
        "🗑️ Delete Everything",
        type="primary",
        disabled=not confirm,
        help="Delete everything",
        width="stretch",
    ):
        for d in [exports_dir, downloads_dir]:
            for fp in d.rglob("*") if d.exists() else []:
                if fp.is_file():
                    fp.unlink(missing_ok=True)
        st.success("All history cleared.")
        st.rerun()


def render_preferences() -> None:
    """Preferences: theme toggle + GitHub instruction repo config."""
    st.subheader("Preferences")
    if st.button(
        "🌙" if theme == "light" else "☀️",
        key="theme_toggle",
        help="Toggle light / dark theme",
    ):
        st.session_state["theme"] = "dark" if theme == "light" else "light"
        st.rerun()

    st.divider()
    st.subheader("🌐 GitHub Instructions (Single Source of Truth)")
    st.caption(
        "The orchestrator fetches instruction .md files from this repo before "
        "passing them to each Claude agent. Use a raw.githubusercontent.com URL."
    )
    repo_url = st.text_input(
        "Raw base URL",
        value=str(SETTINGS.get("github_instructions_repo", "")),
        key="pref_github_repo",
        placeholder="https://raw.githubusercontent.com/your-org/your-repo/main",
        help="Base raw URL of the repo holding the instruction files.",
    )
    files = SETTINGS.get("github_instruction_files", {}) or {}
    col_t, col_s, col_p = st.columns(3)
    with col_t:
        translation_path = st.text_input(
            "Translation instructions",
            value=str(files.get("translation", "")),
            key="pref_github_translation",
            placeholder="Update data/video_dialogue_screenshot_hindi_translation_instructions.md",
        )
    with col_s:
        sync_path = st.text_input(
            "Sync instructions",
            value=str(files.get("sync", "")),
            key="pref_github_sync",
            placeholder="Update data/video_sync_instructions.md",
        )
    with col_p:
        subtitles_path = st.text_input(
            "Subtitles/Effects instructions",
            value=str(files.get("subtitles", "")),
            key="pref_github_subtitles",
            placeholder="Update data/Hindi subtitles+Effect Ad/PLAYBOOK.md",
        )
    if st.button("💾 Save GitHub config", key="pref_github_save", type="primary"):
        updated = dict(SETTINGS)
        updated["github_instructions_repo"] = repo_url.strip()
        updated["github_instruction_files"] = {
            "translation": translation_path.strip(),
            "sync": sync_path.strip(),
            "subtitles": subtitles_path.strip(),
        }
        try:
            SETTINGS_PATH.write_text(
                json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            st.success("Saved. The video pipeline will use these URLs.")
        except OSError as exc:
            st.error(f"Could not write settings.json: {exc}")

    st.divider()
    st.subheader("🎬 Video Pipeline Defaults")
    col_m, col_e, _ = st.columns(3)
    with col_m:
        model = st.selectbox(
            "Claude model for pipeline steps",
            CLAUDE_MODEL_OPTIONS,
            index=CLAUDE_MODEL_OPTIONS.index(
                str(SETTINGS.get("video_pipeline_model", "Sonnet 5"))
            )
            if str(SETTINGS.get("video_pipeline_model", "Sonnet 5")) in CLAUDE_MODEL_OPTIONS
            else 2,
            key="pref_video_model",
        )
    with col_e:
        effort = st.radio(
            "Effort",
            EFFORT_OPTIONS,
            index=EFFORT_OPTIONS.index(
                str(SETTINGS.get("video_pipeline_effort", "High"))
            )
            if str(SETTINGS.get("video_pipeline_effort", "High")) in EFFORT_OPTIONS
            else 2,
            key="pref_video_effort",
            format_func=lambda x: x,
        )
    thinking = st.toggle(
        "Thinking on for pipeline steps",
        value=bool(SETTINGS.get("video_pipeline_thinking", True)),
        key="pref_video_thinking",
    )
    if st.button("💾 Save pipeline defaults", key="pref_video_save", type="primary"):
        updated = dict(SETTINGS)
        updated["video_pipeline_model"] = model
        updated["video_pipeline_effort"] = effort
        updated["video_pipeline_thinking"] = bool(thinking)
        try:
            SETTINGS_PATH.write_text(
                json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            st.success("Saved pipeline defaults.")
        except OSError as exc:
            st.error(f"Could not write settings.json: {exc}")


# --------------------------------------------------------------------------- #
#  Composer (New Task screen + finished-run composer)
# --------------------------------------------------------------------------- #

def step_chip_label(idx: int, step: dict) -> str:
    engine = str(step.get("engine", ENGINE_CLAUDE))
    engine_short = "Claude" if engine == ENGINE_CLAUDE else "Gemini"
    target = str(step.get("target", ""))
    label = f"Step {idx + 1} · {engine_short} · {target}"
    if st.session_state.get("editing_step") == idx:
        label = "✎ " + label
    return label


def load_step_into_composer(idx: int) -> None:
    """Copy a queued step's settings into the composer for editing."""
    step = st.session_state["steps"][idx]
    engine = step.get("engine", ENGINE_CLAUDE)
    st.session_state["composer_prompt"] = step.get("prompt", "")
    st.session_state["composer_engine"] = engine
    if engine == ENGINE_CLAUDE:
        st.session_state["composer_target_claude"] = step.get("target", "")
        st.session_state["composer_model_name"] = step.get("model_name", "Sonnet 5")
    else:
        st.session_state["composer_target_gemini"] = step.get("target", "")
    st.session_state["composer_effort"] = step.get("effort", "Medium")
    st.session_state["composer_thinking"] = bool(step.get("thinking", True))
    st.session_state["composer_files"] = step.get("pass_files", FILES_NONE)
    st.session_state["composer_retries"] = int(step.get("max_retries", 3))
    st.session_state["editing_step"] = idx
    st.rerun()


def handle_composer_submit() -> None:
    """Add (or update) a queued step from the composer card.

    Runs as an ``on_click`` callback (before widgets instantiate), so it can
    safely read and clear the composer text area.
    """
    prompt = str(st.session_state.get("composer_prompt", "")).strip()
    if not prompt:
        st.session_state["composer_error"] = "Please describe the task first."
        return
    st.session_state.pop("composer_error", None)
    steps = st.session_state.setdefault("steps", [])
    engine = st.session_state.get("composer_engine", ENGINE_CLAUDE)
    target = (
        st.session_state.get("composer_target_claude", "")
        if engine == ENGINE_CLAUDE
        else st.session_state.get("composer_target_gemini", "")
    )
    new_step = {
        "engine": engine,
        "target": target,
        "prompt": prompt,
        "pass_files": st.session_state.get("composer_files", FILES_NONE),
        "max_retries": int(st.session_state.get("composer_retries", 3)),
        "model_name": (
            st.session_state.get("composer_model_name", "Sonnet 5")
            if engine == ENGINE_CLAUDE
            else ""
        ),
        "performance_style": "",
        "effort": st.session_state.get("composer_effort", "Medium"),
        "thinking": bool(st.session_state.get("composer_thinking", True)),
    }
    editing = st.session_state.get("editing_step")
    if editing is not None and 0 <= editing < len(steps):
        steps[editing].update(new_step)
        st.session_state["editing_step"] = None
    else:
        steps.append(new_step)
    st.session_state["composer_prompt"] = ""


def render_composer() -> None:
    """Queued-step chips + Run button + composer card (text area + control row)."""
    steps = st.session_state.setdefault("steps", [])
    st.session_state.setdefault("editing_step", None)

    # Composer widget defaults (single call site per rerun — no double render).
    st.session_state.setdefault("composer_engine", ENGINE_CLAUDE)
    st.session_state.setdefault("composer_target_claude", "")
    st.session_state.setdefault("composer_target_gemini", "")
    st.session_state.setdefault("composer_model_name", "Sonnet 5")
    st.session_state.setdefault("composer_effort", "Medium")
    st.session_state.setdefault("composer_thinking", True)
    st.session_state.setdefault("composer_files", FILES_NONE)
    st.session_state.setdefault("composer_retries", 3)

    # Queued-step chips.
    if steps:
        st.markdown("**Queued steps**")
        cols = st.columns(len(steps))
        for i, step in enumerate(steps):
            with cols[i]:
                if st.button(
                    step_chip_label(i, step),
                    key=f"chip_{i}",
                    width="stretch",
                    help="Edit this step",
                ):
                    load_step_into_composer(i)
                if st.button("✕", key=f"chip_rm_{i}", help="Remove this step"):
                    del st.session_state["steps"][i]
                    st.session_state["editing_step"] = None
                    st.rerun()

    # Run Pipeline — sits just outside the top-right of the composer card.
    if steps:
        run_row = st.columns([3, 1])
        with run_row[1]:
            if st.button(
                "▶️ Run Pipeline",
                type="primary",
                key="run_pipeline_btn",
                width="stretch",
            ):
                start_run()
    else:
        if not profiles:
            st.caption(
                "👤 No Claude accounts yet — open ⚙️ Settings → Claude Profiles and log in once."
            )

    # Composer card: the text area and the control row live in ONE card.
    with st.container(border=True):
        st.text_area(
            "✍️ What should it do?",
            key="composer_prompt",
            height=88,
            placeholder=(
                "Describe the task in plain language, e.g.: "
                "“Summarize this file into 5 bullet points.”"
            ),
            label_visibility="collapsed",
            help=(
                "Write the instruction for this step. You can refer to the "
                "previous step's result with {previous_output}, or to a file "
                "from the previous step with {file_path}."
            ),
        )

        if st.session_state.get("composer_error"):
            st.error(st.session_state.pop("composer_error"))

        # Control row — inside the same card, at its bottom edge.
        c_attach, c_engine, c_target, c_spacer, c_adv, c_send = st.columns(
            [0.4, 1.3, 1.7, 0.7, 1.1, 0.5]
        )
        with c_attach:
            files_mode = st.session_state.get("composer_files", FILES_NONE)
            active_cls = " active" if files_mode != FILES_NONE else ""
            st.markdown(
                f'<div class="attach-icon{active_cls}">📎</div>',
                unsafe_allow_html=True,
            )
        with c_engine:
            engine = st.session_state["composer_engine"]
            engine_label = (
                "Claude (Web Chat)" if engine == ENGINE_CLAUDE else "Gemini (API)"
            )
            with st.popover(engine_label, key="pop_engine"):
                st.caption("Engine")
                st.radio(
                    "Engine",
                    [ENGINE_CLAUDE, ENGINE_GEMINI],
                    key="composer_engine",
                    format_func=lambda e: (
                        "Claude (Web Chat)" if e == ENGINE_CLAUDE else "Gemini (API)"
                    ),
                    label_visibility="collapsed",
                    help=(
                        "Claude (Web Chat) uses your logged-in Claude account — great for "
                        "long tasks and file downloads. Gemini (API) uses your API keys — "
                        "fast and great for many quick steps."
                    ),
                )
        with c_target:
            engine = st.session_state["composer_engine"]
            if engine == ENGINE_CLAUDE:
                target_label = (
                    st.session_state.get("composer_target_claude", "") or "Select…"
                )
                target_opts = profiles if profiles else ["(no profiles yet)"]
            else:
                target_label = (
                    st.session_state.get("composer_target_gemini", "") or "Select…"
                )
                target_opts = list(SETTINGS.get("known_gemini_models", []))
            with st.popover(target_label, key="pop_target"):
                if engine == ENGINE_CLAUDE:
                    st.caption("👤 Account")
                    st.selectbox(
                        "👤 Which account?",
                        target_opts,
                        key="composer_target_claude",
                        label_visibility="collapsed",
                        help="Which logged-in Claude account should do this step?",
                    )
                    st.caption("🧠 Model")
                    st.selectbox(
                        "🧠 Which model?",
                        CLAUDE_MODEL_OPTIONS,
                        key="composer_model_name",
                        label_visibility="collapsed",
                        help="Which Claude model should handle this step? **Sonnet 5** is the best all-round choice.",
                    )
                else:
                    st.caption("🧠 Model")
                    st.selectbox(
                        "🧠 Which Gemini model?",
                        target_opts,
                        key="composer_target_gemini",
                        label_visibility="collapsed",
                        help="Which Gemini model should process this step?",
                    )
        with c_spacer:
            pass
        with c_adv:
            with st.popover("⚙️ Advanced", key="pop_adv"):
                st.caption(
                    "Effort — Higher effort means more thorough responses, but takes longer."
                )
                st.radio(
                    "Effort",
                    EFFORT_OPTIONS,
                    key="composer_effort",
                    label_visibility="collapsed",
                    format_func=lambda x: f"{x} (Default)" if x == "Medium" else x,
                )
                st.divider()
                st.toggle(
                    "Thinking (Can think for more complex tasks)",
                    key="composer_thinking",
                    help=(
                        "When on, Claude reasons through the problem before "
                        "answering — better for complex tasks, slightly slower."
                    ),
                )
                st.divider()
                # TODO: this stays a selectbox (3 modes) because the backend
                # expects pass_files values "none"/"downloads"/"exports" — a
                # plain toggle would drop the "exports" option.
                st.selectbox(
                    "📎 Use files from the previous step?",
                    FILE_MODES,
                    key="composer_files",
                    format_func=lambda mode: FILE_MODE_LABELS.get(mode, mode),
                    help="Should this step receive the files (CSV, PDF, etc.) produced in the previous step?",
                )
                st.number_input(
                    "🔁 Max Retries on Error",
                    min_value=0,
                    max_value=10,
                    key="composer_retries",
                    help="If the system fails or gets stuck, how many times should it try again?",
                )
        with c_send:
            st.button(
                "➤",
                key="composer_send",
                help="Send step",
                on_click=handle_composer_submit,
            )


def render_home() -> None:
    """The New Task screen — hero heading + pipeline preset button + composer."""
    steps = st.session_state.get("steps", [])
    if not steps:
        st.markdown(
            '<div class="empty-hero">'
            '<div class="hero-title">CL Multi-Agent Workstation</div>'
            '<div class="hero-sub">Claude Web Automation Engine × Gemini API Engine · Local Streamlit Dashboard</div>'
            "</div>",
            unsafe_allow_html=True,
        )

        # Video Dubbing Pipeline preset
        markup = '<div style="margin: 1.5rem 0 0.5rem;">'
        st.markdown(markup, unsafe_allow_html=True)
        st.markdown(
            '<div style="margin-bottom: 0.75rem; font-size: 0.95rem; '
            'color: var(--text-secondary);">'
            '<strong>🎬 Auto-Dub Pipeline</strong> — 9-step semi-automated video '
            "translation & dubbing pipeline (Claude + Manual uploads)."
            "</div>",
            unsafe_allow_html=True,
        )
        col_preset, col_info = st.columns([1.2, 3])
        with col_preset:
            pipeline_btn = st.button(
                "🚀 Start Video Dubbing Pipeline",
                type="primary",
                key="video_pipeline_preset",
                width="stretch",
                help=(
                    "Launches the 9-step pipeline: video upload, SRT upload, "
                    "Claude dialogue translation, OmniVoice audio upload, "
                    "Claude sync, Hindi subtitles, QA, and fix."
                ),
            )
            if pipeline_btn:
                start_video_pipeline()
        with col_info:
            st.caption(
                "Requires: 1+ Claude profile(s) connected, GitHub instruction "
                "URLs configured in ⚙️ Settings → Preferences."
            )
    render_composer()


# --------------------------------------------------------------------------- #
#  Run lifecycle
# --------------------------------------------------------------------------- #

def start_run() -> None:
    """Launch the pipeline in the background thread and switch to the run view."""
    steps = build_steps_from_session()
    if not steps:
        st.error("No valid steps configured. Fill prompts and targets in the builder.")
        return
    log_q: "queue.Queue[str]" = queue.Queue()
    events_q: "queue.Queue[tuple[str, object]]" = queue.Queue()
    stop_evt = threading.Event()
    manual_state = make_manual_state()

    orchestrator = Orchestrator(SETTINGS, key_manager, log=log_q.put)
    thread = run_orchestrator_in_background(
        orchestrator,
        steps,
        log_sink=log_q.put,
        done_sink=lambda results: events_q.put(("done", results)),
        error_sink=lambda exc: events_q.put(("error", exc)),
        stop_event=stop_evt,
        manual_state=manual_state,
    )
    st.session_state["exec_state"] = {
        "running": True,
        "thread": thread,
        "log_queue": log_q,
        "events": events_q,
        "stop_event": stop_evt,
        "orchestrator": orchestrator,
        "manual_state": manual_state,
        "log_lines": [],
        "results": None,
        "error": None,
        "started_at": time.time(),
    }
    st.session_state["view"] = "running"
    st.rerun()


def save_manual_upload(exec_state: dict, file_key: str, uploaded) -> None:
    """Persist a manual-upload file into workspace/uploads/ and signal the orchestrator."""
    uploads_dir = BASE_DIR / "workspace" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / uploaded.name
    with open(dest, "wb") as fh:
        fh.write(uploaded.getbuffer())
    manual_state = exec_state.get("manual_state") or {}
    paths = list(manual_state.get("uploaded_paths") or [])
    paths.append(str(dest))
    manual_state["uploaded_paths"] = paths
    ready = manual_state.get("ready")
    if isinstance(ready, threading.Event):
        ready.set()


def find_latest_video(files: List[str]) -> Optional[str]:
    """Pick the newest video file from a list of step outputs (final video)."""
    video_exts = (".mp4", ".mov", ".avi", ".mkv", ".webm")
    videos = [fp for fp in files if Path(fp).suffix.lower() in video_exts and os.path.isfile(fp)]
    if not videos:
        return None
    return max(videos, key=lambda fp: Path(fp).stat().st_mtime)


def step_status_line(line: str) -> dict:
    """Parse a log line into a structured console row."""
    row: dict = {"step": "", "account": "", "status": "info", "text": line}
    m = re.match(r"^🔄 Step (\d+)", line)
    if m:
        row["step"] = f"Step {m.group(1)}"
    m = re.search(r"account '([^']+)'", line)
    if m:
        row["account"] = m.group(1)
    if not row["account"]:
        m = re.search(r"→\s*([^\s]+)", line)
        if m:
            row["account"] = m.group(1).strip("'\"")
    if "✅" in line or "📁 Received" in line:
        row["status"] = "success"
    elif any(s in line for s in ("⚠️", "⛔", "❌", "failed", "limit", "exhausted")):
        row["status"] = "danger"
    elif "⏸" in line or "waiting" in line.lower():
        row["status"] = "warning"
    return row


def render_log_console(log_lines: List[str]) -> None:
    """Real-time log console: step, active Claude account, status per line."""
    st.markdown("#### 📋 Real-time Log Console")
    with st.container(border=True, height=340):
        if not log_lines:
            st.caption("Waiting for pipeline events…")
            return
        for line in log_lines[-120:]:
            row = step_status_line(line)
            badge = {"info": "▪️", "success": "🟢", "danger": "🔴", "warning": "🟡"}[row["status"]]
            prefix = f"**{row['step']}**" if row["step"] else ""
            account = f" · `{row['account']}`" if row["account"] else ""
            st.markdown(f"{badge} {prefix}{account} · {row['text']}")


def render_running() -> None:
    """The active run / thread view with manual uploads, video player and log console."""
    exec_state = st.session_state.get("exec_state")
    if not exec_state:
        st.session_state["view"] = "home"
        st.rerun()
        return

    running = bool(exec_state.get("running"))
    manual_state = exec_state.get("manual_state") or {}
    manual_active = bool(manual_state.get("active"))

    # ---------- Centered title + 9-step gallery ----------
    st.markdown(
        "<div style='text-align: center; padding: 0.5rem 0 0.25rem;'>"
        "<h1 style='font-size: 2.2rem; font-weight: 800; margin: 0;'>Auto-Dubber Workstation</h1>"
        "<p style='color: var(--text-secondary); font-size: 0.95rem; margin: 0.25rem 0 1rem;'>"
        "9-Step Semi-Automated Video Translation & Dubbing Pipeline</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Image gallery: 3-column grid of workflow images
    gallery_images = [
        ("/home/shafin/Desktop/BlueprintTube_Project/Claude-workstation/workspace/WhatsApp Image 2026-08-29 at 4.35.05 AM.jpeg", "Steps 1-3: Video Upload → SRT → Claude Translation"),
        ("/home/shafin/Desktop/BlueprintTube_Project/Claude-workstation/workspace/WhatsApp Image 2026-08-29 at 6.08.13 AM.jpeg", "Steps 4-6: Audio Upload → Claude Sync → Subtitles & Effects"),
        ("/home/shafin/Desktop/BlueprintTube_Project/Claude-workstation/workspace/WhatsApp Image 2026-08-29 at 6.08.14 AM.jpeg", "Steps 7-9: QA Check → Fix Errors → Dynamic Split Failover"),
    ]
    gcols = st.columns(3, gap="small")
    for ci, (img_path, caption) in enumerate(gallery_images):
        with gcols[ci]:
            if os.path.isfile(img_path):
                st.image(img_path, use_container_width=True)
            st.caption(caption)

    st.markdown("---")

    if running:
        log_lines: List[str] = exec_state.setdefault("log_lines", [])
        log_lines.extend(str(item) for item in drain_queue(exec_state["log_queue"]))
        for event, payload in drain_queue(exec_state["events"]):
            if event == "done":
                exec_state["results"] = payload
                log_lines.append(f"🏁 Completed {len(payload)} step(s).")
            elif event == "error":
                exec_state["error"] = payload
                log_lines.append(f"❌ {payload}")

        # ---------------- Manual upload pause ----------------
        if manual_active:
            step_id = manual_state.get("step_id")
            label = manual_state.get("label", "Upload file")
            exts = str(manual_state.get("expected_extensions", ".mp4,.mov,.avi,.mkv,.webm"))
            ext_list = [e.strip().lower() for e in exts.split(",") if e.strip()]
            file_key = str(manual_state.get("file_key", ""))

            with st.container(border=True):
                st.markdown(f"#### ⏸️ {label} required")
                st.caption(
                    f"**Step {step_id}** is waiting for a file. "
                    f"Expected: {', '.join(ext_list)}"
                )
                uploaded = st.file_uploader(
                    f"Upload {label}",
                    type=[e.lstrip(".") for e in ext_list],
                    key=f"manual_upload_{step_id}_{file_key}",
                )
                if uploaded is not None:
                    save_manual_upload(exec_state, file_key, uploaded)
                    st.success(f"📁 {uploaded.name} received — resuming pipeline…")
                    st.rerun()

        # ---------------- Live log console ----------------
        render_log_console(log_lines)

        # Stop button occupies the composer card slot while running.
        with st.container(border=True):
            if st.button("⛔ Stop Pipeline", key="sidebar_stop", help="Stop Pipeline"):
                stop_evt = exec_state.get("stop_event")
                if stop_evt is not None:
                    stop_evt.set()
                    st.caption("Stop requested — finishing current step...")

        thread = exec_state["thread"]
        if thread.is_alive():
            time.sleep(0.3)
            st.rerun()
        exec_state["running"] = False
        time.sleep(0.3)
        st.rerun()
    else:
        # Finished run: final video player + results + log console.
        if exec_state.get("error") is not None:
            st.error(f"Pipeline failed: {exec_state['error']}")
        elif exec_state.get("results") is not None:
            results: List[StepResult] = exec_state["results"]
            st.success(f"Pipeline finished in {time.time() - exec_state['started_at']:.1f}s.")

            orchestrator = exec_state.get("orchestrator")
            limit_accounts: List[str] = (
                list(getattr(orchestrator, "limit_accounts", [])) if orchestrator else []
            )
            if limit_accounts:
                st.error(
                    "⚠️ Accounts that hit limits during this run: "
                    + ", ".join(limit_accounts)
                )

            # ---------------- Final Video Player ----------------
            all_files: List[str] = []
            for r in results:
                all_files.extend(r.files)
            final_video = find_latest_video(all_files)
            if final_video:
                st.markdown("#### 🎬 Final Video")
                st.video(final_video)
                st.caption(f"Path: `{final_video}`")

            # ---------------- Per-step results ----------------
            for r in results:
                render_result_block(r)

        # ---------------- Log console after run ----------------
        log_lines = exec_state.get("log_lines") or []
        if log_lines:
            render_log_console(log_lines)

        if st.button("🔄 Reset", width="stretch"):
            st.session_state["exec_state"] = None
            st.session_state["view"] = "home"
            st.rerun()

        render_composer()


def render_history_run(name: str) -> None:
    """Read-only thread view of a saved run from workspace/exports/.

    The view shows every step file of the run (grouped by consecutive step
    numbers / close mtimes), each rendered with the same chat-block layout.
    """
    first = exports_dir / name
    if not first.is_file():
        st.error(f"History file not found: {name}")
    else:
        run_files = _run_for_file(first)
        st.caption(f"📜 {run_label(run_files)}")
        for fp in sorted(run_files, key=_step_index):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                st.warning(f"Could not read `{fp.name}`: {exc}")
                continue
            render_saved_data(data)

    if st.button("← Back to New Task", key="hist_back_btn"):
        st.session_state["view"] = "home"
        st.rerun()


# --------------------------------------------------------------------------- #
#  Custom collapsible side panel (replaces Streamlit's native sidebar)
#  Fully driven by st.session_state — independent of stHeader, so it keeps
#  working even when the native header is hidden entirely.
# --------------------------------------------------------------------------- #

sidebar_open = st.session_state.setdefault("sidebar_open", True)

if sidebar_open:
    nav_col, main_col = st.columns([0.24, 0.76], gap="small")
else:
    toggle_col, main_col = st.columns([0.04, 0.96], gap="small")

if sidebar_open:
    with nav_col:
        if st.button("«", key="collapse_sidebar", help="Collapse sidebar"):
            st.session_state["sidebar_open"] = False
            st.rerun()

        st.markdown(
            '<div class="brand-wordmark"><span class="brand-starburst">◆</span> Hybrid Workstation</div>',
            unsafe_allow_html=True,
        )

        if st.button("➕ New Task", key="new_task", width="stretch"):
            st.session_state["steps"] = []
            st.session_state["editing_step"] = None
            st.session_state["view"] = "home"
            st.rerun()

        search_q = st.text_input(
            "Search",
            placeholder="Search",
            key="sidebar_search",
            label_visibility="collapsed",
        )

        st.markdown("#### History")
        hist_runs = group_exports_by_run(list_export_files())
        if search_q:
            q = search_q.lower()
            hist_runs = [
                run
                for run in hist_runs
                if q in run_label(run).lower()
                or any(q in history_row_label(f).lower() for f in run)
            ]
        if not hist_runs:
            st.caption("No history yet.")

        current_group: Optional[str] = None
        for run in hist_runs:
            group = date_bucket_label(run[0])
            if group != current_group:
                st.markdown(
                    f'<div class="history-group-label">{group}</div>',
                    unsafe_allow_html=True,
                )
                current_group = group
            first = run[0]
            row_c, menu_c = st.columns([3.4, 0.8])
            if row_c.button(
                run_label(run), key=f"histrow_{first.name}", width="stretch"
            ):
                st.session_state["view"] = f"history:{first.name}"
                st.rerun()
            with menu_c:
                with st.popover("⋯", key=f"hismenu_{first.name}"):
                    if st.button("🗑️ Delete", key=f"hist_del_{first.name}"):
                        for fp in run:
                            fp.unlink(missing_ok=True)
                        st.rerun()

        st.markdown("---")
        running_now = bool(exec_state_global and exec_state_global.get("running"))
        st.markdown(f"**● {'Running' if running_now else 'Ready'}** · Local Workstation")
        if st.button("⚙️ Settings", key="open_settings", width="stretch"):
            st.session_state["settings_return_view"] = st.session_state.get("view", "home")
            st.session_state["view"] = "settings"
            st.rerun()
else:
    with toggle_col:
        if st.button("»", key="expand_sidebar", help="Expand sidebar"):
            st.session_state["sidebar_open"] = True
            st.rerun()

# --------------------------------------------------------------------------- #
#  View routing
# --------------------------------------------------------------------------- #

with main_col:
    view = st.session_state.setdefault("view", "home")

    if view == "home":
        render_home()
    elif view == "running":
        render_running()
    elif view == "settings":
        render_settings_view()
    elif view.startswith("history:"):
        render_history_run(view.removeprefix("history:"))
    else:
        render_home()

    st.caption("---")
    st.caption("Hybrid Multi-Agent Workstation · Playwright + google-genai · local-first")
