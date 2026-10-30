# Camellia Architecture

Instagram DM chatbot for Camellia Artisanal (Bengali food, sondesh, Home Table events).
One FastAPI process, one SQLite database, an Admin portal served from the same app.
Read this with `FLOW.md` (how a message travels) and `MEMORY.md` (rules learned the hard way).

## Find code fast (graph first)

`graphify-out/graph.json` is a code graph of every module and test (6.5k nodes). Use it
before reading files:

```bash
graphify query "where is the handoff text chosen"     # broad context
graphify explain "route_for_inbound"                  # one node and its neighbours
graphify path "route_for_inbound" "match_and_respond" # how two pieces connect
graphify update .                                     # refresh after code changes (no LLM, ~20s)
```

`graphify-out/GRAPH_REPORT.md` lists god nodes and communities. Most connected:
`Database`, `require_admin`, `_process_instagram_event`, `route_for_inbound`,
`DeterministicResponder`, `validate_route_payload`.

## Runtime

| Piece | Where |
|---|---|
| Production | Client's Railway project `camellia-artisanal-production` (since 15 Oct 2026) |
| Deploy | Push to `s7oaib/ChatBOT` main → GitHub Action mirrors to `Camellia-Artisanal/ChatBot` → Railway auto-deploys |
| Storage | SQLite on the Railway volume (`DB_PATH=/app/var/camellia.db`); menu.json and uploads beside it |
| Health | `GET /health` (warnings list = first place to look) |
| Admin | `/admin` (single HTML page assembled in `app.py` from module `HTML`/`SCRIPT` strings) |

## Layers (top to bottom)

1. **Webhook + orchestration** — `app.py`
   - `/webhook/instagram` (signed) → durable inbound jobs → `_process_instagram_event`.
   - Decides source context (ad / post / reel / story), calls the responder, applies
     handoff rules, queues outbound sends (`instagram_outbound_jobs`).
   - Also hosts every `/admin/*` route and the Admin page markup (raw string — see MEMORY).
2. **Source routing** — which mapping a message belongs to
   - `instagram_routing.py`: `route_for_inbound()` (ad binding → ad route → inherited
     post/reel route → story/shared media), `validate_route_payload()`, topics.
   - `instagram_ads.py`: Meta ads inventory, `ads_workspace()` (Ads tab rows),
     inheritance (`inherited_route`, `sibling_routes`), `price_answer_status()`.
   - `instagram_media.py`, `instagram_story_*.py`: owned Posts/Reels and Stories.
   - `instagram_source_qa.py` / `instagram_story_qa.py`: a mapping's Approved Q&A
     checklist ("bundle"). Post, Reel and link-less Ad mappings can hold one.
3. **Answering** — `responder.py` (`DeterministicResponder.match_and_respond`)
   - Ordered layers; first that owns the turn answers. Approved Q&A is the authority
     for customer copy; the model never rewrites approved wording.
   - `approved_qa.py` (+ `client_qa.py` built-in registry, `approved_qa_schema.py`):
     approved answers, admin overrides, variations, categories (contexts), `match()`.
   - `rag_fallback.py`, `knowledge.py`, `semantic_fallback.py`, `grounded_answers.py`:
     fallbacks that may only use approved facts.
   - `ai_responder.py`, `router.py`, `triage.py`: LLM pieces; router/triage run in shadow.
4. **Menus** — `menu_catalogue.py` (menus, customer topics), `menu_review.py`
   (drafts → versions; weekly savoury lifecycle; `publish_weekly_image` image-only),
   `menu_images.py` (current image per menu), `weekly_savoury.py`, `admin_menu_review.py`,
   `admin_weekly_savoury.py`.
5. **Handoff** — `handoff.py` (`default_handoff_message()` = the only handoff wording),
   `escalator.py`. An open handoff mutes the bot for that customer until staff press
   **Resume AI** or it expires (72 h). `human_notification_events` persists events, but
   the current `send_human_alert()` transport is disabled and returns `skipped`.
6. **Persistence** — `database.py` (`Database`, every table, migrations in `init_db`).

## Admin UI modules (HTML + SCRIPT strings injected into `/admin`)

`admin_ads.py` (Ads tab), `admin_source_response.py` (Response Source selector shared by
Posts & Reels / Stories / mapping form), `admin_approved_qa.py` (Approved Q&A + categories),
`admin_menu_review.py` (Menu Manager), `admin_weekly_savoury.py` (weekly upload → Publish),
`admin_chatbot_control.py`, `admin_handoff_message.py`.

The Dashboard's Setup Check is assembled by `setup_check.py`; route-specific findings carry
their routing ID and source identity so the admin can open the matching editor directly.
Pending customer escalations are grouped under one collapsed Dashboard/Candidates dropdown,
with plain per-question rows showing customer username, question, and source preview when available.
Staff can ignore an item or
open its exact source mapping; unbound ads expose Map Ad. Mapping edits first show a source preview. Escalations remain
separate records and are not treated as answer drafts.
The exact Instagram default “more info on this” prompt without referral/source context fails
closed to human review so a generic city-specific introduction is never sent as an ad reply.

## Key data

| Table | Meaning |
|---|---|
| `instagram_source_routes` | A mapping: source_type post/reel/ad/story, identity (url/shortcode/media_id/ad_id), response_mode CUSTOM or APPROVED_QA, topic, menu, optional custom generic-info phrase routing |
| `instagram_source_ad_bindings` | Meta Ad ID → mapping (BOUND/OBSERVED, active) |
| `instagram_ads_manual_sync_jobs` | Singleton durable status/stage for the admin background Ads/Posts/Reels/Stories refresh |
| `instagram_source_qa_bundles` (+`_items`) | Checklist of approved answers for a post/reel/ad mapping, with its context |
| `instagram_topics` | Mapping topics; `default_approved_qa_ids` answer price/details for Custom mappings |
| `approved_qa_overrides` (+`_versions`) | Admin edits/new answers; `aliases_json`, `blocks_json`, `hidden_aliases_json` |
| `approved_qa_contexts` | Client-added categories (built-ins are in `CANONICAL_CONTEXTS`) |
| `menu_catalogue`, `menu_versions`, `menu_image_versions`, `menu_customer_topics` | Menus and what customers can see |
| `cross_channel_handoffs` | Handoffs (WAITING_FOR_HUMAN mutes the bot) |
| `conversations` | Every inbound/outbound turn (template_id, ad_id, source_routing_id, safe ad-referral presence/path/source/key diagnostics; no raw webhook body) |

## Tests

`tests/` (~4.5k tests). Run focused files first; full suite before every push:

```bash
python -m pytest -q tests/test_<area>.py
python -m pytest -q -n auto -p no:randomly tests   # parallel, ~10 min (pytest-xdist)
git checkout -- data/menu.json
python -m pytest -q --lf -p no:randomly tests      # rerun failures serially = verdict
```

Some tests rewrite `data/menu.json`; restore it with
`git checkout -- data/menu.json` after a full run and re-run any failure alone.
