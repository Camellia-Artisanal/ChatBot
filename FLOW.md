# Camellia Flows

How things move through the system. Change one step, re-check the steps after it.
Architecture is in `ARCHITECTURE.md`; hard-won rules in `MEMORY.md`.

## 1. Customer DM → reply

```
Meta webhook (signed)  POST /webhook/instagram
  → durable inbound job (instagram_inbound_jobs)           app.py
  → _process_instagram_event(sender, text, ad_id, entry)    app.py
      ├─ active handoff for sender?  → log "waiting_for_human", send nothing
      ├─ source context:
      │    route_for_inbound(ad_id / media / shortcode)     instagram_routing.py
      │      1. ad binding (Ads tab "Map Ad")      → method "ad_id"
      │      2. route with that ad_id              → "ad_id"
      │      3. inherited post/reel mapping, then mapped twin ad  → "ad_creative"
      │      4. story / shared post / reel ids     → "story_media_id" / "shared_*"
      │    explicit ADS referral without exact route → clear prior context and hand off
      │    unmapped shared Reel → active staff handoff, no customer-facing notice
      ├─ responder.match_and_respond(...)                  responder.py
      │      first layer that owns the turn answers:
      │      order flows → mapping checklist (bundle) → Custom topic answers
      │      → approved Q&A (approved_qa.match) → menus → grounded/RAG fallbacks
      ├─ post-rules in app.py:
      │      enabled Custom mapping + exact generic “tell me more” phrases → source note
      │      unmatched price/details on a Custom mapping → client's note once (legacy fallback)
      │      not matched → handoff: get_or_create_handoff + default_handoff_message()
      └─ queue outbound sends (instagram_outbound_jobs), log conversation
```

Rules that hold the flow together:
- Approved Q&A text is sent word for word; blocks (TEXT / MENU / IMAGE_GROUP /
  `catalogue:<menu_id>`) are sent in order.
- Approved Q&A response sequences can include a Reel selected from Camellia's
  current synced Instagram inventory. The picker shows its thumbnail, caption and
  date; the server rechecks owner/current status before preview, publish and send.
  Reels use Meta's native media-share attachment, with the Reel URL as fallback
  only when Meta explicitly rejects that attachment.
- A mapping checklist only allows its ticked answers (+ General). Each answer's
  "On mapped posts/ads, use for" (Price / Details / Booking / Location / Date & time) and its
  question list decide what "How much does it cost?", "Tell me more"… get on that post
  (`approved_qa.mapped_settings`, `match(..., product_fallback=True)`). Saving a mapping where two
  ticked answers claim one question is refused (`mapped_collisions`); the mapping editor shows a
  "What customers on this post will get" table (`POST /admin/instagram-routes/answer-preview`).
- Every handoff sends only `default_handoff_message()` (Admin-editable). Approved answers
  that themselves hand off keep their approved wording.
- Custom Post/Reel mappings can explicitly enable their own source description for a strict
  allowlist of generic info requests (for example “Tell me more about this” and Meta's
  “Hello, can I get more info on this?”). That opt-in can override a broad generic Q&A match;
  price, delivery, booking, human-help, and explicit escalation requests keep their existing
  approved-answer/handoff priority. Unknown phrasings are never semantically guessed into this
  route. The option is stored per source route and is off by default.
- No ad context: generic price questions get `general_price`; nothing mapping-specific leaks.
- An explicit ADS referral without an ad ID or exact active route clears prior ad/topic
  context and hands off; it cannot fall through to a generic or previous-city answer.
- Referral presence/path/source/keys and whether an ad ID was supplied are stored on the
  conversation row. The raw webhook body is not retained.
- Outbound queue: a block retries at most 6 times, a terminal failure fails the rest of its
  answer (so the customer's later replies are not held), a retry resumes after the Meta
  messages already accepted (`sent_steps`), and a reply whose handoff opened after its answer
  finished is dropped (`handoff_open`).

## 2. Ads tab (Admin → Ads)

```
Sync Now → Meta Marketing API → instagram_ads inventory
ads_workspace() rows: mapped? answers_from mapping|post? answer_status APPROVED|NOTE|NONE
Map Ad:
  ad has post link → openAdAnswers → Posts & Reels editor for that post (answers shared by every ad of the post)
  ad has no link   → mapping form preset to source "Ad (no Post/Reel link)" + its Ad ID
Group card Map Ad → the member that still needs answers
/health warns about recently messaged ads that cannot answer price
Ads summary warns about recently messaged unmapped ads and ADS referrals missing an ad ID;
the first observed unmapped ad ID triggers a background inventory/creative refresh.
Manual Sync Now returns immediately, then refreshes ads, Posts/Reels, and Stories in a
background job while the portal polls and displays the current stage/result.
Needs answers includes only recently messaged ads Meta confirms are active; observed-only
IDs with unknown delivery status remain in Unmapped and are not counted in active answer or
routing warnings.
```

## 3. Approved Q&A edit

```
Approved Q&A → Edit / + Add → Preview / Test (dry run, token) → Publish Approved Q&A
  _candidate() validates → _conflicts() refuses wording that already matches another answer
  _save() writes approved_qa_overrides + a version row
Category / context → Manage → add / rename / delete client categories (built-ins locked)
```

## 4. Weekly savoury menu

```
Menu → weekly workspace → choose/drop/paste image → preview + Publish Menu
  POST /admin/weekly-savoury/publish-image → menu_review.publish_weekly_image()
  image published as-is for 7 days; overlapping week superseded; open draft rejected
Customers asking for the menu get the image; a single dish's price → handoff
If there is no eligible current weekly menu, a weekly-menu browse request is recorded
without a customer reply or handoff. Other questions continue through normal routing.
```

## 5. Menu catalogue

```
Menu → + Add Menu → name, Category (Manage), Customer message topic (Manage), file
Customer message topic decides "send <topic> menu" replies (all menus of that topic,
in Reply position order). "Mapped posts/ads only" = usable on mapped posts/ads and in
answer blocks, never sent on general topic requests. No topic = not customer-eligible.
```

## 6. Ship a change

```
focused tests → full suite (parallel) → restore data/menu.json → commit (no co-author)
→ git push origin main → mirror workflow → client Railway deploy
→ gh api repos/Camellia-Artisanal/ChatBot/deployments?sha=<sha> → statuses success
→ GET /health healthy → check the changed behaviour on the live admin/API
```

## 7. Dashboard review actions

```
Dashboard Setup Check → issue rows carry routing IDs → Edit mapping previews the exact
Post/Reel/Story before opening its editor
Pending escalation → collapsed Dashboard / Candidates section → plain question rows show
  customer username, question, and source preview → staff may map/edit the source or Ignore it
```

Escalations stay separate from suggested answer drafts. Verified knowledge still requires
a human to provide and approve the answer facts.
The exact Meta “more info on this” button prompt without referral or mapped-source context
uses a human handoff; it cannot fall through to the city-specific generic food introduction.
