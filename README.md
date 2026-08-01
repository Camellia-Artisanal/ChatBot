# 🌸 Camellia Artisanal — Meta Instagram & WhatsApp Auto-Response Bot

![Python Version](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![Framework](https://img.shields.io/badge/FastAPI-0.110.0-green.svg)
![Meta API](https://img.shields.io/badge/Meta%20Graph%20API-v19.0-0084FF.svg)
![Database](https://img.shields.io/badge/SQLite-3.0-lightgrey.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

Production-ready, deterministic customer auto-response engine engineered for **Camellia Artisanal** (Bengal-rooted gourmet sweets & cloud kitchen food brand in Delhi NCR). 

Built specifically to solve Meta Ad lead conversion bottlenecks, filter non-ad noise, send high-res Menu Photo Cards (`Till Thursday.jpeg`, `From Friday.jpeg`, `Signature Sondesh.jpeg`, `AAMAR MISHTI.jpeg`), and gracefully escalate complex/sensitive customer inquiries to human staff.

---

## 🚀 Key Business Features & Architecture

### 1. 🛡️ Instagram Ad-Gate Filter (PRD Requirement #4)
- **Ad-Originated DMs Only**: Automatically checks Meta Webhook referral data (`messaging[0].referral.source == 'ADS'`).
- **Zero Hallucination / Zero Noise**: Auto-replies *only* to users coming from paid Instagram Ads.
- **Organic DM Protection**: Organic DMs (non-ad) are blocked by the Ad-Gate and left in the Inbox for manual human reply.

### 2. 🖼️ Visual Menu Photo Card Engine
- Sends high-resolution menu photo cards directly in Instagram DMs and WhatsApp chats instead of long text lists.
- **Dynamic Weekly Menu Switch**:
  - **Mon – Thu**: Sends `Till Thursday.jpeg` (Mid-week menu card).
  - **Fri – Sun**: Sends `From Friday.jpeg` (Weekend menu card).
- **Sondesh Collections**:
  - Sends `Signature Sondesh.jpeg` or `AAMAR MISHTI.jpeg` for Sondesh price inquiries.

### 3. 🎯 100% Deterministic Keyword Q&A (PRD Requirement #5)
- Uses keyword boundary regex matching against structured Q&A templates (`qa_templates.json`).
- Prevents AI hallucinations, wrong pricing, or inaccurate delivery promises.
- Covers pricing, weekly menu items, dietary preferences (Veg/Non-Veg), delivery areas (Delhi NCR), minimum order rules, and payment methods.

### 4. 🚨 Human Escalation Pipeline (PRD Requirement #4b)
- **Out-of-Syllabus Protection**: Unmatched customer queries are never answered with guesses.
- **Sensitive Trigger Guard**: Queries containing sensitive keywords (`refund`, `complaint`, `cancel`, `allergic`, `custom order`) immediately flag human escalation.
- **Owner Notification**: Logged into `camellia.db` with customer ID, channel, and message snippet, then alerts human staff via SMS/Email.

### 5. 📊 Self-Service Menu Updates (PRD Requirement Option A)
- Syncs live menu updates directly from a published Google Sheet CSV URL (`POST /admin/sync-menu`) without requiring developer code changes.

### 6. 💬 Browser-Based Live Chat Tester (`/chat`)
- Built-in WhatsApp/Instagram-style web chat interface for live interactive testing in your browser (`http://localhost:8000/chat`).

---

## 🛠️ Technology Stack

- **Backend Framework**: Python 3.11+, FastAPI, Uvicorn
- **HTTP Client**: `httpx` (Async Meta Graph API & WhatsApp Cloud API calls)
- **Database**: SQLite3 (`camellia.db`)
- **API Standards**: Meta Graph API v19.0 (Instagram Messaging API & WhatsApp Business Cloud API)

---

## 📁 Repository Structure

```
Camellia.artisal/
├── app.py                   # FastAPI app entry point & Meta Webhook handlers
├── ad_gate.py               # Instagram Ad-Gate filter module
├── responder.py             # Deterministic Q&A matcher & Photo Card engine
├── whatsapp_adapter.py      # Meta WhatsApp Cloud API integration adapter
├── database.py              # SQLite storage (logs, ad sessions, escalations)
├── escalator.py             # Human escalation & alert notification pipeline
├── sheet_sync.py            # Self-service Google Sheet CSV live menu update module
├── config.py                # Environment variable loader
├── simulate_bot.py          # Interactive CLI terminal simulator
├── test_bot.py              # Automated test suite (10 core test scenarios)
├── menu.json                # Structured weekly menu database
├── qa_templates.json        # Q&A trigger keywords & template responses
├── Till Thursday.jpeg       # Weekday Menu Photo Card
├── From Friday.jpeg         # Weekend Menu Photo Card
├── Signature Sondesh.jpeg   # Signature Sondesh Photo Card
├── AAMAR MISHTI.jpeg        # Aamar Mishti Photo Card
├── HANDOVER.md              # Client handoff guide & Meta setup docs
├── PRD.md                   # Product Requirements Document
├── requirements.txt         # Dependencies list
└── LICENSE                  # MIT License
```

---

## ⚡ Quick Start & Installation

### 1. Clone & Set Up Environment
```bash
git clone https://github.com/s7oaib/ChatBOT.git
cd ChatBOT/Camellia.artisal
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your Meta App Credentials:
```env
META_VERIFY_TOKEN=camellia_verify_secret_2026
WHATSAPP_ACCESS_TOKEN=your_meta_access_token
INSTAGRAM_PAGE_ID=your_instagram_page_id
HUMAN_ESCALATION_PHONE=+918796551990
```

### 3. Run Server
```bash
uvicorn app:app --reload --port 8000
```
Server runs at `http://localhost:8000`.

---

## 🧪 Testing Methods

### Method 1: Live Web Chat Tester (Browser)
Open **[http://localhost:8000/chat](http://localhost:8000/chat)** in your web browser.
- Type any question (*"How much is Sondesh?"*, *"Show me the veg menu this week"*, *"Do you deliver to Gurgaon?"*).
- Switch between **Instagram Ad DM**, **Instagram Organic DM**, and **WhatsApp**.

### Method 2: Interactive Terminal Simulator
```bash
python simulate_bot.py
```
Opens an interactive CLI prompt to test Ad vs Non-Ad DMs and view live logs.

### Method 3: Automated Unit Test Suite
```bash
python test_bot.py
```
Runs 10 automated test scenarios covering Ad-Gate, responder matching, menu photo cards, and escalations in < 1 second.

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](file:///LICENSE) file for details.
