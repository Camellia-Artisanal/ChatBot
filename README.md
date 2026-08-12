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
- **Sensitive Trigger Guard**: Queries containing sensitive keywords (`refund`, `complaint`, `cancel order`, `custom order`, `bulk order`, `payment link`) immediately flag human escalation.
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
*(Server runs locally at `http://localhost:8000`)*

---

## 💻 Test Web Interface (`/chat`)

The bot comes with a built-in **Interactive Web Chat Tester** available at `/chat`, `/demo`, or `/test`. This provides a zero-setup visual environment to test bot responses, menu image cards, Ad-Gate filtering, and Ollama RAG locally in your browser without requiring live Meta developer credentials.

> **Off by default.** The tester writes real conversation rows and fires real escalation alerts to the owner's phone, so it returns `404` unless `ENABLE_WEB_TESTER=true` is set. Enable it in local development only — never on a public domain.

### Accessing the Web Interface

Start your server in terminal:
```bash
ENABLE_WEB_TESTER=true uvicorn app:app --port 8000
```

Then open the Web Chat Interface in your browser:
- [http://localhost:8000/chat](http://localhost:8000/chat) *(or `http://localhost:8000/demo`)*

---

### Features of the Web Chat Interface

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 🌸 Camellia Artisanal — Web Bot Tester                                 │
 ├────────────────────────────────────────────────────────────────────────┤
 │ Channel Mode: [ 📢 Instagram Ad DM ]  [ 🛑 Instagram Organic ] [ 🟢 WA ]│
 ├────────────────────────────────────────────────────────────────────────┤
 │ Quick Test Chips:                                                      │
 │ [ 🍬 Sondesh Prices ] [ 🥬 Weekly Menu ] [ 🚚 Delivery Area ]          │
 │ [ 🦙 Ollama RAG Query ] [ 🚫 Out-of-Syllabus ] [ 🚨 Refund Escalation ]│
 ├────────────────────────────────────────────────────────────────────────┤
 │ [Bot]: 👋 Hi! Welcome to Camellia Artisanal.                           │
 │ [User]: How much is Sondesh?                                           │
 │ [Bot]: Our artisanal Sondesh starts at ₹450 per box.                  │
 │        📷 [Image Card: Signature Sondesh.jpeg]                        │
 └────────────────────────────────────────────────────────────────────────┘
```

1. **Channel Mode Selectors**:
   - **📢 Instagram Ad DM (`instagram_ad`)**: Simulates user entering via a paid Instagram Ad referral (`referral.source == 'ADS'`). Generates auto-replies + high-resolution Menu Photo Cards.
   - **🛑 Instagram Organic DM (`instagram_organic`)**: Simulates non-ad organic DMs. Tests the **Ad-Gate Filter**, displaying:
     `🛑 [AD-GATE BLOCKED]: This is an organic (non-ad) DM. The bot does not auto-reply to organic DMs. Message sits in Inbox for human reply.`
   - **🟢 WhatsApp Business (`whatsapp`)**: Simulates incoming WhatsApp Cloud API messages.

2. **Preset Quick Test Chips**:
   - **🍬 Sondesh Prices**: Triggers Sondesh pricing matrix & dispatches `Signature Sondesh.jpeg` or `AAMAR MISHTI.jpeg`.
   - **🥬 Weekly Veg Menu**: Triggers dynamic day-of-week menu logic (`Till Thursday.jpeg` vs `From Friday.jpeg`).
   - **🚚 Delivery Areas**: Tests Delhi NCR coverage rules vs outside city notifications.
   - **🦙 Ollama RAG Query**: Asks natural language questions resolved via local `llama3.2` model RAG.
   - **🚫 Out-of-Syllabus Refusal**: Tests out-of-context queries (`"What is the capital of France?"`), verifying `OUT_OF_CONTEXT` refusal + human escalation.
   - **🚨 Sensitive Refund Escalation**: Tests complaint/refund guard (`"I want a refund"`), verifying human alert logging.

3. **Live Image Preview Card Render**:
   - Automatically renders outbound menu JPEG cards (`Till Thursday.jpeg`, `From Friday.jpeg`, `Signature Sondesh.jpeg`, `AAMAR MISHTI.jpeg`) inline in the chat bubble.

---

## 📊 Google Spreadsheet Live Menu Sync (`sheet_sync.py`)

Menu items and pricing can be updated seamlessly via a published Google Sheet CSV without modifying any code or JSON files.

### How it works

1. **Maintain Menu in Google Sheets**: Store weekly menu items, categories, descriptions, quantities, and prices in a standard Google Sheet.
2. **Publish as CSV**: In Google Sheets, select `File -> Share -> Publish to Web -> CSV`.
3. **Configure URL**: Add your CSV link to `GOOGLE_SHEET_CSV_URL` in `.env`.
4. **Trigger Live Sync**: Call the `/admin/sync-menu` endpoint via `POST /admin/sync-menu` to hot-reload `menu.json` and memory instantly.

```bash
# Example manual sync request
curl -X POST "http://localhost:8000/admin/sync-menu?csv_url=https://docs.google.com/spreadsheets/d/e/.../pub?output=csv" -H "X-Admin-Key: camellia_admin_secret_key_2026"
```

---

## 🧪 All Testing Methods Summary

### Method 1: Interactive Browser Web Interface (`/chat`)
Set `ENABLE_WEB_TESTER=true`, then open **[http://localhost:8000/chat](http://localhost:8000/chat)** in your web browser.

### Method 2: Automated Unit Test Suite (`test_bot.py`)
Run all 25 automated unit tests:
```bash
python test_bot.py
```
*Tests:* Ad-Gate inspection, webhook challenges (`GET /webhook/*`), webhook signature rejection, WhatsApp parsing, menu photo card logic, Ollama local RAG answers, refusals, offline fallbacks, SQLite logging, retention purge, per-user data deletion, legal pages, tester lockdown, and delivery-failure alerting.

### Method 3: Interactive CLI Terminal Simulator (`simulate_bot.py`)
Run an interactive terminal chat session:
```bash
python simulate_bot.py
```
Type queries directly in your terminal, toggle Ad vs Non-Ad DMs, and inspect live database analytics and human escalations.

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](file:///LICENSE) file for details.
