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
- **Mapping checklists isolate answers.** A post/ad checklist only uses its ticked answers
  (+ General); generic "Tell me more" / "How much does it cost?" pick its ticked
  details/price answer. No ad → general answers only. Use client categories (Approved Q&A →
  Category → Manage) to keep e.g. Kolkata and Gurgaon Home Table answers apart.
- **Generic ad questions are per post, not variations.** Never add "How much does it cost?" as an
  Approved Q&A variation (collides with General price or leaks to no-ad customers). Set the answer's
  "On mapped posts/ads, use for" instead and tick it on the post.
- **New Approved Q&A wording must not match an existing answer** (`_conflicts`); customers
  rarely type the question text, so pick a distinct label ("Kolkata lunch price").
- **Weekly savoury menu is published as an image, no parsing.** Dish-level price questions
  go to the manager.
- **Menu customer topics** decide "send <topic> menu" replies. Use "Mapped posts/ads only"
  for region-specific menus (no city concept exists). A menu with no topic is not
  customer-eligible.
- Kosha Mangsho / Mutton Bento folded into ₹600 Mangsho Bento on an inference — still
  unconfirmed by the client.

## Production facts

- Live: `https://camellia-artisanal-production.up.railway.app` (client's Railway, since
  15 Oct 2026). Old services on the owner's Railway are retired.
- Admin API probing uses the `X-Admin-Key` header from the local `.env` (never paste it).
- The owner's Railway CLI is not logged into the client account; use `/health` and the
  admin API instead of `railway logs`.
