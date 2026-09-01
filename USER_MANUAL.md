# 🤖 CL Multi-Agent Workstation — ইউজার ম্যানুয়াল (বাংলা)

Claude ওয়েব অটোমেশন (Playwright) এবং Gemini API (google-genai) — দুটি ইঞ্জিন দিয়ে সিকুয়েন্সিয়াল টাস্ক পাইপলাইন চালানোর একটি লোকাল সিস্টেম। UI দেখতে **Claude.ai-এর chat-interface**-এর মতো — বামে কাস্টম সাইড প্যানেল, ডানে কম্পোজার ও থ্রেড ভিউ। সাথে রয়েছে **🎬 9-Step Auto-Dub ভিডিও পাইপলাইন** — সেমি-অটোমেটেড ভিডিও অনুবাদ ও ডাবিং।

---

## ১. সিস্টেম চালু করা

```bash
cd /home/shafin/Desktop/BlueprintTube_Project/Claude-workstation
source .venv/bin/activate
streamlit run app.py
```

ব্রাউজারে খুলুন: **http://localhost:8501**

> সিস্টেমটি বর্তমানে চলছে (পোর্ট 8501)। আবার চালাতে চাইলে `Ctrl+C` দিয়ে বন্ধ করে উপরের কমান্ড চালান।

---

## ২. ইন্টারফেস ওভারভিউ

```
┌──────────────────────────────┬─────────────────────────────────────────┐
│  সাইড প্যানেল (« collapse)    │  main content (ভিউ অনুযায়ী)              │
│  ◆ Hybrid Workstation        │                                         │
│  ➕ New Task                 │   [home]   → হিরো + ভিডিও পাইপলাইন বাটন  │
│  🔍 Search                  │              + কম্পোজার কার্ড            │
│  📜 History (date-গ্রুপড)   │   [running]→ টাইটেল + গ্যালারি + লগ       │
│  ─────────────────────      │              কনসোল + ম্যানুয়াল আপলোড     │
│  ● Ready · Local Workstation │              + ফাইনাল ভিডিও প্লেয়ার      │
│  ⚙️ Settings                │   [settings]→ ৪ সেকশনের Settings ভিউ     │
│                              │   [history]→ read-only রান থ্রেড        │
└──────────────────────────────┴─────────────────────────────────────────┘
```

**মূল ভিউগুলো (স্টেট অনুযায়ী স্বয়ংক্রিয়ভাবে বদলায়):**

| ভিউ | কখন দেখা যায় | কাজ |
|---|---|---|
| **home** | শুরুতে / New Task | নতুন পাইপলাইন বানানো (কম্পোজার) + ভিডিও পাইপলাইন চালু |
| **running** | Run চাপলে / ভিডিও পাইপলাইন চালালে | লাইভ লগ কনসোল + ম্যানুয়াল আপলোড + ফাইনাল ভিডিও প্লেয়ার |
| **settings** | ⚙️ Gear ক্লিকে | API Keys / Profiles / Data / Preferences (GitHub + Pipeline Defaults সহ) |
| **history:X** | হিস্ট্রি রো ক্লিকে | পুরনো রান read-only দেখা |

---

## ৩. ধাপে ধাপে ব্যবহার

### ধাপ ১: Gemini API কী যোগ করা (⚙️ Settings → Gemini API Keys)

1. সাইড প্যানেলের **⚙️ Settings** বাটনে ক্লিক করুন → Settings ভিউ খুলবে।
2. বাম নেভ থেকে **Gemini API Keys** সিলেক্ট করুন।
3. প্রতিটি লাইনে একটি করে কী পেস্ট করুন (`AIzaSy...` বা `AQ.Ab...`)।
4. **💾 Save & Test All Keys** ক্লিক করুন।
5. টেবিলে স্ট্যাটাস: 🟢 `active` / 🟠 `429` / 🔴 `exhausted` + প্রতিটি কী-র working model।
6. ফেল করা কী **♻️ Reset failed keys** দিয়ে আবার active করা যায়।

> কী সেভ হয় `config/api_keys.json`-এ। 429 দিলে সিস্টেম অটো পরের কী ব্যবহার করে।

### ধাপ ২: Claude অ্যাকাউন্ট তৈরি ও লগইন (⚙️ Settings → Claude Profiles)

1. **Settings → Claude Profiles** খুলুন।
2. **➕ Add a New Claude Account** এক্সপান্ডারে নাম লিখুন (যেমন `account_1`) → **Create & Login**।
3. headful ব্রাউজার খুলবে — ম্যানুয়ালি লগইন করুন → **উইন্ডো বন্ধ করুন**।
4. অটো-রিফ্রেশে **Connected Accounts** লিস্টে নতুন অ্যাকাউন্ট দেখা যাবে।
5. প্রতিটি অ্যাকাউন্টের পাশে **✏️ Rename** ও **🗑️ Remove** বাটন।

### ধাপ ২.৫: GitHub ইনস্ট্রাকশন URL কনফিগার (⚙️ Settings → Preferences)

ভিডিও পাইপলাইন GitHub থেকে ইনস্ট্রাকশন `.md` ফাইল fetch করে। সেট করতে:

1. **Settings → Preferences** খুলুন।
2. **🌐 GitHub Instructions** সেকশনে:
   - **Raw base URL**: আপনার রিপোর raw URL (যেমন `https://raw.githubusercontent.com/your-org/your-repo/main`)
   - **Translation instructions**: `video_dialogue_screenshot_hindi_translation_instructions.md`-এর পাথ
   - **Sync instructions**: `video_sync_instructions.md`-এর পাথ
   - **Subtitles/Effects instructions**: `PLAYBOOK.md`-এর পাথ
3. **💾 Save GitHub config** ক্লিক করুন।
4. নিচের **🎬 Video Pipeline Defaults** সেকশন থেকে Claude মডেল, Effort, Thinking সেট করে **💾 Save pipeline defaults** ক্লিক করুন।

> GitHub URL কনফিগার না করলেও ভিডিও পাইপলাইন চালু হবে — তবে স্টেপ ৩/৫/৬-এ Claude ইনস্ট্রাকশন পাবে না (GitHub fetch fail করবে, কিন্তু পাইপলাইন থামবে না)।

### ধাপ ৩: নতুন পাইপলাইন বানানো (home ভিউ) — ২টি উপায়

**উপায় ১: ক্লাসিক পাইপলাইন (কম্পোজার)**

1. **✍️ What should it do?** বক্সে কাজ লিখুন — একই কম্পোজার কার্ডের **নিচের কন্ট্রোল রো** থেকে সেটিংস দিন:
   - **Engine pill** — `Claude (Web Chat)` / `Gemini (API)`
   - **Account/Model pill** — Claude হলে অ্যাকাউন্ট + মডেল (Fable 5 / Opus 5 / Sonnet 5 / Haiku 4.5); Gemini হলে মডেল
   - **⚙️ Advanced pill** — ⚡ Effort (Low–Max), 🧠 Thinking toggle, 📎 Use files, 🔁 Max Retries
2. ডানদিকের **➤ (send)** বাটনে ক্লিক করুন → স্টেপটি **চিপ** আকারে যুক্ত হবে (যেমন `Step 1 · Gemini · gemini-3.6-flash ✕`)।
3. আরও স্টেপ যোগ করতে একইভাবে করুন। চিপ ক্লিক করলে স্টেপ **এডিট** করা যায়, ✕ দিলে মুছে যায়।
4. সব স্টেপ ঠিক হলে **▶️ Run Pipeline** বাটন চাপুন।

**প্রম্পটে টেমপ্লেট ভেরিয়েবল:**

| ভেরিয়েবল | অর্থ |
|---|---|
| `{previous_output}` | আগের ধাপের আউটপুট |
| `{file_path}` | আগের ধাপের প্রথম ডাউনলোড ফাইলের পথ |
| `{file_paths}` | আগের ধাপের সব ফাইলের পথ |

**উপায় ২: 🎬 9-Step Auto-Dub ভিডিও পাইপলাইন (এক ক্লিকে)**

home ভিউ-র **🚀 Start Video Dubbing Pipeline** বাটনে ক্লিক করুন — পুরো ৯-স্টেপ পাইপলাইন স্বয়ংক্রিয়ভাবে চালু হবে (শুধু ম্যানুয়াল আপলোডের সময় পজ করবে):

| স্টেপ | টাইপ | কী হয় |
|---|---|---|
| 1 | 📤 ম্যানুয়াল | **Source Video** আপলোড (.mp4/.mov/...) |
| 2 | 📤 ম্যানুয়াল | **Turboscribe SRT** আপলোড (.srt) |
| 3 | 🤖 Claude Agent 1 | GitHub থেকে `video_dialogue_screenshot_hindi_translation_instructions.md` fetch → ডায়ালগ স্ক্রিনশট ZIP + হিন্দি অনুবাদ .md |
| 4 | 📤 ম্যানুয়াল | **OmniVoice অডিও** আপলোড (.wav/.mp3) |
| 5 | 🤖 Claude Agent 2 | GitHub থেকে `video_sync_instructions.md` fetch → ছবি+অডিও+SRT সিঙ্ক করা ভিডিও |
| 6 | 🤖 Claude Agent 3 | হিন্দি সাবটাইটেল + Effect Ads রেন্ডার |
| 7 | 🔎 QA Agent | পুরো ভিডিও চেক করে `ERRORS:` ফরম্যাটে এরর লিস্ট |
| 8 | 🔧 Fix Agent | QA-এর এরর লিস্ট দিয়ে ভুলগুলো ঠিক করে |
| 9 | 🔀 Split Failover | স্টেপ ৮-এ লিমিট হলে কাজ **২টি** প্যারালাল Claude অ্যাকাউন্টে ভাগ → আবার হলে **৩টিতে** |

### ধাপ ৪: পাইপলাইন চালানো (running ভিউ)

1. **▶️ Run Pipeline** (বা ভিডিও পাইপলাইন বাটন) চাপলে স্বয়ংক্রিয়ভাবে **running ভিউ** চলে আসে।
2. উপরে **Auto-Dubber Workstation** সেন্টারড টাইটেল + ৩-কলাম **ওয়ার্কফ্লো গ্যালারি** দেখা যায়।
3. **ম্যানুয়াল স্টেপ এলে পজ:** `⏸️ Upload Source Video required` প্যানেল দেখাবে — ফাইল দিন, সাথে সাথে পরের স্টেপে চলে যাবে। (ভিডিও পাইপলাইনে স্টেপ ১, ২, ৪ এভাবে পজ করবে)
4. নিচে **📋 Real-time Log Console** — প্রতিটি লাইনে: স্টেপ নম্বর, সক্রিয় Claude অ্যাকাউন্ট, স্ট্যাটাস ব্যাজ (🟢 সফল / 🟡 অপেক্ষা / 🔴 ব্যর্থ)।
5. চলাকালীন **⛔ Stop Pipeline** বাটন — ক্লিক করলে বর্তমান স্টেপ শেষ করে থামবে।
6. শেষে: **🎬 Final Video** প্লেয়ারে সরাসরি ভিডিও দেখা যাবে + প্রতিটি স্টেপের ফলাফল + ডাউনলোড বাটন + QA এরর লিস্ট (থাকলে)।
7. **🔄 Reset** দিয়ে ভিউ পরিষ্কার → আবার নতুন টাস্ক।

> **Split Failover কীভাবে চিনবেন:** লগে `🔀 LIMIT DETECTED → splitting fix task into 2 parallel chunks` দেখলে বোঝা যাবে স্টেপ ৯ সক্রিয়। আবার `🔀 Secondary limits detected — re-splitting into 3 chunks` দেখালে স্টেপ ১১-এর মতো ৩টি চাঙ্ক।

### ধাপ ৫: পুরনো রান দেখা (সাইড প্যানেল 📜 History)

1. সাইড প্যানেলে **History** — তারিখ অনুযায়ী গ্রুপড (Today / Yesterday / Previous 7 Days...)।
2. প্রতিটি এন্ট্রি একটি **রান** (যেমন `Run · 2 steps · 14:22`) — ক্লিক করলে read-only থ্রেডে সব স্টেপ দেখা যায় (ডাউনলোড বাটনসহ)।
3. রানের পাশের **⋯** মেনু থেকে **🗑️ Delete** — পুরো রান মুছে ফেলে।
4. **🔍 Search** বক্স দিয়ে রান খোঁজা যায়।
5. সব পরিষ্কার করতে: **Settings → Data → 🧹 Clear History** (চেকবক্স কনফার্মেশনসহ)।

### ধাপ ৬: থিম বদল (⚙️ Settings → Preferences)

- **🌙/☀️** বাটনে ক্লিক করে light/dark থিম টগল করুন।

---

## ৪. ফাইল কোথায় সেভ হয়

```
workspace/
├── downloads/
│   └── step_N/        # Claude থেকে ধরা ফাইল (PDF, CSV, ভিডিও...)
├── exports/
│   └── step_N.txt     # প্রতিটি স্টেপের আউটপুট (JSON + attribution + step_type + error_list)
├── uploads/           # ম্যানুয়াল আপলোড করা ফাইল (ভিডিও, SRT, অডিও)
└── .github_cache/     # GitHub থেকে fetched ইনস্ট্রাকশন .md-র ক্যাশে (hash-keyed)
```

কনফিগ: `config/api_keys.json` (কী), `config/settings.json` (মডেল/লিমিট/GitHub URL/পাইপলাইন ডিফল্ট)

---

## ৫. অটোমেটিক রিকভারি — সমস্যায় সিস্টেম যা করে

| সমস্যা | সিস্টেম যা করে |
|---|---|
| Claude-এ লিমিট | আংশিক আউটপুট ক্যাপচার → পরের অ্যাকাউন্টে সুইচ → প্রসঙ্গ ইনজেক্ট → চালিয়ে যায় |
| ফিক্স স্টেপে (৮) লিমিট | স্টেপ ৯ সক্রিয় → কাজ **২টি** প্যারালাল Claude অ্যাকাউন্টে ভাগ (স্টেপ ১০) → আবার হলে **৩টিতে** (স্টেপ ১১) |
| GitHub fetch fail | লোকাল `.github_cache/` থেকে পড়ে; তাও না থাকলে ইনস্ট্রাকশন ছাড়া প্রম্পট পাঠায় (পাইপলাইন থামে না) |
| Gemini কী 429 | `429` চিহ্নিত → পরের healthy কী দিয়ে রিট্রাই |
| মডেল 404 (not supported) | ঐ কী-র অন্যান্য মডেল ট্রাই (fallback chain) |
| সব কী ফেল | `No healthy Gemini API keys available` এরর |
| পপওভার/ওভারলে ইন্টারসেপশন | Escape×2 + হেডার ক্লিক → `focus()` → `click(force=True)` |

---

## ৬. সাধারণ সমস্যা ও সমাধান

**"No Claude accounts yet"** → Settings → Claude Profiles → Add New Account → লগইন করুন। ভিডিও পাইপলাইনের জন্য **কমপক্ষে ৫টি অ্যাকাউন্ট** (স্টেপ ৩/৫/৬/৭/৮ আলাদা অ্যাকাউন্ট পায়), স্প্লিট ফেইলওভারের জন্য আরও ২-৩টি সুপারিশ করা হয়।

**ভিডিও পাইপলাইন GitHub ইনস্ট্রাকশন fetch করছে না** → Settings → Preferences → GitHub Instructions-এ Raw base URL + ফাইল পাথ ঠিক আছে কিনা দেখুন। GitHub URL-এর শেষে `/main` ব্রাঞ্চ থাকা জরুরি।

**ম্যানুয়াল আপলোড প্যানেল দেখাচ্ছে না** → পাইপলাইন চলাকালীন running ভিউতে থাকুন; স্টেপ ১/২/৪-এ `⏸️ ... required` প্যানেল আসবে। অন্য ভিউতে গেলে প্যানেল দেখতে ফিরে আসুন (পাইপলাইন থেমে অপেক্ষা করে)।

**মডেল/এফোর্ট সিলেক্ট হচ্ছে না** → Claude.ai-র UI বদলালে `modules/claude_driver.py`-এর `_click_model_trigger()`/`_select_effort_and_thinking()` সিলেক্টর আপডেট করুন।

**হেডলেস চালাতে চান** → `config/settings.json`-এ `"headless": true`।

**Cloudflare ব্লক** → স্টেলথ অটো চালু; ম্যানুয়ালি একবার লগইন করে প্রোফাইল সেভ রাখুন।

**টাইমআউট** → `config/settings.json`-এ `max_stop_generating_wait_sec` বাড়ান।

---

## ৭. দ্রুত কাজের ফ্লো (চেকলিস্ট)

### ক্লাসিক পাইপলাইন
- [ ] Settings → API Keys: কী পেস্ট + টেস্ট (🟢 active)
- [ ] Settings → Claude Profiles: ১টি প্রোফাইল তৈরি + লগইন
- [ ] home ভিউ: কম্পোজারে প্রম্পট + পিল সেটিংস → ➤ স্টেপ যোগ
- [ ] ▶️ Run Pipeline → running ভিউতে লগ/ফলাফল দেখা
- [ ] সাইড প্যানেল History → পুরনো রান দেখা
- [ ] Settings → Data → Clear History (প্রয়োজনে)

### 🎬 ভিডিও পাইপলাইন (Auto-Dubber)
- [ ] Settings → Claude Profiles: **কমপক্ষে ৫টি** অ্যাকাউন্ট + লগইন
- [ ] Settings → Preferences → GitHub Instructions: Raw base URL + ৩টি ফাইল পাথ সেভ
- [ ] Settings → Preferences → Video Pipeline Defaults: মডেল/এফোর্ট/থিংকিং সেভ
- [ ] home ভিউ → 🚀 Start Video Dubbing Pipeline
- [ ] স্টেপ ১/২/৪-এ ম্যানুয়াল আপলোড দিন (ভিডিও → SRT → অডিও)
- [ ] স্টেপ ৩/৫/৬-এ Claude নিজে ইনস্ট্রাকশন GitHub থেকে নেবে
- [ ] স্টেপ ৭ QA → স্টেপ ৮ ফিক্স → (লিমিট হলে) স্টেপ ৯ স্প্লিট
- [ ] শেষে 🎬 Final Video প্লেয়ারে ভিডিও দেখুন + ডাউনলোড করুন
