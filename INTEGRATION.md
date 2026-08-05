# 🌸 Camellia Artisanal — Instagram Bot Integration Guide

This guide provides a comprehensive, step-by-step procedure to connect the **Camellia Artisanal Auto-Response Bot** to live **Instagram Direct Messages (DMs)** and **Instagram Sponsored Ads**.

---

## 📋 Table of Contents
1. [Prerequisites](#1-prerequisites)
2. [Step 1: Create Meta Developer App](#step-1-create-meta-developer-app)
3. [Step 2: Get Instagram Business Account ID](#step-2-get-instagram-business-account-id)
4. [Step 3: Configure Environment Variables (`.env`)](#step-3-configure-environment-variables-env)
5. [Step 4: Launch Bot Server & Public Tunnel](#step-4-launch-bot-server--public-tunnel)
6. [Step 5: Configure Meta Instagram Webhook](#step-5-configure-meta-instagram-webhook)
7. [Step 6: Testing & Verification](#step-6-testing--verification)
8. [Troubleshooting Common Issues](#troubleshooting-common-issues)

---

## 1. Prerequisites

Before starting, ensure you have:
- An **Instagram Business / Professional Account** (e.g., `@camellia.artisanal`).
- A **Facebook Page** linked to your Instagram Business account.
- A **Meta Developer Account** registered at [developers.facebook.com](https://developers.facebook.com/).
- Python 3.11+ and dependencies installed (`pip install -r requirements.txt`).

---

## Step 1: Create Meta Developer App

1. Go to the [Meta Developer Dashboard](https://developers.facebook.com/apps/).
2. Click **Create App** $\rightarrow$ Select **Business** as the app type.
3. Enter App Display Name (e.g. `Camellia Artisanal Bot`) and your contact email.
4. Click **Create App**.
5. On the App Dashboard under **Add Products**, locate **Instagram Graph API** and click **Set Up**.

---

## Step 2: Get Instagram Business Account ID

1. Open the [Meta Graph API Explorer](https://developers.facebook.com/tools/explorer/).
2. In the top right panel:
   - **Meta App**: Select `Graph API Explorer` (or your App Name).
   - **User or Page**: Select `User Token`.
3. Under **Permissions**, add:
   - `instagram_basic`
   - `instagram_manage_messages`
   - `pages_show_list`
   - `pages_read_engagement`
4. Click the blue **Generate Access Token** button.
5. In the Facebook login popup, authorize your Facebook Page and Instagram Account.
6. In the top query bar next to `GET`, paste:
   ```text
   me/accounts?fields=name,id,instagram_business_account
   ```
7. Click **Submit**.
8. From the JSON response, copy the numeric `id` inside `instagram_business_account`:
   ```json
   {
     "data": [
       {
         "name": "Camellia Artisanal",
         "id": "109876543210987",
         "instagram_business_account": {
           "id": "17841400000000000"
         }
       }
     ]
   }
   ```

---

## Step 3: Configure Environment Variables (`.env`)

Create or update your `.env` file in the project root:

```env
# Meta Webhook Verification Secret (Choose a secret string)
META_VERIFY_TOKEN=camellia_verify_secret_2026

# Meta App Secret (From App Dashboard -> Settings -> Basic)
META_APP_SECRET=your_meta_app_secret_here

# Instagram Business Account ID (Copied from Step 2)
INSTAGRAM_PAGE_ID=17841400000000000

# Human Escalation Contact Details
HUMAN_ESCALATION_PHONE=+918796551990
HUMAN_ESCALATION_EMAIL=owner@camelliaartisanal.com

# System Storage Paths
DB_PATH=camellia.db
MENU_JSON_PATH=menu.json
QA_TEMPLATES_PATH=qa_templates.json
```

---

## Step 4: Launch Bot Server & Public Tunnel (`ngrok`)

The Meta Webhook requires a public HTTPS URL. Open two terminal windows:

#### Terminal 1: Start FastAPI Server
```bash
uvicorn app:app --reload --port 8000
```
*(Runs locally at `http://127.0.0.1:8000`)*

#### Terminal 2: Start Public HTTPS Tunnel via ngrok
```bash
ngrok http 8000
```
*(Outputs your active public HTTPS URL, e.g., `https://a1b2-34-56-78-90.ngrok-free.app`)*

---

## Step 5: Configure Meta Instagram & WhatsApp Webhooks

1. Go to your **Meta App Dashboard** $\rightarrow$ **Instagram** or **WhatsApp** $\rightarrow$ **Webhooks**.
2. Click **Configure Webhook / Edit Subscription**.
3. Fill in the popup fields:
   - **Callback URL**: 
     - For Instagram: `https://<YOUR-NGROK-SUBDOMAIN>.ngrok-free.app/webhook/instagram`  
     - For WhatsApp: `https://<YOUR-NGROK-SUBDOMAIN>.ngrok-free.app/webhook/whatsapp`  
     *(Example: `https://a1b2-34-56-78-90.ngrok-free.app/webhook/instagram`)*
   - **Verify Token**: `camellia_verify_secret_2026`  
     *(Must match `META_VERIFY_TOKEN` in `.env`)*
4. Click **Verify and Save**.  
   *(The FastAPI server validates the secret token and returns the `hub.challenge` string with `200 OK`)*.
5. Under Webhook Fields:
   - Check **`messages`**
   - Check **`messaging_postbacks`**
6. Click **Subscribe**.

---

## Step 6: Testing & Verification

### 1. Test Instagram Sponsored Ad DM (Auto-Reply Active)
- Launch an Instagram Sponsored Ad with CTA set to **"Send Message"**.
- When a user clicks the ad, Meta sends `referral.source == "ADS"`.
- `ad_gate.py` verifies the ad referral and `responder.py` sends the corresponding Menu Photo Card (`Till Thursday.jpeg`, `From Friday.jpeg`, or `Signature Sondesh.jpeg`) automatically.

### 2. Test Organic Non-Ad DM (Auto-Reply Blocked)
- Send an organic DM from a regular Instagram user.
- The Ad Gate blocks auto-reply (`ignored_organic_dm`), leaving the DM in the Instagram Inbox for manual human reply.

### 3. Test Typo Matching & Out-of-Area Delivery
- Asking *"do you delivere in delhi"* $\rightarrow$ Returns Delhi NCR coverage.
- Asking *"do you deliver to banglore"* $\rightarrow$ Returns Delhi NCR only notice.

### 4. Test Human Escalation
- Asking *"I want a refund for my order"* $\rightarrow$ Triggers human escalation, holds auto-reply, and logs event in `camellia.db`.

---

## 🛠️ Troubleshooting Common Issues

| Issue / Error | Cause | Fix |
|---|---|---|
| `Invalid platform app` | App configuration missing | In Graph API Explorer, select `Graph API Explorer` in Meta App dropdown. |
| `Verification token mismatch` | `.env` token mismatch | Ensure `META_VERIFY_TOKEN` in `.env` matches the token entered in Meta Webhook configuration. |
| `Page can't be found (404)` | `uvicorn` server is down | Keep `uvicorn app:app --port 8000` running in Terminal 1 while tunnel is active in Terminal 2. |
| Organic DMs auto-replying | Ad-Gate disabled | Ensure `ad_gate.py` is enabled in `app.py` line 70. |

---

*Guide generated for Camellia Artisanal Bot v1.0.0*
