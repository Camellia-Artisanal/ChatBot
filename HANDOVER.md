# Client Handover & Operations Guide

**Project:** Camellia Artisanal — Instagram Ad-Gated Auto-Response Bot  
**Target Accounts:** Client-owned Meta Business Manager & Hosting Account  

---

## 1. System Overview

This bot is a **standalone, client-owned system** designed to:
1. Automatically respond **ONLY to Instagram DMs originated from Sponsored Ads** (`referral.source == "ADS"`).
2. Answer customer inquiries on both **Instagram** and **WhatsApp Business** using exact, predefined information from `menu.json` and `qa_templates.json`.
3. **Escalate all out-of-syllabus questions** (custom orders, complaints, refunds, allergies) to a human contact without guessing.
4. Record all conversations and escalation logs in your own private database (`camellia.db`).

---

## 2. Non-Technical Guide: Updating the Weekly Menu & Prices

Because your weekly menu rotates (Friday Lunch to Thursday Dinner), you have two options for keeping prices and dishes updated:

### Option A: Edit `menu.json` Directly
1. Open `menu.json` in any text editor.
2. Under `"weekly_menus"`, edit the dishes under `"vegetarian"` or `"non_vegetarian"`, or update the `"week_of"` date string.
3. Save the file and restart the bot server (or call the `/admin/sync-menu` endpoint).

### Option B: Google Sheet Live Sync (Recommended)
1. Open your designated Google Sheet containing weekly dishes and prices.
2. Make your edits to prices, dish names, or descriptions.
3. The bot reads from the published CSV link automatically — **no developer needed!**

### Option C: Admin Kitchen Portal (`/admin`)
1. Log into your private admin portal at `/admin` using your `ADMIN_PIN`.
2. Paste your raw weekly menu text — the built-in deterministic regex engine parses and syncs the dishes instantly at **$0 operating cost**.
3. **Note on Vision OCR & AI Keys:** The photo flyer Vision OCR feature is **OFF by default** ($0 cost). If you ever wish to activate AI-powered flyer image scanning or cloud RAG, you must provide your own `GEMINI_API_KEY` or `OPENAI_API_KEY` under your own billing account — never the developer's key.

---

## 3. Webhook Setup on Meta Developer Portal

### Instagram Webhook Setup
1. Go to your [Meta Developer Dashboard](https://developers.facebook.com/).
2. Select your App -> **Instagram Graph API** -> **Webhooks**.
3. Set Webhook Callback URL: `https://your-server-domain.com/webhook/instagram`
4. Set Verify Token: (matches `META_VERIFY_TOKEN` in your `.env` file).
5. Subscribe to `messages` and `messaging_postbacks`.

### WhatsApp Business Webhook Setup
1. Go to Meta Developer Dashboard -> **WhatsApp** -> **Configuration**.
2. Set Callback URL: `https://your-server-domain.com/webhook/whatsapp`
3. Set Verify Token: (matches `META_VERIFY_TOKEN`).
4. Subscribe to `messages`.

---

## 3. Testing the Bot Before Going Live

You can test the bot 100% locally on your computer **before** linking it to your live Instagram or WhatsApp accounts.

### Method 1: Interactive Terminal Simulator (Recommended)
Run the built-in simulator script inside `Camellia.artisal`:
```bash
python simulate_bot.py
```
This opens an interactive chat prompt where you can test:
- **Mode 1 (Instagram Ad DM)**: Type queries like `"How much is Sondesh?"` or `"Show me this week's veg menu"` to test instant auto-responses.
- **Mode 2 (Organic Instagram DM)**: Verifies that non-ad DMs are blocked by the Ad-Gate and left for manual human reply.
- **Mode 3 (WhatsApp Message)**: Simulates WhatsApp customer chats.
- **Mode 4 (Database & Escalation Analytics)**: Shows real-time statistics and recorded human escalation logs.

### Method 2: Automated Test Suite
Run the unit test suite to verify all 10 core scenarios pass in < 1 second:
```bash
python test_bot.py
```

### Method 3: Interactive Browser Swagger UI (FastAPI)
1. Start the server:
   ```bash
   uvicorn app:app --reload --port 8000
   ```
2. Open your browser and navigate to: `http://localhost:8000/docs`
3. Use the interactive Swagger UI to send test Webhook POST payloads to `/webhook/instagram` or `/webhook/whatsapp` and inspect live JSON responses.

---

## 4. Running & Deploying the Bot

### Local Test Execution
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run unit tests
python test_bot.py

# 3. Start local development server
uvicorn app:app --reload --port 8000
```

### Production Deployment (Railway / Render / VPS)
1. Create a service on Railway/Render using your GitHub repository.
2. Configure Environment Variables from `.env.example`.
3. Set Start Command: `uvicorn app:app --host 0.0.0.0 --port $PORT`

---

## 5. Handover Checklist

- [x] Source code fully located in `Camellia.artisal/`
- [x] Unit test suite (`test_bot.py`) verified 100% passing
- [x] Instagram Ad Gate logic (`ad_gate.py`) verified
- [x] WhatsApp Cloud API adapter (`whatsapp_adapter.py`) ready
- [x] Escalation & Alert engine (`escalator.py`) active
- [x] SQLite Database (`database.py`) & Analytics active
- [x] Menu sync engine (`sheet_sync.py`) configured
- [x] Environment configuration template (`.env.example`) provided
