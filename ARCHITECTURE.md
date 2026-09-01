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
- **🎬 9-Step Auto-Dub ভিডিও পাইপলাইন** (সেমি-অটোমেটেড ভিডিও অনুবাদ ও ডাবিং)
- **GitHub Single Source of Truth** (ইনস্ট্রাকশন `.md` ফাইল GitHub থেকে fetch করে)
- **Manual-pause স্টেপ** (ভিডিও/SRT/অডিও আপলোডের জন্য UI-তে পজ)
- **QA → Fix লুপ** (Claude QA অ্যাজেন্ট এরর লিস্ট বানায়, ফিক্স অ্যাজেন্ট সেগুলো ঠিক করে)
- **Dynamic Split Failover** (লিমিট এলে টাস্ক ২→৩টি প্যারালাল Claude অ্যাকাউন্টে ভাগ)

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
├── workspace/
│   ├── downloads/
│   │   └── step_N/                 # Claude থেকে ধরা ফাইল (PDF, CSV, ভিডিও...)
│   ├── exports/
│   │   └── step_N.txt              # প্রতিটি স্টেপের আউটপুট (JSON)
│   ├── uploads/                    # ম্যানুয়াল আপলোড করা ফাইল (ভিডিও, SRT, অডিও)
│   └── .github_cache/              # GitHub থেকে fetched instruction .md ফাইলের ক্যাশে
└── Update data/                    # ভিডিও পাইপলাইনের জন্য ইনস্ট্রাকশন ও রেফারেন্স ফাইল
    ├── video_dialogue_screenshot_hindi_translation_instructions.md
    ├── video_sync_instructions.md
    └── Hindi subtitles+Effect Ad/
        ├── PLAYBOOK.md
        ├── PROJECT_STATE.md
        ├── detect_subtitle_segments.py
        ├── render_video.py
        ├── render_video-1.py
        └── lines_data_CHECKPOINT_0-65s.py
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
| `render_preferences()` | **Preferences** — Light/Dark থিম টগল + **🌐 GitHub Instructions** কনফিগ + **🎬 Video Pipeline Defaults** |

**`render_preferences()`-এর নতুন সাব-সেকশন:**

| সাব-সেকশন | কীভাবে কাজ করে |
|---|---|
| **🌐 GitHub Instructions (Single Source of Truth)** | ৩টি text input: Raw base URL (যেমন `https://raw.githubusercontent.com/your-org/your-repo/main`), Translation instructions path, Sync instructions path, Subtitles/Effects path → **💾 Save GitHub config** → `settings.json`-এ `github_instructions_repo` + `github_instruction_files` সেভ |
| **🎬 Video Pipeline Defaults** | Claude model selectbox (Fable/Opus/Sonnet/Haiku), Effort radio (Low–Max), Thinking toggle → **💾 Save pipeline defaults** → `video_pipeline_model` / `video_pipeline_effort` / `video_pipeline_thinking` সেভ |

### ৩.৭ কম্পোজার (নতুন টাস্ক স্ক্রিন)

| ফাংশন | কাজ |
|---|---|
| `step_chip_label()` | চিপ লেবেল: `Step 1 · Claude · account_1` (এডিট মোডে `✎` prefix) |
| `load_step_into_composer()` | চিপ ক্লিকে স্টেপের সেটিংস কম্পোজারে লোড (এডিট) |
| `handle_composer_submit()` | **on_click callback** — কম্পোজার টেক্সট → steps লিস্টে যোগ/আপডেট, টেক্সট ক্লিয়ার |
| `render_composer()` | চিপ স্ট্রিপ + Run বাটন + **কম্পোজার কার্ড** (টেক্সট এরিয়া + কন্ট্রোল রো এক কার্ডে) |
| `render_home()` | খালি অবস্থায় **হিরো হেডিং** ("CL Multi-Agent Workstation") + **🎬 Video Dubbing Pipeline preset বাটন** + কম্পোজার |

**home ভিউ-র নতুন এলিমেন্ট (খালি অবস্থায়):**

| এলিমেন্ট | কীভাবে কাজ করে |
|---|---|
| **🚀 Start Video Dubbing Pipeline বাটন** | `start_video_pipeline()` কল করে → `create_video_dubbing_pipeline()` দিয়ে ৯টি স্টেপ বানায় → ব্যাকগ্রাউন্ড থ্রেডে চালায় → `view="running"` |
| ক্যাপশন | "Requires: 1+ Claude profile(s) connected, GitHub instruction URLs configured in ⚙️ Settings → Preferences" |

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
| `start_video_pipeline()` | **9-স্টেপ অটো-ডাব পাইপলাইন** চালু করে — `create_video_dubbing_pipeline()` + `github_raw_url()` দিয়ে instruction URL বানায় → `manual_state` তৈরি করে ব্যাকগ্রাউন্ডে চালায় |
| `render_running()` | **সেন্টারড টাইটেল + ৩-কলাম ইমেজ গ্যালারি** → ম্যানুয়াল আপলোড পজ → লাইভ লগ কনসোল → স্টপ বাটন → শেষে **ফাইনাল ভিডিও প্লেয়ার** + ফলাফল ব্লক + লগ কনসোল |
| `render_history_run()` | হিস্ট্রি রানের সব স্টেপ ফাইল read-only থ্রেডে |
| `save_manual_upload()` | ম্যানুয়াল আপলোডের ফাইল `workspace/uploads/`-এ সেভ → `manual_state["uploaded_paths"]`-এ যোগ → `ready` event set |
| `find_latest_video()` | স্টেপ আউটপুট ফাইল থেকে সবচেয়ে নতুন ভিডিও (.mp4/.mov/...) খুঁজে |
| `step_status_line()` | লগ লাইন পার্স করে `{step, account, status, text}` রো বানায় |
| `render_log_console()` | স্ক্রলেবল লগ কনসোল — প্রতি লাইনে কালার-কোডেড ব্যাজ (🟢/🔴/🟡) + স্টেপ নম্বর + সক্রিয় অ্যাকাউন্ট |

**`render_running()`-এর নতুন সাব-সেকশন:**

| সাব-সেকশন | কীভাবে কাজ করে |
|---|---|
| **সেন্টারড টাইটেল** | `st.markdown` দিয়ে HTML — "**Auto-Dubber Workstation**" h1 (2.2rem, bold, centered) + সাবটাইটেল "9-Step Semi-Automated Video Translation & Dubbing Pipeline" |
| **ইমেজ গ্যালারি** | ৩টি WhatsApp ওয়ার্কফ্লো ইমেজ ৩-কলাম grid-এ — ক্যাপশনসহ: Steps 1-3 (আপলোড→SRT→Translation), Steps 4-6 (Audio→Sync→Subtitles), Steps 7-9 (QA→Fix→Split) |
| **ম্যানুয়াল আপলোড পজ** | `manual_state["active"]` True হলে: লেবেল + expected extensions দেখায় → `st.file_uploader` → ফাইল দিলে `save_manual_upload()` → `st.rerun()` |
| **লাইভ লগ কনসোল** | `render_log_console(log_lines)` — `st.container(border=True, height=340)` স্ক্রলেবল বক্স, সর্বশেষ ১২০ লাইন |
| **ফাইনাল ভিডিও প্লেয়ার** | সম্পন্ন হলে `find_latest_video(all_files)` → `st.video(final_video)` + পাথ ক্যাপশন |
| **QA এরর লিস্ট** | QA স্টেপের `error_list` থাকলে `st.expander("🔎 QA Error List")` → `st.code()` |

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

**ডেটা মডেল (৩টি dataclass):**

| মডেল | ফিল্ডসমূহ | কাজ |
|---|---|---|
| `PipelineStep` | `step_id`, `engine`, `target`, `prompt_template`, `pass_files`, `max_retries`, `timeout_sec`, `model_name`, `performance_style`, `effort`, `thinking`, `step_type`, `manual_label`, `expected_extensions`, `file_key`, `github_instruction_url`, `error_context_key`, `fixed_step_id`, `split_parallel_accounts`, `split_retry_accounts`, `split_chunk_index`, `split_total_chunks` | পাইপলাইনের একটি নোড |
| `StepResult` | `step_id`, `engine`, `target`, `output`, `files`, `account_switches`, `model_name`, `performance_style`, `effort`, `thinking`, `accounts_used`, `step_type`, `error_list`, `split_results` | একটি স্টেপের আউটপুট |
| `ManualUploadDef` | `step_id`, `label`, `file_key`, `expected_extensions`, `description` | ম্যানুয়াল আপলোড স্টেপের সংজ্ঞা |

**নতুন স্টেপ টাইপ (স্টেপ টাইপ কনস্ট্যান্ট):**

| কনস্ট্যান্ট | মান | অর্থ |
|---|---|---|
| `STEP_TYPE_MANUAL` | `"manual"` | UI-তে পজ — ইউজার ফাইল আপলোড করবে |
| `STEP_TYPE_QA` | `"qa"` | QA ক্লড অ্যাজেন্ট — ভিডিও চেক করে এরর লিস্ট বানায় |
| `STEP_TYPE_FIX` | `"fix"` | ফিক্স ক্লড অ্যাজেন্ট — QA-এর এরর লিস্ট ইনপুট পেয়ে ঠিক করে |
| `STEP_TYPE_SPLIT` | `"split"` | ডাইনামিক স্প্লিট — লিমিট এলে ২→৩ প্যারালাল অ্যাকাউন্ট |

**মূল ফাংশন:**

| ফাংশন | কাজ |
|---|---|
| `run(steps, stop_event, manual_state)` | সিকুয়েন্সিয়াল: স্টেপ → ইঞ্জিন → `_export()` → `{previous_output}` পাস; ম্যানুয়াল স্টেপে `_run_manual_step()`, QA/Fix-এ `_run_qa_fix_step()`, স্প্লিটে `_run_split_strategy()` |
| `create_video_dubbing_pipeline()` | **9-স্টেপ অটো-ডাব পাইপলাইন ফ্যাক্টরি** — `claude_accounts` লিস্ট + ৩টি GitHub URL + মডেল/এফোর্ট/থিংকিং নিয়ে ৯টি `PipelineStep` বানায় |
| `render_pipeline_template()` | স্ট্যাটিক টেমপ্লেট রেন্ডার — `{chunk}`/`{chunks}` placeholder সাবস্টিটিউট |
| `fetch_github_instruction(raw_url)` | **GitHub Single Source of Truth** — raw URL থেকে `.md` fetch → `workspace/.github_cache/`-এ hash-keyed ক্যাশে; GitHub down থাকলে ক্যাশে ফলব্যাক |
| `_run_manual_step(step)` | `manual_state` dict + `threading.Event` — স্টেপের লেবেল/extensions সেট → UI-এর `ready` event-এর জন্য পোল (০.৫s) → আপলোডেড ফাইল পাথ রিটার্ন |
| `_run_qa_fix_step(step, ...)` | QA/Fix উভয়ই — QA হলে আউটপুট থেকে `_extract_error_list()` → `_context[error_context_key]`-এ সেভ; Fix হলে `{qa_errors}` প্লেসহোল্ডার QA এরর লিস্ট দিয়ে রিপ্লেস |
| `_run_split_strategy(step, ...)` | লিমিট ডিটেক্ট করলে টাস্ক **২টি প্যারালাল ক্লড অ্যাকাউন্টে** (`asyncio.gather`) ভাগ — আবার লিমিট হলে **৩টিতে** |
| `_run_claude_step()` | **Strict handover** + failover: লিমিট → partial output → `next_profile_after()` → প্রসঙ্গ ইনজেক্ট → শেষে ড্রাইভার বন্ধ |
| `_run_gemini_step()` | `gemini.generate()` + attribution |
| `_with_attribution()` | `[Generated by: account_1, account_2 using Sonnet 5 · effort: High]` |
| `_render_prompt()` | `{previous_output}`, `{file_path}`, `{file_paths}` + `_context`-এর সব কী (`{source_video}`, `{turboscribe_srt}`, `{omnivoice_audio}`, `{qa_errors}`) সাবস্টিটিউট |
| `_export()` | `workspace/exports/step_N.txt` — output + মেটাডেটা JSON (`step_type` ও `error_list` সহ) |
| `run_orchestrator_in_background()` | **Async/sync ব্রিজ** — আলাদা থ্রেড + `asyncio.new_event_loop()`; `manual_state` dict পাস করা যায় |

**`create_video_dubbing_pipeline()` — 9-স্টেপ লেআউট:**

| স্টেপ | টাইপ | টার্গেট | কী করে |
|---|---|---|---|
| 1 | `manual` | — | Upload Source Video (.mp4/.mov/...) |
| 2 | `manual` | — | Upload Turboscribe SRT (.srt) |
| 3 | `claude` | account[0] | Agent 1: `video_dialogue_screenshot_hindi_translation_instructions.md` GitHub থেকে → স্ক্রিনশট ZIP + হিন্দি অনুবাদ .md |
| 4 | `manual` | — | Upload OmniVoice Audio (.wav/.mp3) |
| 5 | `claude` | account[1] | Agent 2: `video_sync_instructions.md` → ছবি+অডিও+SRT সিঙ্ক করা ভিডিও |
| 6 | `claude` | account[2] | Agent 3: হিন্দি সাবটাইটেল + Effect Ads রেন্ডার (PLAYBOOK.md) |
| 7 | `qa` | account[3] | Agent 4: QA চেক — `ERRORS:` ফরম্যাটে এরর লিস্ট (`error_context_key="qa_errors"`) |
| 8 | `fix` | account[4] | Agent 5: `{qa_errors}` দিয়ে ভুল ঠিক করে (`fixed_step_id=7`) |
| 9 | `split` | — | ফেইলওভার: স্টেপ ৮-এ লিমিট হলে ২টি (তখন ৩টি) প্যারালাল ক্লড অ্যাকাউন্টে ভাগ (স্টেপ ১০/১১ আচরণ) |

---

## ৫. ডেটা ফ্লো (এক নজরে)

### ৫.১ ক্লাসিক পাইপলাইন ফ্লো (কম্পোজার → রান)

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

### ৫.২ 9-স্টেপ ভিডিও পাইপলাইন ফ্লো (Auto-Dubber)

```
[home ভিউ] 🚀 Start Video Dubbing Pipeline বাটন
        │
        ▼
start_video_pipeline()
   ├── accounts = account_manager.list_profiles()
   ├── instruction URLs = github_raw_url(settings.github_instruction_files.*)
   ├── steps = create_video_dubbing_pipeline(accounts, urls, model, effort, thinking)
   ├── manual_state = {"active": False, "ready": threading.Event(), ...}
   └── run_orchestrator_in_background(steps, manual_state=manual_state)
        │
        ▼
[Background thread] Orchestrator.run(steps, manual_state)

   স্টেপ ১ (manual) ──→ manual_state.active=True → [UI পজ: আপলোড ভিডিও]
        │                        ↑ ইউজার ফাইল দিলে ready.set()
        ▼                        └── save_manual_upload() → workspace/uploads/
   স্টেপ ২ (manual) ──→ [UI পজ: আপলোড SRT]
        │
        ▼
   স্টেপ ৩ (claude) ──→ fetch_github_instruction(translation_url) → প্রম্পটে প্রিপেন্ড
        │              → ClaudeDriver → স্ক্রিনশট ZIP + হিন্দি অনুবাদ .md
        │
        ▼
   স্টেপ ৪ (manual) ──→ [UI পজ: আপলোড OmniVoice অডিও]
        │
        ▼
   স্টেপ ৫ (claude) ──→ fetch_github_instruction(sync_url) → সিঙ্ক করা ভিডিও
        │
        ▼
   স্টেপ ৬ (claude) ──→ fetch_github_instruction(subtitles_url) → হিন্দি সাবটাইটেল + Effects
        │
        ▼
   স্টেপ ৭ (qa) ──────→ Claude QA অ্যাজেন্ট → _extract_error_list() → _context["qa_errors"]
        │
        ▼
   স্টেপ ৮ (fix) ─────→ {qa_errors} প্রম্পটে → ফিক্স অ্যাজেন্ট → ঠিক করা ভিডিও
        │
        ▼
   স্টেপ ৯ (split) ───→ limit_accounts চেক
                          ├── কোনো লিমিট নেই → "nothing to split"
                          ├── লিমিট আছে → ২টি প্যারালাল chunk (স্টেপ ১০)
                          └── আবার লিমিট → ৩টি প্যারালাল chunk (স্টেপ ১১)
        │
        ▼
[running ভিউ]  সেন্টারড টাইটেল + গ্যালারি → লগ কনসোল → ফাইনাল ভিডিও প্লেয়ার
```

### ৫.৩ স্টেট ম্যানেজমেন্ট — `manual_state` dict (থ্রেড-সেফ হ্যান্ডঅফ)

```
একটি শেয়ার্ড dict — অর্কেস্ট্রেটর (ব্যাকগ্রাউন্ড থ্রেড) লেখে, UI (main thread) পড়ে:

{
  "active": bool,             # ম্যানুয়াল স্টেপ অপেক্ষা করছে কিনা
  "step_id": int,             # কোন স্টেপ
  "label": str,               # "Upload Source Video" ইত্যাদি
  "file_key": str,            # "source_video" / "turboscribe_srt" / "omnivoice_audio"
  "expected_extensions": str, # ".mp4,.mov,..." / ".srt" / ".wav,.mp3"
  "uploaded_paths": [..],     # UI এখানে absolute পাথ লেখে
  "ready": threading.Event()  # UI ফাইল দিলে set() করে
}

ফ্লো:
1. অর্কেস্ট্রেটর: active=True + মেটাডেটা সেট → ready.clear() → ready.wait() লুপ (০.৫s পোল)
2. UI: render_running() দেখে active → file_uploader দেখায় → save_manual_upload()
   → uploaded_paths-এ পাথ যোগ → ready.set()
3. অর্কেস্ট্রেটর: ready.is_set() → পাথ পড়ে → active=False → পরের স্টেপে চলে

কেন থ্রেড-সেফ: Streamlit sync main thread + অর্কেস্ট্রেটর আলাদা daemon থ্রেড —
দুটোই একই dict-এ access করে, তাই শুধু threading.Event + list-append ব্যবহৃত
(কোনো সরাসরি st.session_state রাইট থ্রেড থেকে করা হয় না)।
```

---

## ৬. কনফিগারেশন ফাইল

### `config/settings.json` — কীগুলো:

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
| `github_cache_dir` | `workspace/.github_cache` | GitHub ইনস্ট্রাকশন ক্যাশে ডিরেক্টরি |
| `limit_detection_phrases` | ৭টি ফ্রেজ | লিমিট ব্যানার চেনার টেক্সট |
| `known_gemini_models` | ৫টি | UI ড্রপডাউনের মডেল |
| `github_instructions_repo` | `https://raw.githubusercontent.com/your-org/your-repo/main` | ইনস্ট্রাকশন `.md`-র raw base URL (Single Source of Truth) |
| `github_instructions_branch` | `main` | GitHub ব্রাঞ্চ |
| `github_instruction_files` | ৩টি পাথ | `translation` / `sync` / `subtitles` ইনস্ট্রাকশন ফাইলের রিপো-রিলেটিভ পাথ |
| `split_strategy` | — | `max_parallel_accounts` (৩), `min_chunk_duration_sec` (৩০), `initial_split_count` (২), `secondary_split_count` (৩) |
| `video_pipeline_model` | `Sonnet 5` | অটো-ডাব পাইপলাইনের Claude মডেল |
| `video_pipeline_effort` | `High` | পাইপলাইন এফোর্ট লেভেল |
| `video_pipeline_thinking` | `true` | পাইপলাইনে Thinking টগল |

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
| Frontend | home ভিউ | 6 (হিরো, **Video Pipeline preset**, চিপ, Run, কম্পোজার কার্ড, কন্ট্রোল রো) |
| Frontend | কম্পোজার কন্ট্রোল রো | 5 (attach, Engine, Account/Model, Advanced, send) |
| Frontend | running ভিউ | 8 (সেন্টারড টাইটেল, গ্যালারি, ম্যানুয়াল আপলোড পজ, লগ কনসোল, স্টপ, ফলাফল, ভিডিও প্লেয়ার, রিসেট+কম্পোজার) |
| Frontend | Settings ভিউ | 6 (API Keys, Profiles, Data, Preferences, **GitHub Config**, **Pipeline Defaults**) |
| Frontend | history ভিউ | 2 (রান থ্রেড, ব্যাক বাটন) |
| Backend | `key_manager.py` | ১ ক্লাস + ১ dataclass + ৩ ক্লাসিফায়ার |
| Backend | `account_manager.py` | ১ ক্লাস + ~৯ ফাংশন |
| Backend | `claude_driver.py` | ১ ক্লাস + ~২০ ফাংশন |
| Backend | `gemini_driver.py` | ১ ক্লাস + ৩ ফাংশন |
| Backend | `orchestrator.py` | ১ ক্লাস + ৩ ডেটা মডেল + ৪ স্টেপ টাইপ + ১২+ ফাংশন + ব্রিজ |
| Backend | **9-স্টেপ ফ্যাক্টরি** | `create_video_dubbing_pipeline()` → ৩ manual + ৩ claude + ১ QA + ১ fix + ১ split |
| Config | `settings.json` | ২৪+ কী (GitHub + split + pipeline ডিফল্টসহ) |
| Config | `api_keys.json` | কী-রেকর্ড লিস্ট |

## ৯. গুরুত্বপূর্ণ ডিজাইন সিদ্ধান্ত

1. **নেটিভ `st.sidebar` নেই** — কাস্টম columns-ভিত্তিক প্যানেল (`sidebar_open` flag) — Streamlit-এর DOM বদলালেও টগল ভাঙে না।
2. **`stHeader` সম্পূর্ণ hidden** — কারণ টগল নিজের হাতে, নেটিভ হেডারের ওপর নির্ভরতা শূন্য।
3. **Settings পূর্ণ-পেজ ভিউ** (dialog নয়) — X-বাটন/ওভারলে quirks সম্পূর্ণ এড়ানো।
4. **Persistent contexts (প্রোফাইল)** — প্রতিটি অ্যাকাউন্ট আলাদা `user_data_dir` → সেশন সেভ থাকে।
5. **Strict handover** — প্রতিটি স্টেপ নিজের অ্যাকাউন্ট, শেষে ড্রাইভার বন্ধ।
6. **Partial output capture** — লিমিটে আংশিক কাজ হারায় না।
7. **Stability-checked completion** — "Stop গেছে + টেক্সট স্থির" — আধা-জেনারেটেড টেক্সট কখনো কালেক্ট হয় না।
8. **`width="stretch"`** — `use_container_width` deprecation-মুক্ত (Streamlit 1.62+)।
9. **GitHub = Single Source of Truth** — ইনস্ট্রাকশন `.md` কখনো UI-তে hardcode নয়; সর্বদা GitHub raw URL থেকে fetch + লোকাল ক্যাশে (অফলাইন ফলব্যাক)।
10. **Manual pause ≠ pipeline break** — `manual_state` dict + `threading.Event` — ব্যাকগ্রাউন্ড থ্রেড অপেক্ষা করে, UI-তে পজ প্যানেল দেখায়; আপলোডের পর থ্রেড স্টেট হারানো ছাড়াই চালিয়ে যায়।
11. **Split strategy = টাস্ক ভাগ, অ্যাকাউন্ট ভাগ নয়** — একই ফিক্স কাজ ২/৩টি টুকরায় ভাগ হয় (`split_chunk_index`/`split_total_chunks`), প্রতিটি টুকরা ভিন্ন Claude অ্যাকাউন্টে প্যারালাল চলে — টোকেন/রেট লিমিট স্প্রেড হয়।
12. **QA → Fix ডেটা-চেইন** — QA স্টেপ `_extract_error_list()` দিয়ে `ERRORS:` ব্লক আলাদা করে `_context["qa_errors"]`-এ রাখে; ফিক্স স্টেপ শুধু সেই লিস্ট পায় — পুরো আউটপুট নয় (টোকেন বাঁচায়, প্রসঙ্গ পরিষ্কার থাকে)।
13. **Split chunk স্টেপ আইডি এনকোডিং** — চাঙ্ক স্টেপ আইডি `step_id*10+idx` (৯১, ৯২, ৯৩...) এবং retry `step_id*100+idx` — রান হিস্ট্রিতে কোন চাঙ্ক কোন ফ্যান-আউট থেকে এসেছে তা চেনা যায়।

---

## ১০. কীভাবে ভেরিফাই করবেন

```bash
cd /home/shafin/Desktop/BlueprintTube_Project/Claude-workstation
source .venv/bin/activate

# ১. সিনট্যাক্স চেক
python -m py_compile app.py modules/*.py

# ২. পাইপলাইন ফ্যাক্টরি পরীক্ষা
python -c "
from modules.orchestrator import create_video_dubbing_pipeline
steps = create_video_dubbing_pipeline(
    claude_accounts=['a1','a2','a3','a4','a5'],
    translation_instructions_url='https://example.com/t.md',
    sync_instructions_url='https://example.com/s.md',
    subtitles_instructions_url='https://example.com/p.md',
)
assert len(steps) == 9
manual = [s for s in steps if s.step_type == 'manual']
qa = [s for s in steps if s.step_type == 'qa']
fix = [s for s in steps if s.step_type == 'fix']
split = [s for s in steps if s.step_type == 'split']
assert len(manual) == 3 and len(qa) == 1 and len(fix) == 1 and len(split) == 1
print('9-step pipeline factory: OK')
"

# ৩. UI চালু
streamlit run app.py
```

- কোনো `NameError`/`ImportError` নেই
- http://localhost:8501 খুললে: বামে কাস্টম প্যানেল (« বাটন দিয়ে collapse), ডানে home ভিউ (হিরো + Video Pipeline বাটন + কম্পোজার)
- Settings gear → Preferences → GitHub Instructions + Pipeline Defaults দেখা যায়
- 🚀 Start Video Dubbing Pipeline → running ভিউতে টাইটেল + গ্যালারি + লগ কনসোল দেখা যায়
- স্টেপ ১ পৌঁছালে `⏸️ Upload Source Video required` প্যানেল আসে
- স্টেপ যোগ → Run → ক্লাসিক পাইপলাইন → ফলাফল + হিস্ট্রিতে রান দেখা যায়
