# PRD: Camellia Artisanal — Instagram Ad-Gated Auto-Response Bot

**Client:** camellia.artisanal
**Requested by:** Mr. Somdeb Ji

---

## 1. Background

Camellia Artisanal runs Instagram sponsored ads to drive product inquiries (Sondesh, weekly Bengali menu items). Currently, customer replies to these ads are handled manually. Per the Minutes of Meeting (30 July 2026), the client wants an automated system that responds **only** to ad-originated inquiries, with **strictly pre-defined answers**, escalating everything else to a human.

This is a **one-time build**: delivered, handed over, and owned entirely by the client going forward. No ongoing dependency on the developer for hosting, API keys, billing, or maintenance.

---

## 2. Requirements (from MoM, restated as system requirements)

| # | MoM Point | System Requirement |
|---|-----------|---------------------|
| 1 | AI chatbot for Instagram customer queries | Automated responder on Instagram DMs |
| 2 | Integrated with WhatsApp Business | Same bot logic/session reachable via WhatsApp Business |
| 3 | Ads generate the inquiries | Bot must read Instagram ad-referral metadata |
| 4 | Bot responds **only** to ad-originated queries — not all incoming DMs | Hard gate: check `referral.source == "ADS"` before any auto-reply |
| 5 | Instant, predefined, relevant answers only | Deterministic keyword/template matching — no free-form generation |
| 6 | Website integration not needed now | Excluded from Phase 1–5; documented as future option only |
| (4b) | Answer only "in syllabus" questions; else forward to human | Escalation path is mandatory, not optional |

---

## 3. Ownership & Billing Model (Critical — read first)

This is the part that makes it a genuine one-time engagement rather than a subscription relationship in disguise.

| Component | Who owns / pays |
|---|---|
| Meta Developer App + WhatsApp Business API number | **Client's own Meta Business Manager account** |
| Instagram Business account + ad account | **Client's own** (already exists) |
| Hosting (server running the bot) | **Client's own cloud account** (e.g. Railway, Render, or a small VPS) registered with client's own card |
| Domain / webhook URL (if needed) | **Client's own domain**, or a free subdomain from the hosting provider |
| Any LLM API key (only if Phase 6 is taken up later) | **Client's own OpenAI/Anthropic account & billing** |
| Source code | **Fully transferred to client** — GitHub repo transferred to client's own account at handover |
| Database (conversation logs, escalations) | Hosted on **client's own infra**, client owns all data |

**What this means practically:** at the end of Phase 5, the developer hands over a working system running entirely on infrastructure and accounts the client controls and pays for directly. The developer does not sit in the payment chain, does not hold API keys, and is not a recurring vendor. If the client wants changes later, that's a **new, separate engagement** — not a support contract implied by this build.

**Action needed from client before Phase 0 starts:** they must personally create/have ready:
1. A Meta Business Manager account with the Instagram + WhatsApp Business API set up (or grant developer temporary access to set this up under their account — access revoked after handover)
2. A hosting account (developer can suggest cheapest reliable option in Phase 0)
3. A decision on the escalation contact — which human/number gets forwarded queries

---

## 4. Scope

### In scope (Phase 1–5)
- Instagram ad-referral detection
- Deterministic Q&A engine (menu.json + qa_templates.json as source of truth)
- WhatsApp Business integration for the same logic
- Escalation/human-handoff system for anything not "in syllabus"
- Conversation logging (for the client's own record — not shared with developer post-handover)
- One-time deployment + documentation + walkthrough

### Out of scope (explicitly, per MoM point 6)
- Website chatbot
- Free-form AI/LLM conversation
- Payment processing inside the chat
- Order tracking / inventory system

### Proposed for later (Phase 6, optional, only if client initiates)
See Section 8.

---

## 5. System Architecture

```
Instagram Ad → Customer clicks "Send Message"
        │
        ▼
Instagram DM opens → Meta sends webhook with referral{source:"ADS", ad_id}
        │
        ▼
   [AD GATE] Is referral.source == "ADS"?
        │
   ┌────┴────┐
   NO         YES
   │           │
no auto-reply   tag session "ad_qualified"
(sits for            │
 manual reply)        ▼
              Customer sends message
                       │
                       ▼
         [MATCH ENGINE] compare against qa_templates.json
                       │
              ┌────────┴────────┐
            MATCH             NO MATCH
              │                   │
              ▼                   ▼
       Send predefined      [ESCALATE] forward to
       answer (from              human contact
       menu.json)           + log in escalations table
              │                   │
              ▼                   ▼
        Log conversation    Send holding message to
        (client's own DB)   customer ("our team will
                              get back to you shortly")
```

Same match engine + same data files serve both Instagram and WhatsApp — one brain, two channels.

---

## 6. Data Sources (already built, see project files)

- `menu.json` — structured product catalog (Aamar Mishti, Signature Collection, weekly rotating menu)
- `qa_templates.json` — trigger-keyword → predefined-response mapping, plus escalation rules

These are the **entire "syllabus."** Nothing gets answered that isn't represented here.

---

## 7. Phased Implementation Plan

### **Phase 0 — Setup & Access (client-driven)**
- Client sets up Meta Business Manager, WhatsApp Business API number, and hosting account
- Client grants developer temporary collaborator access (revoked at handover)
- Confirm escalation contact (which number/person receives forwarded queries)
- **Deliverable:** All accounts live and access confirmed

### **Phase 1 — Core Ad-Gate + Match Engine (MVP)**
- Build `ad_gate.py`: reads Instagram webhook, checks `referral.source == "ADS"`
- Build `responder.py`: keyword-matching engine against `qa_templates.json`
- Wire up `menu.json` as the answer source
- Unit test: confirm ad-originated messages get replies, non-ad messages don't
- **Deliverable:** Working bot logic, testable locally with sample webhook payloads

### **Phase 2 — WhatsApp Business Integration **
- Connect same `responder.py` logic to WhatsApp Business API
- Ensure Instagram → WhatsApp handoff (per MoM point 2) works: if a conversation needs to continue on WhatsApp, session/context carries over
- **Deliverable:** Same bot answering identically on both channels

### **Phase 3 — Escalation System **
- Build `escalator.py`: on no-match, send holding message + forward full query + ad context to the human contact (WhatsApp message, email, or simple dashboard — client's choice)
- Log all escalations for client's own visibility
- **Deliverable:** No query is ever silently dropped — always answered or escalated

### **Phase 4 — Testing & UAT **
- Run through real ad campaigns in a test environment
- Client reviews and approves the exact answer set in `qa_templates.json`
- Edge case testing: misspellings, mixed Bengali/English/Hinglish phrasing, emoji-only messages
- **Deliverable:** Client sign-off on live behavior

### **Phase 5 — Deployment, Handover & Documentation **
- Deploy on client's own hosting account
- Transfer GitHub repo ownership to client
- Deliver:
  - Full source code
  - Setup/runbook documentation (how to restart, redeploy, check logs)
  - **A simple non-technical guide for updating `menu.json` / `qa_templates.json`** (see Section 8 — this is what makes "no need to contact again" realistic)
  - One walkthrough call/session
- Revoke developer's access to all client accounts
- **Deliverable:** Fully independent, client-owned, live system

---

## 8. Recommended Extras (to raise with client — these directly serve the "no need to contact again" goal)

The biggest practical risk to "build once, never touch again" is that **the menu changes weekly** (per the menu images: new Friday–Thursday rotation every week). If updating `qa_templates.json`/`menu.json` requires a developer, the client *will* need to contact someone eventually. Two ways to solve this — recommend proposing both, let client pick:

| Option | Effort | Result |
|---|---|---|
| **A. Google Sheet as the source of truth** | Low | Client edits a Google Sheet (prices, weekly menu); bot reads from it directly. Zero code editing ever needed. |
| **B. Simple internal admin webpage** | Medium | Client logs into a small password-protected page, edits menu items/prices via form, hits save. More polished, slightly more build time. |

**Strong recommendation: Option A.** It's the simplest way to guarantee the client never needs to contact the developer again to update a price or add a weekly dish — non-technical, familiar tool, no login system to maintain.

Other optional extras worth flagging (not required, client's call):
- **Order-intent capture:** if a customer says "I want to order," bot collects item/quantity/area into a structured message sent to escalation contact — reduces back-and-forth (saves the human agent time, doesn't cost anything extra to build now)
- **Basic analytics:** weekly count of "matched vs escalated" queries so client can see which questions to add to the syllabus over time — self-serve improvement, no developer needed
- **Multi-language triggers:** add common Bengali/Hindi transliterations to `qa_templates.json` triggers (e.g. "koto dam" for "how much") — improves match rate for the client's actual customer base

---

## 9. Non-Functional Requirements

- **No hallucination tolerance:** every reply must trace to `menu.json`/`qa_templates.json` — enforced by design (no LLM in the reply path for Phase 1–5)
- **Uptime:** bot runs on client's own hosting; recommend a provider with auto-restart on crash (covered in handover docs)
- **Data privacy:** conversation logs stay on client's own database — developer does not retain copies post-handover
- **Response time:** must feel instant (<3 seconds) since it's pure keyword matching, no API calls to a model

---

## 10. Open Questions for Client (block Phase 4 sign-off until answered)

1. Payment methods accepted — needed to answer "how do I pay"
2. Order minimums / lead time / order cutoff times
3. Who is the escalation contact (number/person) for unmatched queries?
4. Confirm: Option A (Google Sheet) or Option B (admin page) for self-service menu updates?
5. Meaning of `*` marker next to Mango/Kesar sondesh on the menu (seasonal/premium?)

---

## 11. Handover Checklist (Phase 5 exit criteria)

- [ ] Source code repo transferred to client's GitHub account
- [ ] Bot deployed and live on client's own hosting account
- [ ] WhatsApp Business number connected and tested
- [ ] Instagram ad-gate tested against a real ad campaign
- [ ] Escalation path tested end-to-end
- [ ] Menu self-update mechanism (Sheet or admin page) tested by client directly
- [ ] Documentation delivered (setup, runbook, how-to-update-menu)
- [ ] Walkthrough session completed
- [ ] Developer access revoked from all client accounts
- [ ] Final payment settled

---

## 12. Rough Timeline

**Total: ~3 weeks** (Phase 0 through Phase 5), assuming client account setup (Phase 0) doesn't stall.

| Phase | Duration |
|---|---|
| 0 — Setup | 
| 1 — Core engine | 
| 2 — WhatsApp integration |
| 3 — Escalation system | 
| 4 — Testing/UAT |
| 5 — Deployment & handover | 

---

## 13. Success Criteria

- Bot replies **only** to ad-originated Instagram DMs — verified with real campaign traffic
- 100% of "in syllabus" questions get instant correct answers
- 100% of everything else is escalated, never guessed
- Client can update a price or add a weekly dish **without contacting the developer**
- Client fully owns and controls all accounts, code, and data at handover
