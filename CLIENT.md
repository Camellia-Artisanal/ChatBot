# CLIENT.md — Camellia Artisanal Bot: Client Handover & Hosting Guide

This document is written for **the client (business owner)** and whoever administers their
server. It explains, in plain terms:

1. What you are receiving
2. How to put it on a server/VPS so it runs 24×7
3. Where the API keys are stored and how to keep them safe
4. How to connect it to WhatsApp and Instagram
5. Where every file and every piece of customer data lives
6. What it costs per month

---

## 1. What You Are Receiving

A self-hosted auto-reply bot for **Instagram DMs** and **WhatsApp Business**. It answers
customer questions about the sondesh collections, prices, delivery area and timings, sends
the menu photo cards, and — critically — **hands the conversation to a human whenever it is
not certain**. It never invents prices.

| Piece | What it is |
| --- | --- |
| Web service | One Python (FastAPI) process listening on port `8000` |
| Answer script | `qa_templates.json` — the exact wording the bot replies with |
| Product data | `menu.json` — collections, flavours, prices, weekly menus |
| Menu photo cards | The `.jpeg` files in the project root, served at `/images/...` |
| Conversation log | `camellia.db` — a single SQLite file |
| Optional local AI | Ollama (`llama3.2`) for paraphrased questions — **off by default** |

The bot is **hard-gated to ad traffic on Instagram**: an organic DM from someone who did not
click an ad is deliberately left untouched in the Instagram inbox for a human to reply to.

---

## 2. Handing It Over: Server / VPS Setup

### 2.1 What kind of server you need

This is a very light workload — a few hundred messages a day is nothing for it.

| Spec | Minimum | Recommended |
| --- | --- | --- |
| RAM | 1 GB | 2 GB |
| vCPU | 1 | 1 |
| Disk | 20 GB SSD | 25 GB SSD |
| OS | Ubuntu 22.04 or 24.04 LTS | Same |
| Location | **Mumbai / Bangalore** (lowest latency to Indian customers) | Same |

Any of these work: DigitalOcean, Hetzner, Vultr, Linode, AWS Lightsail, or an Indian
provider like E2E Networks. Pick the one whose billing you are comfortable with.

> **If you enable the local Ollama AI**, the requirement jumps to **8 GB RAM minimum**
> because the language model is loaded into memory. See §6 for the cost difference. Most
> clients should leave Ollama off — the deterministic script already covers the real questions.

### 2.2 One-time server preparation

SSH into the fresh server as root and run:

```bash
# 1. Update the machine
apt update && apt upgrade -y

# 2. Install Docker (the bot ships as a container, so nothing else is needed)
curl -fsSL https://get.docker.com | sh

# 3. Create a non-root user to run the bot
adduser --disabled-password --gecos "" camellia
usermod -aG docker camellia

# 4. Basic firewall: allow SSH and HTTPS only
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw --force enable
```

### 2.3 Put the code on the server

```bash
su - camellia
git clone https://github.com/s7oaib/ChatBOT.git camellia-bot
cd camellia-bot
```

### 2.4 Create the secrets file

```bash
cp .env.example .env
nano .env
```

Fill in the real values (see §3 for where each one comes from), save, then lock the file
so only the bot's user can read it:

```bash
chmod 600 .env
```

### 2.5 Start the bot

```bash
docker compose up -d --build
```

Confirm it is alive:

```bash
curl http://localhost:8000/
docker compose logs -f          # live logs; Ctrl-C to stop watching
```

`restart: always` is already set in `docker-compose.yml`, so the bot comes back
automatically after a crash or a server reboot.

### 2.6 Put it behind HTTPS (required by Meta)

Meta will only send webhooks to a **valid HTTPS URL on port 443**. Point a domain
(e.g. `bot.camelliaartisanal.com`) at the server's IP, then:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

Create `/etc/nginx/sites-available/camellia`:

```nginx
server {
    server_name bot.camelliaartisanal.com;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Enable it and issue a free TLS certificate:

```bash
sudo ln -s /etc/nginx/sites-available/camellia /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d bot.camelliaartisanal.com
```

Certbot auto-renews the certificate, so HTTPS keeps working with no further action.

Your public webhook URLs are now:

- `https://bot.camelliaartisanal.com/webhook/instagram`
- `https://bot.camelliaartisanal.com/webhook/whatsapp`

### 2.7 Updating the bot later

```bash
cd ~/camellia-bot
git pull
docker compose up -d --build
```

The conversation history survives this because it lives in a Docker **named volume**, not
inside the container image.

---

## 3. Where the API Keys Are Stored

**All secrets live in a single file: `.env`, in the project folder on your server.**

That file is deliberately **never committed to git** (it is listed in `.gitignore`), never
baked into the Docker image (it is in `.dockerignore`), and never printed in logs.
`docker-compose.yml` reads it at startup and passes the values to the container as
environment variables.

The template with blank placeholders is `.env.example` — that one *is* in git, so you always
know which keys are expected.

### 3.1 The keys, and where each one comes from

| Variable | Where you get it | Notes |
| --- | --- | --- |
| `META_VERIFY_TOKEN` | **You invent it.** Any random string. | You type the same string into the Meta dashboard. Meta echoes it back once to prove the webhook is yours. |
| `META_APP_SECRET` | Meta App Dashboard → **Settings → Basic → App Secret** | Used to verify that every incoming webhook is genuinely from Meta. **Set this in production.** If blank, the signature check is skipped. |
| `WHATSAPP_ACCESS_TOKEN` | Meta App Dashboard → **WhatsApp → API Setup** | See §4.4 — the 24-hour test token must be swapped for a permanent System User token. |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta App Dashboard → **WhatsApp → API Setup** | A numeric ID, *not* the phone number itself. |
| `INSTAGRAM_PAGE_ID` | Meta App Dashboard → **Instagram → API Setup** | The Instagram Business account linked to your Facebook Page. |
| `ADMIN_API_KEY` | **You invent it.** Use `openssl rand -hex 32`. | Protects `/admin/*`, which exposes customer conversations. If blank, those pages are **open to the internet** — always set it. |
| `HUMAN_ESCALATION_PHONE` | Your own number | Where the bot sends "a customer needs you" alerts. Currently `+918796551990` (Mr. Guru Dayal Singh). |
| `HUMAN_ESCALATION_EMAIL` | Your own email | Recorded on escalations. |
| `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey | **Optional.** Only for `menu_vision_parser.py`, the tool that reads a weekly menu photo. Free tier is enough. |
| `GOOGLE_SHEET_CSV_URL` | Google Sheets → File → Share → Publish to web → CSV | **Optional.** Lets you update prices from a spreadsheet instead of editing code. |

### 3.2 Rules for keeping the keys safe

1. **Never** paste a token into WhatsApp, email, a screenshot, or a git commit.
2. `chmod 600 .env` — only the bot's user can read it.
3. Rotate `WHATSAPP_ACCESS_TOKEN` and `ADMIN_API_KEY` if a laptop or phone with server
   access is lost, or if a staff member with access leaves.
4. To rotate: edit `.env`, then `docker compose up -d` to restart with the new value.
5. Keep an offline copy of `.env` somewhere safe (a password manager entry is ideal).
   If the server dies, that file is the only thing that is not in git.

---

## 4. Connecting to WhatsApp

You need a **Meta Business account** and a **phone number that is not currently registered
on the normal WhatsApp or WhatsApp Business app**. Meta requires the number to be free
before it can be attached to the Cloud API.

### 4.1 Create the app

1. Go to https://developers.facebook.com/apps → **Create App**.
2. Choose **Business** as the type.
3. Attach it to your Meta Business Portfolio (create one if you have none).

### 4.2 Add the WhatsApp product

1. In the app dashboard, **Add Product → WhatsApp → Set up**.
2. Meta gives you a free test number so you can try things before committing your real one.
3. Copy the **Phone number ID** into `WHATSAPP_PHONE_NUMBER_ID` in `.env`.

### 4.3 Register your real business number

1. **WhatsApp → API Setup → Add phone number**.
2. Enter your business number and verify it by SMS or voice call.
3. Set the display name (this is what customers see) and business category.
   Meta reviews the display name — usually within a day.

### 4.4 Get a permanent access token

The token shown on the API Setup page **expires in 24 hours**. For production:

1. Go to https://business.facebook.com/settings/system-users
2. **Add** a System User → give it the **Admin** role.
3. **Add Assets** → select your app → grant **Full control**.
4. **Generate New Token** → select your app → tick `whatsapp_business_messaging` and
   `whatsapp_business_management` → set expiry to **Never**.
5. Copy that token into `WHATSAPP_ACCESS_TOKEN` in `.env`.

### 4.5 Point the webhook at your server

1. **WhatsApp → Configuration → Webhooks → Edit**.
2. **Callback URL**: `https://bot.camelliaartisanal.com/webhook/whatsapp`
3. **Verify token**: the exact string you put in `META_VERIFY_TOKEN`.
4. Click **Verify and save** — the bot answers Meta's challenge automatically.
5. Under **Webhook fields**, subscribe to **`messages`**.

### 4.6 Instagram, same idea

1. **Add Product → Instagram → API Setup**.
2. Link your Instagram Business account to your Facebook Page.
3. **Webhooks → Edit**: Callback URL `https://bot.camelliaartisanal.com/webhook/instagram`,
   same verify token.
4. Subscribe to **`messages`** and **`messaging_postbacks`**.
5. Copy the Instagram account ID into `INSTAGRAM_PAGE_ID`.

### 4.7 Go live

While the app is in **Development mode** only your own test numbers can message it. When
you are happy, switch the app to **Live** in the top bar of the dashboard. Meta requires a
Privacy Policy URL and a completed Business Verification before it will allow this — start
Business Verification early, as it can take a few days.

### 4.8 Checking it works

```bash
# Should return the challenge string:
curl "https://bot.camelliaartisanal.com/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=hello"

# Watch messages arrive in real time:
docker compose logs -f
```

Then send a real WhatsApp message to your business number and watch the log.

---

## 5. Where Every File Lives

### 5.1 On the server

```
/home/camellia/camellia-bot/          ← the project, cloned from git
├── .env                              ← ALL SECRETS. chmod 600. Not in git.
├── app.py                            ← the web service (webhooks, admin, web tester)
├── responder.py                      ← decides which scripted answer to send
├── ollama_responder.py               ← optional local-AI fallback
├── ad_gate.py                        ← blocks non-ad Instagram DMs
├── escalator.py                      ← alerts a human when the bot is unsure
├── session_memory.py                 ← remembers the customer's name mid-chat (in RAM)
├── whatsapp_adapter.py               ← sends/receives on WhatsApp Cloud API
├── database.py                       ← SQLite read/write
├── sheet_sync.py                     ← optional Google Sheet price sync
├── menu_vision_parser.py             ← optional: read a weekly menu from a photo
├── qa_templates.json                 ← THE ANSWER SCRIPT — edit this to change wording
├── menu.json                         ← collections, flavours, prices, weekly menus
├── AAMAR MISHTI.jpeg                 ← menu photo cards, served at /images/...
├── Signature Sondesh.jpeg
├── Till Thursday.jpeg
├── From Friday.jpeg
├── test_bot.py                       ← 19 automated tests
├── simulate_bot.py                   ← chat with the bot in the terminal, no Meta needed
├── docker-compose.yml                ← how the container is run
└── Dockerfile                        ← how the container is built
```

### 5.2 Customer data (the important one)

Conversation history, escalations and counters live in **one SQLite file** inside a Docker
named volume, so rebuilds never wipe it:

- Inside the container: `/app/data/camellia.db`
- On the host: `/var/lib/docker/volumes/camellia_db_data/_data/camellia.db`

Four tables: `sessions` (who came from an ad), `conversations` (every message in and out),
`escalations` (everything a human needs to follow up), `analytics` (counters).

**This file never leaves your server.** No customer message is sent to any third party
unless you switch Ollama on — and even then, Ollama runs on your own machine.

### 5.3 Backing it up

```bash
# Nightly backup at 3:17 AM
crontab -e
17 3 * * * docker run --rm -v camellia_db_data:/data -v /home/camellia/backups:/backup alpine \
  cp /data/camellia.db /backup/camellia-$(date +\%F).db
```

Copy `/home/camellia/backups` and your offline `.env` to cloud storage weekly. Those two
things are your entire disaster-recovery kit.

### 5.4 Changing prices or wording — no developer needed

| To change… | Edit… | Then… |
| --- | --- | --- |
| The bot's exact reply wording | `qa_templates.json` | `docker compose restart` |
| Prices, flavours, weekly menu | `menu.json` | `docker compose restart` |
| A menu photo card | Replace the `.jpeg`, keeping the same filename | `docker compose up -d --build` |
| Prices from a spreadsheet | Publish the sheet as CSV, set `GOOGLE_SHEET_CSV_URL` | `POST /admin/sync-menu` with your `X-Admin-Key` |

Always test locally first: `python simulate_bot.py` lets you talk to the bot in a terminal
without touching Instagram or WhatsApp.

---

## 6. Monthly Cost

Prices are indicative as of **August 2026** and will drift; treat them as a planning
baseline, not a quotation.

### 6.1 Recommended setup — scripted bot, Ollama off

| Item | Provider | Monthly |
| --- | --- | --- |
| VPS, 2 GB RAM / 1 vCPU, Mumbai | DigitalOcean / Vultr / Linode | **₹900 – ₹1,100** |
| Domain name | Namecheap / GoDaddy (₹900–1,200 per **year**) | **₹75 – ₹100** |
| HTTPS certificate | Let's Encrypt | **₹0** |
| Meta WhatsApp Cloud API — platform fee | Meta | **₹0** |
| Instagram Messaging API | Meta | **₹0** |
| Server backups (optional snapshots) | Provider add-on, ~20% of VPS | **₹180 – ₹220** |
| **Total** | | **≈ ₹1,150 – ₹1,400 / month** |

Cheaper option: **Hetzner CX22** (2 GB / 2 vCPU) is about **₹400/month**, but its data
centres are in Europe, which adds roughly 150 ms of latency. Fine for a chatbot; the
customer will not notice.

### 6.2 WhatsApp conversation charges

Since Meta's 2025 pricing change, WhatsApp bills **per delivered template message** by
category, not per 24-hour window.

| Category | Indicative rate (India) | When it applies |
| --- | --- | --- |
| **Service** — you reply within the customer's 24-hour window | **Free, unlimited** | This is what this bot does almost always |
| Utility — order/payment updates | ~₹0.11 – ₹0.15 per message | Only if you add order-status templates |
| Marketing — promotions | ~₹0.72 – ₹0.79 per message | Only if you send promotional broadcasts |
| Authentication — OTP | ~₹0.11 per message | Not used |

**Because this bot only ever answers a customer who messaged first, your expected
WhatsApp bill is ₹0.** You would only start paying if you later decide to send marketing
broadcasts. Confirm current rates at
https://developers.facebook.com/docs/whatsapp/pricing before budgeting for those.

### 6.3 If you enable the local Ollama AI

Ollama needs the language model in RAM, which means a bigger machine:

| Item | Monthly |
| --- | --- |
| VPS, 8 GB RAM / 4 vCPU, Mumbai | **₹2,900 – ₹3,800** |
| Domain + backups | **₹250 – ₹320** |
| Ollama licence / API fees | **₹0** (runs on your own server) |
| **Total** | **≈ ₹3,200 – ₹4,100 / month** |

Responses also become slower (a few seconds instead of instant) and are no longer perfectly
predictable. **Recommendation: leave `OLLAMA_ENABLED=false`.** The scripted answers cover
the real questions, and anything unusual already goes to a human — which is what you want
anyway when someone asks about a custom order.

### 6.4 One-time and occasional costs

| Item | Cost |
| --- | --- |
| Meta Business Verification | ₹0 |
| WhatsApp business number (if you buy a new SIM) | ₹200 – ₹500 one-time |
| Gemini Vision API for menu-photo parsing | ₹0 on the free tier; a few rupees a month beyond it |
| Developer time for wording/price changes | ₹0 — edit the JSON files yourself |

### 6.5 Cost summary

> **Realistic all-in running cost: about ₹1,200 – ₹1,400 per month** (≈ US$14 – 17),
> and **₹0 in WhatsApp message fees** for the way this bot is used.

---

## 7. Day-to-Day Operation

### 7.1 Checking on things

| Task | Command / URL |
| --- | --- |
| Is it running? | `curl https://bot.camelliaartisanal.com/` |
| Live logs | `docker compose logs -f` |
| Customers waiting for a human | `curl -H "X-Admin-Key: YOUR_KEY" https://bot.camelliaartisanal.com/admin/escalations` |
| How many messages, how many matched | `curl -H "X-Admin-Key: YOUR_KEY" https://bot.camelliaartisanal.com/admin/analytics` |
| Try the bot in a browser | `https://bot.camelliaartisanal.com/chat` |
| Restart after editing JSON | `docker compose restart` |

**Someone should check `/admin/escalations` at least twice a day.** That list is customers
the bot could not answer — a real person is waiting on the other end.

### 7.2 If something breaks

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Bot silent on WhatsApp | Access token expired | Generate a permanent System User token (§4.4) |
| Bot silent on Instagram | Message was an organic DM | Working as designed — reply by hand |
| Meta webhook won't verify | `META_VERIFY_TOKEN` mismatch, or URL is not HTTPS | Check `.env` matches the dashboard exactly |
| `403` on a webhook | `META_APP_SECRET` is wrong | Re-copy it from Settings → Basic |
| `403` on `/admin/...` | Missing or wrong `X-Admin-Key` header | Use the value from `.env` |
| Conversations disappeared after a rebuild | `DB_PATH` was moved outside `/app/data` | It must stay `/app/data/camellia.db` |
| Wrong price quoted | `menu.json` / `qa_templates.json` out of date | Edit, then `docker compose restart` |

### 7.3 Before every handover or upgrade

```bash
python test_bot.py     # all 19 tests must pass
```

---

## 8. Security Checklist Before Going Live

- [ ] `META_APP_SECRET` is set (otherwise anyone can POST fake customer messages)
- [ ] `ADMIN_API_KEY` is set to a long random value (otherwise customer chats are public)
- [ ] `.env` is `chmod 600` and is **not** in git
- [ ] The permanent WhatsApp System User token is in place, not the 24-hour test token
- [ ] HTTPS is live and certbot auto-renewal is active
- [ ] `ufw` allows only SSH, 80 and 443 — port 8000 is **not** exposed to the internet
- [ ] Nightly database backup cron is installed and you have verified a backup file exists
- [ ] An offline copy of `.env` is stored in a password manager
- [ ] SSH uses a key, not a password
- [ ] `OLLAMA_ENABLED=false` unless you have deliberately chosen otherwise

---

## 9. Who to Contact

- **Orders and customer escalations:** Mr. Guru Dayal Singh — +91 87965 51990
- **Kitchen hours:** 10:00 AM – 7:30 PM, daily
- **Delivery area:** Delhi NCR only, dispatched from Sushant Lok, Gurgaon
