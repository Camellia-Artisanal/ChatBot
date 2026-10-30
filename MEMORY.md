# Camellia Memory

Rules learned from real failures. Each one cost time once; read before changing the
area it names. Structure is in `ARCHITECTURE.md`, message paths in `FLOW.md`.

## How to work on this repo

- **Graph first.** `graphify query "<question>"` / `graphify explain "<symbol>"` before
  reading big files (`app.py` 14k lines, `responder.py` 7k, `database.py` 6.7k).
  Refresh with `graphify update .` after changes (~20 s, no LLM).
- **Commit straight to `main`, push when green.** Push deploys to the client's Railway.
  Only open a PR for genuinely risky changes and say why.
- **No `Co-Authored-By: Claude` trailer** on any commit or PR — the repo is mirrored to the
  client's GitHub and must show only the owner.
- **Verify live after every push:** deploy status via
  `gh api repos/Camellia-Artisanal/ChatBot/deployments?sha=<sha>` then `/health`, then the
  changed behaviour itself. This project fails silently; "healthy" is not proof.
- **Test like the client.** Unit tests have passed while the screen was wrong (18 Oct: the
  Ads card said "Not mapped" for a mapped ad; a popup opened behind a dialog). For any Admin
  change, click through it in a browser against a local copy before pushing.
- **When a UI "does nothing", open the browser console first** — a dead script, a hidden
  element, or a dialog layering problem, before blaming server config.
- **OneDrive can revert writes.** After a script writes a large file, re-read it and check
  counts before the next step uses it.

## Code traps

- `html_content = r"""..."""` in `app.py` and every module `HTML`/`SCRIPT` string must stay
  **raw**. A non-raw string turns JS `\n` into a real newline and kills the whole Admin
  script silently.
- Shell heredocs mangle backslashes and apostrophes in JS/regex. Write edit scripts to a
  file and run them; check the result (`\b` became a backspace once).
- `data/qa_templates.json` is a seed: edits ship only when `seed_version` is bumped.
  `data/menu.json` has no seed_version on purpose (runtime rewrites it).
- Parallel test failures that start with "Replies to Instagram messages may be slower than usual" were the
  slow-reply notice firing under load; `tests/conftest.py` turns it off for tests (18 Oct).
- Tests: some are order-dependent and rewrite `data/menu.json`. After a full run,
  `git checkout -- data/menu.json`; re-run any failure alone before calling it real.
- Outbound jobs are ordered per recipient: any job that never reaches SENT/FAILED blocks
  every newer reply to that customer. Keep the 6-attempt cap and the batch abandon (19 Oct:
  outside-window errors retried forever). `sent_steps` stops a retry repeating accepted chunks.
- SQLite cannot widen a CHECK constraint: rebuild the table (drop triggers, rename, create,
  copy, recreate triggers) — see `instagram_source_qa.initialize`.
- `<dialog>` elements draw above any page overlay. Opening a `display:flex` popup from inside
  a dialog hides it behind the dialog — close/park the dialog first.

## Business rules (client decisions)

- **Approved wording is sent word for word.** The model may choose an approved answer,
  never reword one. Prices and tables are never paraphrased.
- **Every handoff sends only the Admin handoff text** (`default_handoff_message()`).
- **Open handoffs mute the bot** for that customer until staff press Resume AI (or 72 h).
- **Ads answer like their post/reel**; renewed ads use their mapped twin's mapping; a group
  card's Map Ad opens the ad that needs answers. Ads Meta sends with no post link get their
  own mapping (source "Ad", identity = Meta Ad ID).
- **An explicit ADS referral must have an exact active route.** If its ad ID is missing or
  the ad has no route, clear prior ad/topic/product context and hand off; never answer from
  an earlier conversation, an ad title, or generic city-specific copy. Store only referral
  path/source/key names and ID-presence diagnostics, never the raw webhook body. The Ads
  summary surfaces recent unmapped ad messages and missing-ID referrals; the first observed
  unmapped ad ID triggers a background inventory/creative refresh.
- **Needs answers only counts Meta-confirmed active ads.** A webhook-observed ID whose Meta
  inventory status is unknown stays visible under Unmapped but is not reported in active
  price-answer or routing warnings.
- **Admin Sync Now must not hold the browser request open for full Meta pagination.** Queue
  the ads, Posts/Reels, and Stories refresh in the background and expose durable progress.
- **Mapping checklists isolate answers.** A post/ad checklist only uses its ticked answers
  (+ General); generic "Tell me more" / "How much does it cost?" pick its ticked
  details/price answer. No ad → general answers only. Use client categories (Approved Q&A →
  Category → Manage) to keep e.g. Kolkata and Gurgaon Home Table answers apart.
- **Price button wording can vary across ads.** Keep one approved price answer and map price
  prompts such as "What is the price per box?" to that price role on the exact ad/post. It
  answers only when the source route selects one price answer; ambiguity or missing source
  context still hands off.
- **Generic ad questions are per post, not variations.** Never add "How much does it cost?" as an
  Approved Q&A variation (collides with General price or leaks to no-ad customers). Set the answer's
  "On mapped posts/ads, use for" instead and tick it on the post.
- **Generic source-info copy is an explicit per-route choice.** A Custom Post/Reel may use its own
  description for the strict allowlist of general “tell me more” phrases. Keep specific approved
  answers and explicit handoff triggers higher priority; do not use fuzzy semantic matching here.
- **New Approved Q&A wording must not match an existing answer** (`_conflicts`); customers
  rarely type the question text, so pick a distinct label ("Kolkata lunch price").
- **Weekly savoury menu is published as an image, no parsing.** Dish-level price questions
  go to the manager.
- **Menu customer topics** decide "send <topic> menu" replies. Use "Mapped posts/ads only"
  for region-specific menus (no city concept exists). A menu with no topic is not
  customer-eligible.
- **A handoff mutes the customer, so handoff patterns must be narrow.** "Wants a person"
  (`handoff._HUMAN_HELP_RE`) matches only phrases asking for a person, never bare
  "someone"/"people"/"staff"/"support" (19 Oct: "sondesh for someone special" handed off).
  Strip a leading greeting before compound-question checks ("Hii! share the menu").
- **Handoffs on purpose:** photos/voice notes, catering/hosting quotes, shelf life, and the
  client's always-forward words (complaint, discount) even on a Custom-note mapping.
- **Every logged message carries `decision_reason`** (which answer/rule, or the handoff ref
  and reason). Read it on `/admin/conversations` before guessing why a reply happened.
- **Dashboard escalation questions are not answer drafts.** Keep them under one collapsed section,
  with plain rows showing customer username, question, and source preview when available. Keep the
  section open after an Ignore refresh; do not add nested disclosure controls per question.
  Show Map Ad for an unbound ad, Edit mapping for a bound source, and let staff Ignore
  an escalation (remove it from pending) or preview/edit its exact mapping. Never auto-approve a reply.
- **Setup-check Edit mapping previews the exact Post/Reel/Story first.** Staff explicitly continue
  before the route editor opens, so they can verify the source before changing its mapping.
- **Meta's default "more info on this" prompt may arrive without a referral block.** With no
  exact source or existing session route, hand it to a person; do not send the city-specific
  savoury intro from an unknown ad context.
- **Do not claim staff push alerts are delivered.** `human_notification_events` is durable,
  but `Escalator.send_human_alert()` currently returns `skipped` (`manual_handoff_only`);
  no first-hit push or daily escalation report is configured.
- **System Q&A (Approved Q&A → System Q&A tab):** greeting, thanks, OK, bye and the two
  clarifying questions read `system_replies.text(id)` (override id `system_reply.<id>`, same
  versioned store as the handoff message). Add new built-in conversation copy there, not as
  a hard-coded string. Booking/payment answers shown there are ordinary Approved Q&A records.
- **WhatsApp links go out short:** `<PUBLIC_BASE_URL>/w/<ref>-<6 random chars>` redirects to
  the full wa.me link (`whatsapp_short_links` table, purged with retention). The random part
  is required — refs are 5 digits and order links carry the customer's order. No valid
  PUBLIC_BASE_URL → the full link is sent.
- **New ads are mappable before Meta delivers them.** Sync has no delay; "Running Now"
  needs ACTIVE + impressions today, so ads under 48 h old that are in review / not delivering
  are listed under "New: waiting for Meta" (Meta `created_time` only). Sync Now also syncs
  Posts/Reels and Stories; ads + Posts/Reels auto-sync every 15 min
  (`INSTAGRAM_ADS_AUTO_SYNC_ENABLED`, off in tests).
- Kosha Mangsho / Mutton Bento folded into ₹600 Mangsho Bento on an inference — still
  unconfirmed by the client.

## Production facts

- Live: `https://camellia-artisanal-production.up.railway.app` (client's Railway, since
  15 Oct 2026). Old services on the owner's Railway are retired.
- Admin API probing uses the `X-Admin-Key` header from the local `.env` (never paste it).
- The owner's Railway CLI is not logged into the client account; use `/health` and the
  admin API instead of `railway logs`.
