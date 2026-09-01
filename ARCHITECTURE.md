# 🤖 CL Multi-Agent Workstation — সম্পূর্ণ আর্কিটেকচার ডকুমেন্টেশন

এই ডকুমেন্টটি পুরো সিস্টেমের একটি **ধাপে-ধাপে আর্কিটেকচার ব্রেকডাউন**। এই ফাইলটি পড়লে যেকোনো ডেভেলপার বুঝতে পারবে — সিস্টেমে মোট কয়টি সেকশন/সাব-সেকশন আছে, প্রতিটা কীভাবে কাজ করে, এবং ব্যাকএন্ড থেকে ফ্রন্টএন্ড পর্যন্ত সব ডেটা কীভাবে প্রবাহিত হয়।

---

## ১. সিস্টেম ওভারভিউ

এটি একটি **হাইব্রিড মাল্টি-এজেন্ট ওয়ার্কস্টেশন** যা দুটি ভিন্ন AI ইঞ্জিনকে একসাথে একটি **সিকুয়েন্সিয়াল পাইপলাইন** (ধাপে ধাপে) হিসেবে চালায়:

| ইঞ্জিন | কীভাবে চলে | কিসের জন্য |
|---|---|---|
| **Claude Web Automation** | Playwright (real browser) দিয়ে Claude.ai-তে লগইন করা অ্যাকাউন্ট ব্যবহার করে | দীর্ঘ কাজ, ফাইল ডাউনলোড, ওয়েব আর্টিফ্যাক্ট |
| **Gemini API Engine** | google-genai SDK দিয়ে API কল | দ্রুত, অনেক স্টেপের কাজ, কী রোটেশন |

**ফ্রন্টএন্ড:** লোকাল Streamlit ড্যাশবোর্ড (http://localhost:8501) — **Claude.ai-এর মতো chat-style UI**, অ-টেকনিক্যাল ইউজারের জন্য সহজ, কিন্তু আন্ডারে অ্যাডভান্সড অটোমেশন।

**কোর ক্যাপাবিলিটিস:**
- Strict step-by-step অ্যাকাউন্ট হ্যান্ডওভার (প্রতিটি স্টেপ তার নির্ধারিত অ্যাকাউন্ট ব্যবহার করে)
- Mid-task quota failover (লিমিট এলে আংশিক আউটপুট সেভ করে পরের অ্যাকাউন্টে সুইচ)
- Gemini API কী রোটেশন (429/কোটা এলে অটো কী বদল)
- Output attribution (কোন অ্যাকাউন্ট/মডেল জেনারেট করেছে তা আউটপুটে লেখা থাকে)
- ফাইল ইন্টারসেপশন (Claude-র আর্টিফ্যাক্ট ডাউনলোড)
- Light/Dark থিম (Claude-এর warm palette)

---

## ২. সম্পূর্ণ প্রজেক্ট ডাইরেক্টরি স্ট্রাকচার

```
claude-workstation/
├── app.py                          # Streamlit UI (মূল এন্ট্রি পয়েন্ট — সব UI এখানে)
├── requirements.txt                # ডিপেন্ডেন্সি লিস্ট
├── USER_MANUAL.md                  # ইউজার ম্যানুয়াল (বাংলা)
├── ARCHITECTURE.md                 # এই ফাইলটি
├── config/
│   ├── api_keys.json               # Gemini কী + স্বাস্থ্য স্ট্যাটাস (JSON)
│   └── settings.json               # অ্যাপ কনফিগারেশন (JSON)
├── modules/
│   ├── __init__.py
│   ├── key_manager.py              # Gemini কী ম্যানেজমেন্ট + রোটেশন
│   ├── account_manager.py          # Playwright প্রোফাইল ম্যানেজমেন্ট
│   ├── claude_driver.py            # Playwright অটোমেশন (মূল ইঞ্জিন)
│   ├── gemini_driver.py            # Gemini API async র্যাপার
│   └── orchestrator.py             # পাইপলাইন রানার (UI ↔ ড্রাইভার ব্রিজ)
├── profiles/                       # Playwright persistent browser profiles
│   ├── <account_email>/            # (লগইন করা সেশন/কুকি সেভ থাকে)
│   └── ...
└── workspace/
    ├── downloads/
    │   └── step_N/                 # Claude থেকে ধরা ফাইল (PDF, CSV, ভিডিও...)
    └── exports/
        └── step_N.txt              # প্রতিটি স্টেপের আউটপুট (JSON)
```

---

## ৩. ফ্রন্টএন্ড — `app.py` (Streamlit UI)

`app.py` সম্পূর্ণ chat-interface স্টাইল। নিচে পুরো ফাইলের সেকশন-সাবসেকশন ব্রেকডাউন:

### ৩.১ গ্লোবাল সেকশন (ফাইলের উপরের অংশ)

| সাব-সেকশন | কাজ |
|---|---|
| **ইমপোর্ট** | streamlit, pandas, modules থেকে সব ক্লাস আমদানি |
| **কনফিগ লোডিং** | `load_settings()` → `config/settings.json` পড়ে `SETTINGS` ডিকশনারি তৈরি |
| **হেল্পার ফাংশন** | `run_coro()` (async→sync), `drain_queue()` (থ্রেড-সেফ কিউ), `build_steps_from_session()`, `styled_keys_frame()` (কালার-কোডেড টেবিল) |
| **সিঙ্গেলটন** | `@st.cache_resource` দিয়ে `get_key_manager()` ও `get_account_manager()` |

### ৩.২ থিম ও CSS সিস্টেম

| সাব-সেকশন | কাজ |
|---|---|
| **থিম স্টেট** | `st.session_state["theme"]` = `light`/`dark` (ডিফল্ট light) |
| **টোকেন ডিকশনারি** | `_THEME_TOKENS` — দুটি থিমের জন্য Claude-প্যালেট রং: bg-app `#F9F7F4`, bg-composer `#FFFFFF`, accent-brand (কমলা), accent-interactive (নীল `#2F6FED`) |
| **CSS টেমপ্লেট** | `_CSS_TEMPLATE` — string.Template দিয়ে থিম টোকেন সাবস্টিটিউট; **কোনো position:fixed/absolute নেই** — শুধু রং/রেডিয়াস/স্পেসিং |
| **ইনজেকশন** | `build_theme_css(theme)` → `st.markdown(..., unsafe_allow_html=True)` — প্রতি রিরানে থিম অনুযায়ী CSS তৈরি |
| **নেটিভ chrome লুকানো** | `#MainMenu`, `footer`, `stDecoration`, `stToolbar`, `header[stHeader]` — সব hidden (কাস্টম সাইড প্যানেল ব্যবহার করায় নিরাপদ) |

### ৩.৩ মডিউল-লেভেল স্টেট ও কনস্ট্যান্ট

| নাম | কাজ |
|---|---|
| `health` / `profiles` | কী-পুল স্ট্যাটাস ও অ্যাকাউন্ট লিস্ট (সাইড প্যানেল/স্ট্যাটাসে ব্যবহৃত) |
| `exports_dir` / `downloads_dir` | ওয়ার্কস্পেস পাথ |
| `CLAUDE_MODEL_OPTIONS` | `["Fable 5", "Opus 5", "Sonnet 5", "Haiku 4.5"]` |
| `EFFORT_OPTIONS` | `["Low", "Medium", "High", "Extra", "Max"]` |
| `FILE_MODE_LABELS` | pass-files মোডের বাংলা/ইংরেজি লেবেল |

### ৩.৪ হিস্ট্রি হেল্পার (sidebar-এর জন্য)

| ফাংশন | কাজ |
|---|---|
| `list_export_files()` | exports ফোল্ডারের সব `step_*.txt` (mtime অনুসারে) |
| `group_exports_by_run()` | **এক রানের সব স্টেপ ফাইল এক গ্রুপে** — ধারাবাহিক step নম্বর + ১২০ সেকেন্ডের মধ্যে mtime → এক রান |
| `run_label()` | `"Run · 2 steps · 14:22"` ফরম্যাট |
| `date_bucket_label()` | Today / Yesterday / Previous 7 Days / Previous 30 Days / Older |
| `history_row_label()` | এক্সপোর্ট JSON থেকে স্টেপ লেবেল |

### ৩.৫ চ্যাট থ্রেড রেন্ডারিং (লাইভ + হিস্ট্রি শেয়ার করা)

| ফাংশন | কাজ |
|---|---|
| `split_logs_by_step()` | অর্কেস্ট্রেটরের ফ্ল্যাট লগ লাইনকে `🔄 Step N:` অনুযায়ী পার-স্টেপ সেগমেন্টে ভাগ |
| `render_saved_data()` | হিস্ট্রি JSON → `st.chat_message("assistant")` ব্লক (হেডার + আউটপুট + ফাইল ডাউনলোড বাটন) |
| `render_result_block()` | লাইভ `StepResult` → একই chat ব্লক লেআউট |

### ৩.৬ Settings ভিউ (পূর্ণ-পেজ)

| ফাংশন | কাজ |
|---|---|
| `render_settings_view()` | বাম left-nav (radio) + ডান কনটেন্ট — ৪টি সেকশন |
| `render_api_keys()` | **Gemini API Keys** — কী পেস্ট, Save & Test, Clear pool, Key Pool টেবিল, Retest, Reset failed |
| `render_claude_accounts()` | **Claude Profiles** — Add New Account (Create & Login), auth polling, Connected Accounts (Rename/Remove) |
| `render_data_section()` | **Data** — Downloaded files লিস্ট + Clear History (confirm checkbox + ২টি ডিলিট বাটন) |
| `render_preferences()` | **Preferences** — Light/Dark থিম টগল |

### ৩.৭ কম্পোজার (নতুন টাস্ক স্ক্রিন)

| ফাংশন | কাজ |
|---|---|
| `step_chip_label()` | চিপ লেবেল: `Step 1 · Claude · account_1` (এডিট মোডে `✎` prefix) |
| `load_step_into_composer()` | চিপ ক্লিকে স্টেপের সেটিংস কম্পোজারে লোড (এডিট) |
| `handle_composer_submit()` | **on_click callback** — কম্পোজার টেক্সট → steps লিস্টে যোগ/আপডেট, টেক্সট ক্লিয়ার |
| `render_composer()` | চিপ স্ট্রিপ + Run বাটন + **কম্পোজার কার্ড** (টেক্সট এরিয়া + কন্ট্রোল রো এক কার্ডে) |
| `render_home()` | খালি অবস্থায় **হিরো হেডিং** ("CL Multi-Agent Workstation") + কম্পোজার |

**কম্পোজার কার্ডের ভেতরের কন্ট্রোল রো (Claude-এর model-switcher-এর মতো):**

| এলিমেন্ট | কাজ |
|---|---|
| 📎 attach আইকন | "Use files from previous step" চালু থাকলে নীল হাইলাইট (কসমেটিক) |
| Engine pill (popover) | `Claude (Web Chat)` / `Gemini (API)` radio |
| Account/Model pill (popover) | Claude: account + model selectbox; Gemini: model selectbox |
| ⚙️ Advanced pill (popover) | Effort radio (Low–Max, Medium = Default) + Thinking toggle + Use-files selectbox + Max Retries stepper |
| ➤ send বাটন (বৃত্তাকার) | স্টেপ যোগ/আপডেট করে |

### ৩.৮ রান লাইফসাইকেল

| ফাংশন | কাজ |
|---|---|
| `start_run()` | `build_steps_from_session()` → ব্যাকগ্রাউন্ড থ্রেডে `run_orchestrator_in_background()` চালু → `view="running"` |
| `render_running()` | লাইভ লগ (পার-স্টেপ chat ব্লকে) → স্টপ বাটন (কম্পোজার স্লটে) → সম্পন্ন হলে ফলাফল ব্লক + লিমিট ব্যানার + রিসেট + কম্পোজার |
| `render_history_run()` | হিস্ট্রি রানের সব স্টেপ ফাইল read-only থ্রেডে |

### ৩.৯ কাস্টম সাইড প্যানেল (নেটিভ sidebar নেই!)

| সাব-সেকশন | কাজ |
|---|---|
| **`sidebar_open` স্টেট** | `st.session_state` — True হলে প্যানেল দৃশ্যমান (ডিফল্ট), False হলে সংকুচিত |
| **লেআউট** | খোলা: `st.columns([0.24, 0.76])`; বন্ধ: `st.columns([0.04, 0.96])` — **নেটিভ st.sidebar সম্পূর্ণ বাদ** |
| **« / » বাটন** | ghost-স্টাইল টগল বাটন (title="Collapse/Expand sidebar") |
| **nav_col কনটেন্ট** | ◆ wordmark (কমলা starburst) + ➕ New Task + 🔍 Search + 📜 History (date-গ্রুপড, প্রতি রানে এক এন্ট্রি) + ফুটার (● Ready/Running + ⚙️ Settings) |
| **main_col** | ভিউ রাউটিংয়ের পুরো কনটেন্ট |

### ৩.১০ ভিউ রাউটিং

```
view = st.session_state["view"]  (ডিফল্ট "home")

"home"      → render_home()          # নতুন টাস্ক: হিরো + কম্পোজার
"running"   → render_running()       # সক্রিয়/সম্পন্ন রান থ্রেড
"settings"  → render_settings_view() # পূর্ণ-পেজ Settings
"history:X" → render_history_run()   # read-only রান থ্রেড
```

---

## ৪. ব্যাকএন্ড মডিউলসমূহ — বিস্তারিত

### ৪.১ `modules/key_manager.py` — Gemini কী ম্যানেজার

**ক্লাস/অবজেক্ট:**

| নাম | ধরন | কাজ |
|---|---|---|
| `APIKeyRecord` | dataclass | একটি কী-র রেকর্ড: `key`, `status`, `usage_count`, `last_tested`, `last_error`, `working_model` |
| `KeyManager` | class | পুরো কী পুলের মালিকানা |
| `AllKeysExhaustedError` | exception | কোনো healthy কী না থাকলে |
| `is_rate_limit_error()` / `is_auth_error()` / `is_model_error()` | ফাংশন | SDK এরর ক্লাসিফাই করা |

**মূল ফাংশন:**

| ফাংশন | কাজ |
|---|---|
| `load_keys()` / `save_keys()` | `config/api_keys.json`-এ লোড/সেভ (অ্যাটমিক রাইট) |
| `set_keys(raw)` | পেস্ট করা কী যোগ (ডুপ্লিকেট স্কিপ, `AIzaSy...` ও `AQ...` ফরম্যাট) |
| `test_all_keys()` | ৮টার কনকারেন্সি — একাধিক মডেল চেষ্টা (`gemini-3.6-flash` → `1.5-flash`), কাজ করা মডেল `working_model` |
| `next_key()` | **রাউন্ড-রবিন** — শুধু active কী থেকে পরেরটি |
| `mark_failed()` | কী `429`/`exhausted` চিহ্নিত করে পুল থেকে বাদ |
| `reset_key()` / `clear_keys()` | এক বা সব কী রিসেট |

### ৪.২ `modules/account_manager.py` — প্রোফাইল ম্যানেজার

| ফাংশন | কাজ |
|---|---|
| `list_profiles()` | `profiles/`-এর সব ডিরেক্টরি নাম |
| `ensure_profile(name)` | নাম ভ্যালিডেট করে ডিরেক্টরি তৈরি |
| `rename_profile(old, new)` | প্রোফাইল রিনেম (সেশনে স্থায়ী) |
| `delete_profile(name)` | রিকার্সিভ ডিলিট |
| `next_profile_after(current, exclude)` | লিমিটে সুইচ করার জন্য পরের healthy প্রোফাইল (রাউন্ড-রবিন) |
| `launch_authenticator(name)` | headful ব্রাউজার খুলে ম্যানুয়াল লগইন → উইন্ডো বন্ধ করলে কুকি সেভ |
| `stealth_args()` / `user_agent()` / `webdriver_hide_js()` | স্টেলথ কনফিগ (Cloudflare বাইপাস) |

### ৪.৩ `modules/claude_driver.py` — Claude ওয়েব অটোমেশন (মূল ইঞ্জিন)

**মেসেজ ফ্লো — `send_message()`:**

```
১. লিমিট প্রি-চেক
২. select_model_and_style(model, effort, thinking)   ← মডেল/এফোর্ট/থিংকিং সিলেকশন
৩. _attach_files()                                    ← ফাইল অ্যাটাচ
৪. _focus_composer()                                  ← ইন্টারসেপশন-বাইপাস ফোকাস
৫. টাইপ + Enter
৬. _wait_for_output_completion()                      ← সম্পূর্ণ শেষ হওয়া পর্যন্ত অপেক্ষা
৭. _extract_latest_response()                         ← শেষ সম্পন্ন মেসেজের টেক্সট
৮. _capture_downloads()                               ← আর্টিফ্যাক্ট ডাউনলোড
```

**সাব-সিস্টেম:**

| সাব-সিস্টেম | ফাংশন | কীভাবে কাজ করে |
|---|---|---|
| **মডেল সিলেকশন** | `_click_model_trigger()` | regex দিয়ে ট্রিগার খোঁজে (`Sonnet 5\|Opus 5\|...`) — লেবেল ডায়নামিক ("Sonnet 5 High") |
| **এফোর্ট + থিংকিং** | `_select_effort_and_thinking()` | Trigger → "Effort" ক্লিক → wait_for_timeout(500) → Thinking টগল → এফোর্ট লেভেল |
| **কমপ্লিশন ডিটেকশন** | `_wait_for_output_completion()` | "Stop generating" উধাও + টেক্সট ৩বার স্থির + Copy বাটন → সম্পন্ন |
| **আউটপুট কালেকশন** | `_extract_latest_response()` | শেষ Copy-বাটন-যুক্ত মেসেজের innerText (JS) |
| **ফাইল ক্যাপচার** | `_capture_downloads()` | `button[aria-label*="Download"]` → `expect_download(15000)` → `workspace/downloads/step_N/` |
| **লিমিট ডিটেকশন** | `_detect_limit()` | `settings.limit_detection_phrases` body টেক্সটে খোঁজে |
| **পপওভার ক্লিনআপ** | `_close_popovers()` | Escape×2 + হেডার ক্লিক — ওভারলে ইন্টারসেপশন রোধ |
| **কম্পোজার ফোকাস** | `_focus_composer()` | ক্লিক → Escape → `focus()` → `click(force=True)` — ৩-স্তরের বাইপাস |

### ৪.৪ `modules/gemini_driver.py` — Gemini API async র্যাপার

| ফাংশন | কাজ |
|---|---|
| `generate(prompt, model, file_paths, ...)` | 429 এলে `next_key()` → `mark_failed()` → রিট্রাই লুপ |
| `_candidate_models()` | মডেল ফলব্যাক: requested → working_model → fallback list |
| `_build_contents()` | ফাইলকে `Part.from_bytes()` + টেক্সট → `Content` |

### ৪.৫ `modules/orchestrator.py` — পাইপলাইন রানার (মস্তিষ্ক)

| ফাংশন | কাজ |
|---|---|
| `run(steps, stop_event)` | সিকুয়েন্সিয়াল: স্টেপ → ইঞ্জিন → `_export()` → `{previous_output}` পাস |
| `_run_claude_step()` | **Strict handover** + failover: লিমিট → partial output → `next_profile_after()` → প্রসঙ্গ ইনজেক্ট → শেষে ড্রাইভার বন্ধ |
| `_run_gemini_step()` | `gemini.generate()` + attribution |
| `_with_attribution()` | `[Generated by: account_1, account_2 using Sonnet 5 · effort: High]` |
| `_render_prompt()` | `{previous_output}`, `{file_path}`, `{file_paths}` সাবস্টিটিউট |
| `_export()` | `workspace/exports/step_N.txt` — output + মেটাডেটা JSON |
| `run_orchestrator_in_background()` | **Async/sync ব্রিজ** — আলাদা থ্রেড + `asyncio.new_event_loop()` |

---

## ৫. ডেটা ফ্লো (এক নজরে)

```
[কম্পোজার (home ভিউ)]
   প্রম্পট + পিল সেটিংস → ➤ send → st.session_state["steps"] (chip আকারে)
        │
        ▼
[▶️ Run Pipeline বাটন]
   build_steps_from_session() → List[PipelineStep]
        │
        ▼
[Background thread + asyncio loop]
   Orchestrator.run(steps)
        │
        ├── Claude স্টেপ → ClaudeDriver → Playwright → Claude.ai
        │        ├── মডেল/এফোর্ট/থিংকিং সিলেকশন
        │        ├── প্রম্পট + ফাইল অ্যাটাচ
        │        ├── সম্পন্ন পর্যন্ত অপেক্ষা → আউটপুট কালেক্ট
        │        ├── আর্টিফ্যাক্ট ডাউনলোড → workspace/downloads/step_N/
        │        └── লিমিট এলে → failover → পরের অ্যাকাউন্ট
        │
        └── Gemini স্টেপ → GeminiDriver → google-genai → 429 হলে কী রোটেশন
        │
        ▼
   StepResult (attribution সহ) → _export() → workspace/exports/step_N.txt
        │
        ▼
[running ভিউ]
   লাইভ লগ (পার-স্টেপ chat ব্লক) → ফলাফল ব্লক + লিমিট ব্যানার + ডাউনলোড বাটন
        │
        ▼
[সাইড প্যানেল 📜 History]
   date-গ্রুপড রান লিস্ট → ক্লিকে read-only থ্রেড (এক রানের সব স্টেপ)
```

---

## ৬. কনফিগারেশন ফাইল

### `config/settings.json` — ১৭টি কী:

| কী | ডিফল্ট | অর্থ |
|---|---|---|
| `claude_url` | `https://claude.ai` | লক্ষ্য URL |
| `headless` | `false` | ব্রাউজার হেডলেস চলবে কিনা |
| `stealth` | `true` | অ্যান্টি-বট স্টেলথ |
| `poll_interval_sec` | `1.0` | DOM পোলিং ব্যবধান |
| `stability_checks_required` | `3` | আউটপুট স্থির প্রমাণের চেক সংখ্যা |
| `max_stop_generating_wait_sec` | `600` | জেনারেশন টাইমআউট |
| `gemini_default_model` | `gemini-3.6-flash` | ডিফল্ট Gemini মডেল |
| `gemini_test_models` | ৫টি মডেল | কী টেস্টে ফলব্যাক অর্ডার |
| `profiles_dir` / `downloads_dir` / `exports_dir` | পাথ | ডিরেক্টরি |
| `limit_detection_phrases` | ৭টি ফ্রেজ | লিমিট ব্যানার চেনার টেক্সট |
| `known_gemini_models` | ৫টি | UI ড্রপডাউনের মডেল |

### `config/api_keys.json` — কী রেকর্ড ফরম্যাট:

```json
[
  {
    "key": "AIzaSy...",
    "status": "active",
    "usage_count": 12,
    "last_tested": "2026-08-29T00:00:00+00:00",
    "last_error": "",
    "working_model": "gemini-3.6-flash"
  }
]
```

`status`: `active` / `429` / `exhausted`

---

## ৭. Async/Sync ব্রিজ — সবচেয়ে গুরুত্বপূর্ণ অংশ

```
Streamlit (sync, main thread)
        │
        ▼  run_orchestrator_in_background()
┌───────────────────────────────┐
│  Background daemon thread     │
│  loop = asyncio.new_event_loop()│
│  loop.run_until_complete(     │
│      orchestrator.run(...)    │
│  )                            │
│  loop.close()                 │
└───────────────────────────────┘
        │  log_sink / events (queue.Queue)
        ▼
Streamlit UI  (st.status + st.rerun polling)
```

- **কেন দরকার:** Streamlit sync, Playwright/Gemini async — এক লুপ শেয়ার করলে "Event loop is closed" এরর।
- **যোগাযোগ:** থ্রেড → `queue.Queue` → UI `drain_queue()` দিয়ে পড়ে। থ্রেড কখনো সরাসরি `st.session_state` লেখে না।

---

## ৮. সেকশন-সাবসেকশন তালিকা (এক নজরে)

| স্তর | সেকশন | সাব-সেকশন সংখ্যা |
|---|---|---|
| Frontend | থিম ও CSS | 5 (টোকেন, টেমপ্লেট, ইনজেকশন, chrome-hiding, hero) |
| Frontend | কাস্টম সাইড প্যানেল | 5 (toggle, wordmark, New Task, Search+History, footer+gear) |
| Frontend | home ভিউ | 5 (হিরো, চিপ, Run, কম্পোজার কার্ড, কন্ট্রোল রো) |
| Frontend | কম্পোজার কন্ট্রোল রো | 5 (attach, Engine, Account/Model, Advanced, send) |
| Frontend | running ভিউ | 5 (লাইভ থ্রেড, স্টপ, ফলাফল, ব্যানার, রিসেট+কম্পোজার) |
| Frontend | Settings ভিউ | 4 (API Keys, Profiles, Data, Preferences) |
| Frontend | history ভিউ | 2 (রান থ্রেড, ব্যাক বাটন) |
| Backend | `key_manager.py` | ১ ক্লাস + ১ dataclass + ৩ ক্লাসিফায়ার |
| Backend | `account_manager.py` | ১ ক্লাস + ~৯ ফাংশন |
| Backend | `claude_driver.py` | ১ ক্লাস + ~২০ ফাংশন |
| Backend | `gemini_driver.py` | ১ ক্লাস + ৩ ফাংশন |
| Backend | `orchestrator.py` | ১ ক্লাস + ৩ ডেটা মডেল + ব্রিজ |
| Config | `settings.json` | ১৭ কী |
| Config | `api_keys.json` | কী-রেকর্ড লিস্ট |

---

## ৯. গুরুত্বপূর্ণ ডিজাইন সিদ্ধান্ত

1. **নেটিভ `st.sidebar` নেই** — কাস্টম columns-ভিত্তিক প্যানেল (`sidebar_open` flag) — Streamlit-এর DOM বদলালেও টগল ভাঙে না।
2. **`stHeader` সম্পূর্ণ hidden** — কারণ টগল নিজের হাতে, নেটিভ হেডারের ওপর নির্ভরতা শূন্য।
3. **Settings পূর্ণ-পেজ ভিউ** (dialog নয়) — X-বাটন/ওভারলে quirks সম্পূর্ণ এড়ানো।
4. **Persistent contexts (প্রোফাইল)** — প্রতিটি অ্যাকাউন্ট আলাদা `user_data_dir` → সেশন সেভ থাকে।
5. **Strict handover** — প্রতিটি স্টেপ নিজের অ্যাকাউন্ট, শেষে ড্রাইভার বন্ধ।
6. **Partial output capture** — লিমিটে আংশিক কাজ হারায় না।
7. **Stability-checked completion** — "Stop গেছে + টেক্সট স্থির" — আধা-জেনারেটেড টেক্সট কখনো কালেক্ট হয় না।
8. **`width="stretch"`** — `use_container_width` deprecation-মুক্ত (Streamlit 1.62+)।

---

## ১০. কীভাবে ভেরিফাই করবেন

```bash
cd /home/shafin/Desktop/BlueprintTube_Project/Claude-workstation
source .venv/bin/activate
python -m py_compile app.py modules/*.py        # সিনট্যাক্স চেক
streamlit run app.py                             # UI চালু
```

- কোনো `NameError`/`ImportError` নেই
- http://localhost:8501 খুললে: বামে কাস্টম প্যানেল (« বাটন দিয়ে collapse), ডানে home ভিউ (হিরো + কম্পোজার)
- Settings gear → ৪টি সেকশন কাজ করে
- স্টেপ যোগ → Run → running ভিউ → ফলাফল + হিস্ট্রিতে রান দেখা যায়
