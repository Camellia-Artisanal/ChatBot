# 📱 WhatsApp Business API — Foolproof Onboarding & Setup Guide

This document provides an end-to-end, zero-error, step-by-step procedure to connect your client's WhatsApp phone number to the **Camellia Artisanal Auto-Response Bot** using the official Meta WhatsApp Business Cloud API.

Follow these steps exactly to ensure active auto-reply capability without downtime or errors.

---

## 🚨 Critical Prerequisites & Warnings (Read First!)

Before touching the Meta portal, verify these two rules:

1. **Delete Existing WhatsApp App Account (Crucial):**
   - If the client's phone number is currently active on a physical phone using standard **WhatsApp** or **WhatsApp Business App**, the account **MUST BE DELETED** inside the app (`Settings -> Account -> Delete Account`).
   - *Why?* Meta forbids a phone number from being registered simultaneously on a mobile app and the Business Cloud API. If not deleted, Meta number verification will fail.
2. **SMS/Voice Call Capability:**
   - The number must be capable of receiving an SMS or Voice Call during the setup phase to receive a 6-digit verification code.

---

## 📋 Step-by-Step Implementation Flow

```
┌─────────────────────────┐     ┌─────────────────────────┐     ┌─────────────────────────┐
│ 1. Meta Developer App   │ ──> │ 2. Add Phone Number     │ ──> │ 3. Permanent System     │
│    Create & Configure   │     │    & Verify OTP         │     │    Access Token         │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
                                                                             │
┌─────────────────────────┐     ┌─────────────────────────┐                  ▼
│ 6. End-to-End Test      │ <── │ 5. Webhook Subscription │ <── ┌─────────────────────────┐
│    Send DM & Auto-Reply │     │    Verify Token & URL   │     │ 4. Configure .env       │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
```

---

### Step 1: Create Meta Developer App & Add WhatsApp

1. Log into [Meta Developers Dashboard](https://developers.facebook.com/apps/).
2. Click **Create App** $\rightarrow$ Select **Business** as the app type.
3. Enter App Display Name (e.g., `Camellia WhatsApp Bot`) and your contact email.
4. On the App Dashboard under **Add Products to Your App**, locate **WhatsApp** and click **Set Up**.
5. **Select or Create a Meta Business Account (Portfolio):**
   - **Where to find it during App Setup:** When setting up WhatsApp, Meta will display a dropdown asking you to choose a *Meta Business Portfolio*. If you already have one, select it from the list.
   - **How to create a new one (takes 1 minute):**
     - Go directly to [business.facebook.com/overview](https://business.facebook.com/overview) or [business.facebook.com/settings](https://business.facebook.com/settings).
     - Click **Create a Business Portfolio** (or **Create Account**).
     - Fill in your Business Name, Your Name, and Work Email address, then click **Submit**.
     - Open your email inbox and click the verification link sent by Meta.
     - Return to the Developer Portal, refresh the page, and select your newly created Business Account in the dropdown.

---

### Step 2: Onboard & Verify Client's Phone Number

1. In the App Left Sidebar, go to **WhatsApp** $\rightarrow$ **API Setup**.
2. Scroll down to Step 5: **Add a Phone Number** and click **Add Phone Number**.
3. Enter the WhatsApp Business Display Name (e.g., `Camellia Artisanal`).
4. Select Category (e.g., `Food & Beverage`) and optional description.
5. Enter the **Client's Phone Number** with Country Code (e.g., `+919876543210`).
6. Choose verification method: **Text Message (SMS)** or **Phone Call**.
7. Enter the 6-digit verification code received on the client's phone.
8. Once verified, copy the generated **Phone Number ID** (e.g., `102938475610293`).
   > ⚠️ **Important:** Do NOT use the raw phone number or the WABA (WhatsApp Business Account ID). Copy the **Phone Number ID**.

---

### Step 3: Generate Permanent Access Token (System User)

> 🛑 **Danger Zone:** Do NOT use the Temporary Access Token shown on the API Setup page! It expires in 24 hours and will break your bot overnight.

Follow these steps to create a **Permanent Token**:

1. Go to [Meta Business Settings](https://business.facebook.com/settings/).
2. Under **Users**, click **System Users** $\rightarrow$ Click **Add**.
3. Set Name: `WhatsApp Bot System User`, Role: **Admin**.
4. Click **Assign Assets** $\rightarrow$ Choose **Apps** $\rightarrow$ Select your App $\rightarrow$ Enable **Full Control**.
5. Click **Generate New Token**:
   - Select your WhatsApp App.
   - Set Token Expiration: **Never**.
   - Check Permissions:
     - `whatsapp_business_messaging`
     - `whatsapp_business_management`
6. Click **Generate Token** and copy the resulting string immediately. (This is your `WHATSAPP_ACCESS_TOKEN`).

---

### Step 4: Configure Server Environment (`.env`)

In the project root directory (`Camellia.artisal`), create or update your `.env` file:

```env
# Meta Webhook Secrets
META_VERIFY_TOKEN=camellia_whatsapp_verify_secret_2026
META_APP_SECRET=your_meta_app_secret_from_basic_settings

# WhatsApp Cloud API Credentials
WHATSAPP_ACCESS_TOKEN=your_permanent_system_user_access_token
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id_from_step_2
WHATSAPP_API_VERSION=v19.0

# Public Domain (Must be HTTPS)
PUBLIC_BASE_URL=https://your-bot-domain.com

# Human Escalation Contact
HUMAN_ESCALATION_PHONE=+918796551990
HUMAN_ESCALATION_EMAIL=owner@camelliaartisanal.com

# Storage Paths
DB_PATH=camellia.db
MENU_JSON_PATH=menu.json
QA_TEMPLATES_PATH=qa_templates.json
```

---

### Step 5: Deploy Bot & Configure Webhook

#### 1. Start the Server
Deploy your server to production (VPS/Railway/Render) or launch locally with a tunnel:

```bash
# Start FastAPI application
uvicorn app:app --host 0.0.0.0 --port 8000
```

If testing locally via ngrok:
```bash
ngrok http 8000
# Copy your HTTPS URL e.g. https://a1b2c34.ngrok-free.app
```

#### 2. Link Webhook in Meta Portal
1. Go to **Meta Developer Dashboard** $\rightarrow$ **WhatsApp** $\rightarrow$ **Configuration**.
2. Next to **Webhook**, click **Edit**.
3. Enter Details:
   - **Callback URL:** `https://your-bot-domain.com/webhook/whatsapp`
   - **Verify Token:** `camellia_whatsapp_verify_secret_2026` *(Must match `META_VERIFY_TOKEN` in `.env`)*
4. Click **Verify and Save**. (Your FastAPI server will receive a verification GET request and return `200 OK`).
5. Under **Webhook Fields**, click **Manage** and **Subscribe** to:
   - `messages`

---

### Step 6: Verification & Testing Checklist

Perform these tests in order:

- [ ] **1. Outbound Test:** Send a WhatsApp text `"Hi"` or `"Menu"` from a personal phone number to the client's WhatsApp number.
- [ ] **2. Log Check:** Verify server output logs:
  `WhatsApp adapter processing incoming message from +91...`
- [ ] **3. Auto-Reply Check:** Ensure your personal phone receives the automated reply with text and image menu cards.
- [ ] **4. Image Link Test:** Confirm menu image URLs load properly on WhatsApp (`PUBLIC_BASE_URL` must be publicly accessible HTTPS).
- [ ] **5. Out-of-Syllabus Escalation Test:** Text an unrecognized query like `"Can I get a custom birthday cake for tomorrow?"` $\rightarrow$ Confirm bot escalates and logs event to `camellia.db`.

---

## 🛠️ Foolproof Troubleshooting Matrix

| Symptom / Error | Root Cause | Solution / Fix |
|---|---|---|
| **OTP SMS Not Arriving** | Phone number registered in WhatsApp App | Open WhatsApp on mobile $\rightarrow$ Settings $\rightarrow$ Account $\rightarrow$ Delete Account, then re-trigger OTP. |
| **Verification Token Mismatch (403)** | `META_VERIFY_TOKEN` mismatch | Verify `.env` string matches string typed into Meta Webhook configuration modal. |
| **Invalid Payload Signature (403)** | App Secret mismatch | Set `META_APP_SECRET` in `.env` matching **App Settings $\rightarrow$ Basic $\rightarrow$ App Secret** in Meta Dashboard. |
| **Bot Stops Replying After 24 Hours** | Used Temporary Token | Generate a permanent System User Token as instructed in **Step 3**. |
| **Images Fail to Render on WhatsApp** | Relative or HTTP image URL | Ensure `PUBLIC_BASE_URL` in `.env` is a full `https://` domain accessible to Meta servers. |
| **400 Bad Request on Meta Outbound** | Customer outside 24-hr window or incorrect Phone Number ID | Ensure recipient messaged first to open the 24-hour service window. Verify `WHATSAPP_PHONE_NUMBER_ID`. |

---
*Generated for Camellia Artisanal Auto-Response System v1.0.0*
