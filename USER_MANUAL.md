# 🤖 CL Multi-Agent Workstation — ইউজার ম্যানুয়াল (বাংলা)

Claude ওয়েব অটোমেশন (Playwright) এবং Gemini API (google-genai) — দুটি ইঞ্জিন দিয়ে সিকুয়েন্সিয়াল টাস্ক পাইপলাইন চালানোর একটি লোকাল সিস্টেম। UI দেখতে **Claude.ai-এর chat-interface**-এর মতো — বামে কাস্টম সাইড প্যানেল, ডানে কম্পোজার ও থ্রেড ভিউ।

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
│  ➕ New Task                 │   [home]   → হিরো হেডিং + কম্পোজার কার্ড │
│  🔍 Search                  │   [running]→ চ্যাট থ্রেড + স্টপ বাটন     │
│  📜 History (date-গ্রুপড)   │   [settings]→ ৪ সেকশনের Settings ভিউ     │
│  ─────────────────────      │   [history]→ read-only রান থ্রেড        │
│  ● Ready · Local Workstation │                                         │
│  ⚙️ Settings                │                                         │
└──────────────────────────────┴─────────────────────────────────────────┘
```

**মূল ভিউগুলো (স্টেট অনুযায়ী স্বয়ংক্রিয়ভাবে বদলায়):**

| ভিউ | কখন দেখা যায় | কাজ |
|---|---|---|
| **home** | শুরুতে / New Task | নতুন পাইপলাইন বানানো (কম্পোজার) |
| **running** | Run চাপলে | লাইভ লগ + ফলাফল থ্রেড |
| **settings** | ⚙️ Gear ক্লিকে | API Keys / Profiles / Data / Preferences |
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

### ধাপ ৩: নতুন পাইপলাইন বানানো (home ভিউ — কম্পোজার)

**+ New Task** ক্লিক করলে খালি কম্পোজার দেখা যায়:

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

### ধাপ ৪: পাইপলাইন চালানো (running ভিউ)

1. **▶️ Run Pipeline** চাপলে স্বয়ংক্রিয়ভাবে **running ভিউ** চলে আসে।
2. প্রতিটি স্টেপ **chat মেসেজ ব্লক** হিসেবে — `Step N · engine · account · Effort` হেডার + লাইভ লগ লাইন (`⏳ Claude is generating…`, `📥 Downloaded: …`)।
3. চলাকালীন কম্পোজারের জায়গায় **⛔ Stop Pipeline** বাটন।
4. শেষে: প্রতিটি স্টেপের ফলাফল + **📥 ডাউনলোড বাটন** (ফাইল থাকলে) + `⚠️ Accounts that hit limits during this run` ব্যানার (যদি থাকে)।
5. **🔄 Reset** দিয়ে ভিউ পরিষ্কার → আবার নতুন টাস্ক।

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
└── exports/
    └── step_N.txt     # প্রতিটি স্টেপের আউটপুট (JSON + attribution)
```

কনফিগ: `config/api_keys.json` (কী), `config/settings.json` (মডেল/লিমিট/সেটিংস)

---

## ৫. অটোমেটিক রিকভারি — সমস্যায় সিস্টেম যা করে

| সমস্যা | সিস্টেম যা করে |
|---|---|
| Claude-এ লিমিট | আংশিক আউটপুট ক্যাপচার → পরের অ্যাকাউন্টে সুইচ → প্রসঙ্গ ইনজেক্ট → চালিয়ে যায় |
| Gemini কী 429 | `429` চিহ্নিত → পরের healthy কী দিয়ে রিট্রাই |
| মডেল 404 (not supported) | ঐ কী-র অন্যান্য মডেল ট্রাই (fallback chain) |
| সব কী ফেল | `No healthy Gemini API keys available` এরর |
| পপওভার/ওভারলে ইন্টারসেপশন | Escape×2 + হেডার ক্লিক → `focus()` → `click(force=True)` |

---

## ৬. সাধারণ সমস্যা ও সমাধান

**"No Claude accounts yet"** → Settings → Claude Profiles → Add New Account → লগইন করুন।

**মডেল/এফোর্ট সিলেক্ট হচ্ছে না** → Claude.ai-র UI বদলালে `modules/claude_driver.py`-এর `_click_model_trigger()`/`_select_effort_and_thinking()` সিলেক্টর আপডেট করুন।

**হেডলেস চালাতে চান** → `config/settings.json`-এ `"headless": true`।

**Cloudflare ব্লক** → স্টেলথ অটো চালু; ম্যানুয়ালি একবার লগইন করে প্রোফাইল সেভ রাখুন।

**টাইমআউট** → `config/settings.json`-এ `max_stop_generating_wait_sec` বাড়ান।

---

## ৭. দ্রুত কাজের ফ্লো (চেকলিস্ট)

- [ ] Settings → API Keys: কী পেস্ট + টেস্ট (🟢 active)
- [ ] Settings → Claude Profiles: ১টি প্রোফাইল তৈরি + লগইন
- [ ] home ভিউ: কম্পোজারে প্রম্পট + পিল সেটিংস → ➤ স্টেপ যোগ
- [ ] ▶️ Run Pipeline → running ভিউতে লগ/ফলাফল দেখা
- [ ] সাইড প্যানেল History → পুরনো রান দেখা
- [ ] Settings → Data → Clear History (প্রয়োজনে)
