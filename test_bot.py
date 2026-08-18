import os
import json
import hmac
import hashlib
import sqlite3
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Use temp database for testing
TEST_DB_PATH = "test_camellia.db"
os.environ["DB_PATH"] = TEST_DB_PATH

from config import config
from database import db, Database
from ad_gate import ad_gate
from responder import responder
from whatsapp_adapter import whatsapp_adapter
from escalator import escalator
from app import app

class TestCamelliaBot(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Override DB path to test_camellia.db
        db.db_path = TEST_DB_PATH
        db.init_db()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    def setUp(self):
        # Ensure Ad-Gate and AI testing are isolated from .env overrides
        config.BYPASS_AD_GATE = False
        config.GEMINI_ENABLED = False
        from session_memory import session_memory
        session_memory._sessions.clear()
        # Clear tables between tests
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions;")
            cursor.execute("DELETE FROM conversations;")
            cursor.execute("DELETE FROM escalations;")
            cursor.execute("UPDATE analytics SET value = 0;")
            conn.commit()

    def _post_signed(self, path: str, payload: dict):
        """POSTs a webhook payload carrying a valid X-Hub-Signature-256, as Meta does."""
        raw = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if config.META_APP_SECRET:
            headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(
                config.META_APP_SECRET.encode("utf-8"), raw, hashlib.sha256
            ).hexdigest()
        return self.client.post(path, content=raw, headers=headers)

    def test_ad_gate_organic_dm_ignored(self):
        """PRD Requirement #4: Non-ad organic DMs must be IGNORED by the bot."""
        organic_event = {
            "sender": {"id": "user_organic_101"},
            "message": {"text": "Hi, do you have Sondesh?"}
            # Note: No referral object present!
        }
        is_ad, sender, text, ad_id = ad_gate.inspect_and_gate_message(organic_event)
        self.assertFalse(is_ad, "Organic DM must NOT be ad-qualified.")
        self.assertEqual(sender, "user_organic_101")
        self.assertFalse(db.is_ad_qualified("user_organic_101", "instagram"))

    def test_ad_gate_ad_dm_qualified(self):
        """PRD Requirement #4: Ad-originated DMs (referral.source == 'ADS') are qualified."""
        ad_event = {
            "sender": {"id": "user_ad_202"},
            "message": {
                "text": "Hi, what are the Sondesh prices?",
                "referral": {
                    "source": "ADS",
                    "type": "OPEN_THREAD",
                    "ad_id": "1204040404"
                }
            }
        }
        is_ad, sender, text, ad_id = ad_gate.inspect_and_gate_message(ad_event)
        self.assertTrue(is_ad, "Ad-originated DM must be ad-qualified.")
        self.assertEqual(sender, "user_ad_202")
        self.assertEqual(ad_id, "1204040404")
        self.assertTrue(db.is_ad_qualified("user_ad_202", "instagram"))

    def test_subsequent_messages_in_ad_session(self):
        """Subsequent messages from an already ad-qualified session remain qualified."""
        # 1. First message from ad
        ad_event = {
            "sender": {"id": "user_ad_303"},
            "message": {
                "text": "Hi",
                "referral": {"source": "ADS", "ad_id": "999"}
            }
        }
        ad_gate.inspect_and_gate_message(ad_event)
        
        # 2. Second message without referral payload
        next_event = {
            "sender": {"id": "user_ad_303"},
            "message": {"text": "How much for Nolen Gur?"}
        }
        is_ad, sender, text, _ = ad_gate.inspect_and_gate_message(next_event)
        self.assertTrue(is_ad, "Subsequent message in ad-qualified session must be allowed.")

    @patch("ad_gate.httpx.Client")
    def test_ad_gate_message_edit_fallback(self, mock_client_cls):
        """Test fallback Graph API lookup for message_edit events lacking a sender field."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "from": {"id": "user_fallback_999"},
            "to": {"id": "page_123"},
            "message": "Edited message text from fallback"
        }
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_response

        event_without_sender = {
            "timestamp": 1700000000,
            "message_edit": {
                "mid": "m_test_mid_fallback_123"
            }
        }
        config.BYPASS_AD_GATE = True
        is_ad, sender, text, _ = ad_gate.inspect_and_gate_message(event_without_sender)
        self.assertTrue(is_ad)
        self.assertEqual(sender, "user_fallback_999")
        self.assertEqual(text, "Edited message text from fallback")

    def test_responder_sondesh_pricing(self):
        """Test pricing query returns correct predefined template and photo card."""
        response, was_matched, tmpl_id, img_url = responder.match_and_respond("How much is Sondesh?")
        self.assertTrue(was_matched)
        self.assertEqual(tmpl_id, "sondesh_price_general")
        self.assertIn("₹450", response)
        self.assertIsNotNone(img_url)

    def test_responder_dynamic_weekly_menu(self):
        """Test menu query returns menu photo card URL."""
        response, was_matched, tmpl_id, img_url = responder.match_and_respond("Show me the veg menu this week")
        self.assertTrue(was_matched)
        self.assertEqual(tmpl_id, "weekly_menu_veg")
        self.assertIn("Camellia Artisanal", response)
        self.assertIsNotNone(img_url)

    def test_responder_delivery_location_matching(self):
        """Delivery in Delhi NCR matches delivery_area; Bangalore/out-of-area matches delivery_out_of_area."""
        # 1. Delhi NCR query
        resp_delhi, matched_delhi, tmpl_delhi, _ = responder.match_and_respond("do you deliver in delhi")
        self.assertTrue(matched_delhi)
        self.assertEqual(tmpl_delhi, "delivery_area")
        self.assertIn("Delhi NCR", resp_delhi)

        # 2. Bangalore location reply / query
        resp_blr, matched_blr, tmpl_blr, _ = responder.match_and_respond("bangalore")
        self.assertTrue(matched_blr)
        self.assertEqual(tmpl_blr, "delivery_out_of_area")
        self.assertIn("do not deliver to Bangalore", resp_blr)

        # 3. Explicit "do you deliver to Bangalore" query
        resp_blr2, matched_blr2, tmpl_blr2, _ = responder.match_and_respond("do you deliver to bangalore")
        self.assertTrue(matched_blr2)
        self.assertEqual(tmpl_blr2, "delivery_out_of_area")

    def test_responder_typo_fuzzy_matching(self):
        """Spelling typos like 'delivere' or 'banglore' are matched using fuzzy similarity."""
        # 1. Typo in delivere & delhi
        resp1, matched1, tmpl1, _ = responder.match_and_respond("do you delivere in delhi")
        self.assertTrue(matched1)
        self.assertEqual(tmpl1, "delivery_area")

        # 2. Typo in delivere & banglore
        resp2, matched2, tmpl2, _ = responder.match_and_respond("do you delivere in banglore")
        self.assertTrue(matched2)
        self.assertEqual(tmpl2, "delivery_out_of_area")

    def test_responder_explicit_escalation(self):
        """Sensitive queries (refund/complaint) must trigger explicit escalation."""
        response, was_matched, tmpl_id, img_url = responder.match_and_respond("I want a refund for my order")
        self.assertFalse(was_matched)
        self.assertEqual(tmpl_id, "explicit_escalation")

    def test_responder_unmatched_out_of_syllabus(self):
        """Out-of-syllabus query must escalate without guessing when AI is disabled."""
        with patch.object(config, "GEMINI_ENABLED", False):
            response, was_matched, tmpl_id, img_url = responder.match_and_respond("Do you sell Chinese Hakka Noodles?")
            self.assertFalse(was_matched)
            self.assertIsNone(tmpl_id)
            self.assertTrue("notified our team" in response or "connect you with our team" in response)

    def test_whatsapp_adapter_flow(self):
        """PRD Requirement #2: WhatsApp integration handling."""
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "919876543210",
                            "id": "wamid.HBgLMTIzNDU2Nzg5MA==",
                            "type": "text",
                            "text": {"body": "Hi, what is the price of Sondesh?"}
                        }]
                    }
                }]
            }]
        }
        res = whatsapp_adapter.parse_webhook_message(payload)
        self.assertIsNotNone(res)
        sender, msg_id, body = res
        self.assertEqual(sender, "919876543210")
        self.assertEqual(msg_id, "wamid.HBgLMTIzNDU2Nzg5MA==")
        self.assertEqual(body, "Hi, what is the price of Sondesh?")

        # Process through adapter
        with patch.object(whatsapp_adapter, "send_message"):
            result = whatsapp_adapter.process_incoming_message(sender, body)
        self.assertEqual(result["status"], "processed")
        self.assertTrue(result["was_matched"])
        self.assertEqual(result["template_id"], "sondesh_price_general")

    def test_analytics_tracking(self):
        """Verify analytics DB updates match rates correctly."""
        analytics = db.get_analytics()
        self.assertIn("total_messages", analytics)
        self.assertIn("match_rate_percent", analytics)

        # Log 1 matched and 1 escalated
        db.log_conversation("user_test_1", "instagram", "sondesh price", "response", True, "sondesh_price_general")
        db.log_conversation("user_test_2", "instagram", "random question", "fallback", False, None)

        analytics = db.get_analytics()
        self.assertEqual(analytics["total_messages"], 2)
        self.assertEqual(analytics["matched_responses"], 1)
        self.assertEqual(analytics["escalations_count"], 1)
        self.assertEqual(analytics["match_rate_percent"], 50.0)

    def test_fastapi_instagram_webhook_endpoints(self):
        """FastAPI Webhook Verification & POST Event Handler Tests."""
        # 1. GET Verify
        res = self.client.get(
            "/webhook/instagram",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": config.META_VERIFY_TOKEN,
                "hub.challenge": "test_challenge_123"
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.text, "test_challenge_123")

        # 2. POST Event - Organic DM (Ignored)
        payload_organic = {
            "object": "instagram",
            "entry": [{
                "messaging": [{
                    "sender": {"id": "ig_organic_user"},
                    "message": {"text": "Hello, sondesh price?"}
                }]
            }]
        }
        res_post = self._post_signed("/webhook/instagram", payload_organic)
        self.assertEqual(res_post.status_code, 200)
        data = res_post.json()
        self.assertEqual(data["results"][0]["action"], "ignored_organic_dm")

        # 3. POST Event - Ad DM (Auto-Replied)
        payload_ad = {
            "object": "instagram",
            "entry": [{
                "messaging": [{
                    "sender": {"id": "ig_ad_user"},
                    "message": {
                        "text": "Hello, sondesh price?",
                        "referral": {"source": "ADS", "ad_id": "ad_888"}
                    }
                }]
            }]
        }
        with patch("app.send_instagram_reply"):
            res_post_ad = self._post_signed("/webhook/instagram", payload_ad)
        self.assertEqual(res_post_ad.status_code, 200)
        data_ad = res_post_ad.json()
        self.assertEqual(data_ad["results"][0]["action"], "auto_replied")

    def test_webhook_rejects_unsigned_payload(self):
        """An unsigned POST must be refused whenever META_APP_SECRET is configured."""
        if not config.META_APP_SECRET:
            self.skipTest("META_APP_SECRET not configured; signature guard is intentionally off")

        res = self.client.post("/webhook/instagram", json={"object": "instagram", "entry": []})
        self.assertEqual(res.status_code, 403)

    @patch("httpx.Client.post")
    def test_gemini_in_context_query(self, mock_post):
        """Test Gemini RAG responds accurately for in-syllabus queries when deterministic match score is low."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "We offer artisanal Nolen Gur Sondesh made with authentic date palm jaggery delivered fresh across Delhi NCR!"}]
                }
            }]
        }
        mock_post.return_value = mock_response

        with patch.object(config, "GEMINI_ENABLED", True), patch.object(config, "GEMINI_API_KEY", "test_key"):
            resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("What lentil cakes are used in Dhokar Dalna?")
        self.assertTrue(was_matched)
        self.assertEqual(tmpl_id, "gemini_rag")
        self.assertIn("Nolen Gur Sondesh", resp_text)

    @patch("httpx.Client.post")
    def test_gemini_out_of_context_refusal(self, mock_post):
        """Test Gemini RAG refuses out-of-syllabus query with OUT_OF_CONTEXT and triggers human escalation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "OUT_OF_CONTEXT"}]
                }
            }]
        }
        mock_post.return_value = mock_response

        with patch.object(config, "GEMINI_ENABLED", True), patch.object(config, "GEMINI_API_KEY", "test_key"):
            resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("What is the capital of France?")
        self.assertFalse(was_matched)
        self.assertEqual(tmpl_id, "gemini_out_of_context")
        self.assertTrue("notified our team" in resp_text or "connect you with our team" in resp_text)

    @patch("httpx.Client.post")
    def test_gemini_offline_fallback(self, mock_post):
        """Test graceful fallback to human escalation if Gemini API call fails."""
        mock_post.side_effect = Exception("API connection error")

        with patch.object(config, "GEMINI_ENABLED", True), patch.object(config, "GEMINI_API_KEY", "test_key"):
            resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("Can you send a violin player for a wedding?")
        self.assertFalse(was_matched)
        self.assertTrue(tmpl_id is None or tmpl_id.startswith("gemini_"))

    def test_session_memory_fact_extraction(self):
        """Test extraction of customer name in Session Memory."""
        from session_memory import session_memory
        session_memory.clear_session("test_mem_user", "whatsapp")

        facts = session_memory.extract_and_update_facts(
            "test_mem_user", "whatsapp", "Hi, my name is Shoaib"
        )
        self.assertEqual(facts["name"], "Shoaib")

    def test_escalation_handoff_brief_generation(self):
        """Test escalation handoff brief includes customer name and recent chat history."""
        from session_memory import session_memory
        user_id = "user_handoff_test"
        channel = "instagram"
        session_memory.clear_session(user_id, channel)

        # 1. Establish session memory turns
        responder.match_and_respond("Call me Shoaib", user_id=user_id, channel=channel)

        # 2. Trigger unmatched escalation
        resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("Can you send a violin player for a wedding?", user_id=user_id, channel=channel)
        with patch.object(escalator, "send_human_alert", return_value={"status": "sent", "mock": True}):
            escalator.handle_escalation(user_id, channel, "Can you send a violin player for a wedding?", "Unmatched Query", {})

        escalation_record = db.get_escalations(limit=1)[0]

        context_data = json.loads(escalation_record["context_data"])
        self.assertIn("handoff_brief", context_data)
        brief = context_data["handoff_brief"]
        self.assertEqual(brief["customer_name"], "Shoaib")
        self.assertTrue(len(brief["recent_chat_history"]) > 0)

    def test_reset_session_endpoint(self):
        """Test /api/reset-session clears session memory correctly."""
        from session_memory import session_memory
        user_id = "user_reset_test"
        channel = "whatsapp"

        # 1. Set fact
        session_memory.extract_and_update_facts(user_id, channel, "My name is Shoaib")
        self.assertEqual(session_memory.get_session(user_id, channel)["facts"]["name"], "Shoaib")

        # 2. Call reset API endpoint
        with patch.object(config, "ENABLE_WEB_TESTER", True):
            res = self.client.post("/api/reset-session", json={"user_id": user_id, "channel": channel})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "cleared")

        # 3. Verify memory is empty
        self.assertIsNone(session_memory.get_session(user_id, channel)["facts"]["name"])

    def test_web_tester_hidden_when_disabled(self):
        """The browser tester writes real rows and pages the owner, so it must be
        invisible unless ENABLE_WEB_TESTER is explicitly switched on."""
        with patch.object(config, "ENABLE_WEB_TESTER", False):
            self.assertEqual(self.client.get("/chat").status_code, 404)
            self.assertEqual(self.client.post("/api/test-chat", json={"message": "hi"}).status_code, 404)
            self.assertEqual(self.client.post("/api/reset-session", json={"user_id": "x"}).status_code, 404)

        with patch.object(config, "ENABLE_WEB_TESTER", True):
            self.assertEqual(self.client.get("/chat").status_code, 200)

    def test_legal_pages_are_public(self):
        """Meta requires a reachable Privacy Policy URL before an app can go Live,
        and both pages must stay open — no admin key, no tester flag."""
        for path in ("/privacy", "/terms"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, path)
            self.assertIn("text/html", res.headers["content-type"])
        self.assertIn(str(config.DATA_RETENTION_DAYS), self.client.get("/privacy").text)

    def test_retention_purge_removes_only_expired_rows(self):
        """Rows past DATA_RETENTION_DAYS go; anything inside the window stays."""
        from datetime import datetime, timedelta

        db.log_conversation("purge_recent", "whatsapp", "hi", "hello", True, "greeting")
        db.log_conversation("purge_old", "whatsapp", "hi", "hello", True, "greeting")

        stale = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE conversations SET timestamp = ? WHERE user_id = 'purge_old'", (stale,)
            )
            conn.commit()

        removed = db.purge_expired_data(retention_days=90)
        self.assertEqual(removed["conversations"], 1)

        with db.get_connection() as conn:
            rows = conn.execute("SELECT user_id FROM conversations").fetchall()
        remaining = {r["user_id"] for r in rows}
        self.assertIn("purge_recent", remaining)
        self.assertNotIn("purge_old", remaining)

    def test_retention_purge_disabled_when_zero(self):
        """DATA_RETENTION_DAYS=0 means keep forever, not delete everything."""
        db.log_conversation("purge_keep", "whatsapp", "hi", "hello", True, "greeting")
        removed = db.purge_expired_data(retention_days=0)
        self.assertEqual(removed["conversations"], 0)
        with db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM conversations").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_delete_user_data_removes_every_trace(self):
        """Backs the DPDP deletion right promised on /privacy."""
        db.log_conversation("dpdp_user", "whatsapp", "hi", "hello", True, "greeting")
        db.log_conversation("other_user", "whatsapp", "hi", "hello", True, "greeting")
        db.record_escalation("dpdp_user", "whatsapp", "refund please", "Refund request", {})

        removed = db.delete_user_data("dpdp_user")
        self.assertEqual(removed["conversations"], 1)
        self.assertEqual(removed["escalations"], 1)

        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT user_id FROM conversations WHERE user_id = 'dpdp_user'"
            ).fetchall()
        self.assertEqual(rows, [])

        with db.get_connection() as conn:
            survivors = conn.execute(
                "SELECT COUNT(*) AS c FROM conversations WHERE user_id = 'other_user'"
            ).fetchone()["c"]
        self.assertEqual(survivors, 1)

    def test_delivery_monitor_alerts_only_after_repeated_failures(self):
        """One network blip must not page the owner; a dead token must."""
        from monitoring import DeliveryHealthMonitor

        monitor = DeliveryHealthMonitor(failure_threshold=3)
        with patch.object(monitor, "_send_owner_alert") as alert:
            monitor.record_failure("whatsapp", "timeout")
            monitor.record_failure("whatsapp", "timeout")
            self.assertFalse(alert.called)
            self.assertTrue(monitor.snapshot()["healthy"])

            monitor.record_failure("whatsapp", "401 invalid token")
            self.assertEqual(alert.call_count, 1)
            self.assertFalse(monitor.snapshot()["healthy"])

            # Still failing: alert once, do not spam.
            monitor.record_failure("whatsapp", "401 invalid token")
            self.assertEqual(alert.call_count, 1)

            monitor.record_success("whatsapp")
            self.assertTrue(monitor.snapshot()["healthy"])

    def test_freshness_ingredients_query(self):
        """Verify 'how fresh are the ingredients' matches freshness_ingredients template and not delivery_time."""
        resp_text, matched, tmpl_id, img_url = responder.match_and_respond(
            "how fresh are the ingredients", "test_user_fresh", "whatsapp"
        )
        self.assertTrue(matched)
        self.assertEqual(tmpl_id, "freshness_ingredients")
        self.assertIn("freshness and quality", resp_text.lower())
        self.assertNotIn("sushant lok", resp_text.lower())

    def test_aamar_mishti_250g_query_and_repeat_image_delivery(self):
        """Verify 'aamar mishti 250g' matches collection template and attaches photo cards on every request."""
        from session_memory import session_memory
        session_memory._sessions.clear()

        # Turn 1: User asks for menu -> gets text + menu photo cards
        text1, matched1, tmpl1, img1 = responder.match_and_respond(
            "send the menu", "user_test_menu_suppression", "whatsapp"
        )
        self.assertTrue(matched1)
        self.assertIsNotNone(img1, "First menu query should attach photo cards.")

        # Turn 2: User asks "aamar mishti 250g" -> gets exact price breakdown and photo cards
        text2, matched2, tmpl2, img2 = responder.match_and_respond(
            "aamar mishti 250g", "user_test_menu_suppression", "whatsapp"
        )
        self.assertTrue(matched2)
        self.assertEqual(tmpl2, "aamar_mishti_collection")
        self.assertIn("Collection 1 — Aamar Mishti", text2)
        self.assertIn("250g (9pcs) ₹450", text2)
        self.assertIsNotNone(img2, "Menu/collection query should attach photo cards as requested.")

    def test_order_confirmation_escalation_and_verification(self):
        """Verify order confirmation triggers staff alert with verification status."""
        from escalator import escalator
        from whatsapp_adapter import whatsapp_adapter

        # 1. Test missing address/phone -> flags PENDING VERIFICATION
        res1 = escalator._evaluate_order_verification("I want 2 boxes of Nolen Gur", "no previous history")
        self.assertIn("PENDING VERIFICATION", res1)

        # 2. Test valid address & phone -> flags READY FOR VERIFICATION & INVOICING
        res2 = escalator._evaluate_order_verification(
            "Here is my order: 250g Nolen Gur. Address: Flat 402, Sector 56 Gurgaon, Phone: 9876543210", ""
        )
        self.assertIn("READY FOR VERIFICATION & INVOICING", res2)

        # 3. Test WhatsApp order_next_steps triggers escalation alert
        with patch.object(escalator, "handle_escalation") as mock_esc, patch.object(whatsapp_adapter, "send_message"):
            whatsapp_adapter.process_incoming_message("919999888877", "confirm my order here is my address Sector 43 Gurgaon")
            self.assertTrue(mock_esc.called)

    def test_order_calculator_integration(self):
        """Verify OrderCalculator correctly parses quantities and generates itemized draft receipts."""
        from order_calculator import order_calculator
        msg = "I want to order 2 packs of 250g Nolen Gur sondesh"
        calc_res = order_calculator.parse_and_calculate(msg)
        self.assertIsNotNone(calc_res)
        self.assertIn("ORDER DRAFT RECEIPT", calc_res["formatted_receipt"])
        self.assertIn("Nolen Gur", calc_res["formatted_receipt"])
        self.assertEqual(calc_res["quantity"], 2)
        self.assertEqual(calc_res["total_price"], 1200)

    def test_multi_turn_order_slot_filling(self):
        """PRD Requirement: Multi-turn order slot memory carries preferences across turns without repeating menus."""
        from responder import responder
        from session_memory import session_memory
        session_memory.clear_session("test_slot_user", "whatsapp")

        # Turn 1: User says "rose"
        resp1, match1, tmpl1, _ = responder.match_and_respond("rose", user_id="test_slot_user", channel="whatsapp")
        self.assertTrue(match1)
        self.assertEqual(tmpl1, "sondesh_classic_rose")
        self.assertIn("Which collection and pack size would you like?", resp1)

        # Turn 2: User says "aamar mishti 500g" (combines Rose + Aamar Mishti + 500g)
        resp2, match2, tmpl2, _ = responder.match_and_respond("aamar mishti 500g", user_id="test_slot_user", channel="whatsapp")
        self.assertTrue(match2)
        self.assertEqual(tmpl2, "order_draft_receipt")
        self.assertIn("CAMELLIA ARTISANAL — ORDER DRAFT RECEIPT", resp2)
        self.assertIn("Rose Sondesh", resp2)
        self.assertIn("500g", resp2)
        self.assertIn("₹800", resp2)

    def test_multi_item_cart_receipt(self):
        """PRD Requirement: Ordering multiple items in a single message or adding to cart calculates itemized receipt with grand total."""
        from responder import responder
        from session_memory import session_memory
        session_memory.clear_session("test_multi_cart_user", "whatsapp")

        # Test single message with multiple items: "add rose 250g and jolbhora nolen gur 500g both"
        msg = "add rose 250g and jolbhora nolen gur 500g both"
        resp, match, tmpl, _ = responder.match_and_respond(msg, user_id="test_multi_cart_user", channel="whatsapp")
        self.assertTrue(match)
        self.assertEqual(tmpl, "order_draft_receipt")
        self.assertIn("CAMELLIA ARTISANAL — ORDER DRAFT RECEIPT", resp)
        self.assertIn("Rose Sondesh", resp)
        self.assertIn("Jolbhora Sondesh", resp)
        self.assertIn("₹450", resp)
        self.assertIn("₹1,400", resp)
        self.assertIn("₹1,850", resp)

    def test_cart_item_removal(self):
        """PRD Requirement: Removing an item from cart recalculates receipt for remaining items."""
        from responder import responder
        from session_memory import session_memory
        session_memory.clear_session("test_removal_user", "whatsapp")

        # Step 1: Add 3 items
        msg = "add rose 250g, jolbhora 500g, and mango 250g"
        resp1, match1, tmpl1, _ = responder.match_and_respond(msg, user_id="test_removal_user", channel="whatsapp")
        self.assertTrue(match1)
        self.assertEqual(tmpl1, "order_draft_receipt")
        self.assertIn("Rose Sondesh", resp1)
        self.assertIn("Jolbhora Sondesh", resp1)
        self.assertIn("Mango Sondesh", resp1)

        # Step 2: User says "remove rose"
        resp2, match2, tmpl2, _ = responder.match_and_respond("remove rose", user_id="test_removal_user", channel="whatsapp")
        self.assertTrue(match2)
        self.assertEqual(tmpl2, "order_draft_receipt")
        self.assertIn("Rose Sondesh* has been removed", resp2)
        self.assertNotIn("Rose Sondesh (Aamar", resp2)
        self.assertIn("Jolbhora Sondesh", resp2)
        self.assertIn("Mango Sondesh", resp2)
        self.assertIn("₹1,950", resp2)

    def test_out_of_context_question_does_not_trigger_receipt(self):
        """Out-of-context questions like 'Who is the President of US?' must NOT trigger an order receipt even if draft slots exist."""
        from responder import responder
        from session_memory import session_memory
        session_memory.clear_session("test_ooc_user", "whatsapp")

        # Step 1: User says "rose 250g" -> generates order receipt
        resp1, match1, tmpl1, _ = responder.match_and_respond("rose 250g", user_id="test_ooc_user", channel="whatsapp")
        self.assertTrue(match1)
        self.assertEqual(tmpl1, "order_draft_receipt")

        # Step 2: User asks "Who is the President of US?" -> MUST NOT return order_draft_receipt!
        resp2, match2, tmpl2, _ = responder.match_and_respond("Who is the President of US?", user_id="test_ooc_user", channel="whatsapp")
        self.assertNotEqual(tmpl2, "order_draft_receipt")
        self.assertNotIn("ORDER DRAFT RECEIPT", resp2)

    def test_whatsapp_quick_reply_buttons(self):
        """Verify WhatsAppAdapter includes quick reply interactive buttons for greetings and menu requests."""
        from whatsapp_adapter import whatsapp_adapter
        with patch.object(whatsapp_adapter, "send_message") as mock_send:
            whatsapp_adapter.process_incoming_message("919876543210", "Hi")
            self.assertTrue(mock_send.called)
            _, kwargs = mock_send.call_args
            buttons = kwargs.get("buttons")
            self.assertIsNotNone(buttons)
            self.assertIn("🌸 See Menu", buttons)
            self.assertIn("🛍️ Place Order", buttons)

    def test_parse_raw_menu_text(self):
        """Test MenuParser regex extraction of items, quantities, prices, and categories from unstructured text."""
        from menu_parser import menu_parser
        raw = """
        Bengal Sweet Special:
        • Rose Sondesh 250g - ₹450
        • Jolbhora Nolen Gur 500g - ₹1400
        
        Non-Veg Items:
        • Mutton Kosha Bento 1 box - ₹650
        • Chicken Cutlet 2pcs - ₹350
        """
        parsed = menu_parser.parse_menu_text(raw)
        self.assertTrue(len(parsed["vegetarian"]) >= 2)
        self.assertTrue(len(parsed["non_vegetarian"]) >= 2)
        
        veg_names = [item["item"] for item in parsed["vegetarian"]]
        self.assertTrue(any("Rose" in name for name in veg_names))
        
        nonveg_names = [item["item"] for item in parsed["non_vegetarian"]]
        self.assertTrue(any("Mutton" in name for name in nonveg_names))

    def test_parse_menu_card_endpoint(self):
        """Test POST /admin/parse-menu-card endpoint."""
        headers = {"X-Admin-Key": config.ADMIN_API_KEY} if config.ADMIN_API_KEY else {}
        raw_text = "Rose Sondesh 250g - Rs 450\nKosha Mangsho Bento ₹650 (non veg)"
        response = self.client.post("/admin/parse-menu-card", json={"raw_text": raw_text}, headers=headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["updated_items_count"] >= 2)

    def test_collection_1_and_2_matching(self):
        """Verify that typing 'collection 1' or 'collection 2' matches specific collection templates rather than generic know_more."""
        from responder import responder
        
        resp1, match1, tmpl1, _ = responder.match_and_respond("collection 1", user_id="test_col1", channel="whatsapp")
        self.assertTrue(match1)
        self.assertEqual(tmpl1, "aamar_mishti_collection")
        self.assertIn("Collection 1 — Aamar Mishti", resp1)

        resp2, match2, tmpl2, _ = responder.match_and_respond("collection 2", user_id="test_col2", channel="whatsapp")
        self.assertTrue(match2)
        self.assertEqual(tmpl2, "signature_collection")
        self.assertIn("Collection 2 — Signature Collection", resp2)

    @patch("gemini_responder.httpx.Client")
    def test_gemini_template_refinement(self, mock_httpx_cls):
        """Verify Gemini AI refines template response when enabled."""
        from responder import responder
        from gemini_responder import gemini_responder
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Hello Shoaib! Here are the details for Collection 2 — Signature Collection made from desi A2 cow milk."}]
                    }
                }
            ]
        }
        mock_httpx_cls.return_value.__enter__.return_value.post.return_value = mock_response

        with patch.object(config, "GEMINI_ENABLED", True), patch.object(config, "GEMINI_API_KEY", "fake_key"):
            resp, match, tmpl, _ = responder.match_and_respond("collection 2", user_id="test_ai_refine", channel="whatsapp")
            self.assertTrue(match)
            self.assertEqual(tmpl, "signature_collection")
            self.assertIn("Hello Shoaib!", resp)
            self.assertIn("Collection 2 — Signature Collection", resp)

    def test_how_much_is_sondesh_price_inquiry(self):
        """Verify 'How much is Sondesh?' returns price info template/RAG instead of order receipt draft."""
        from responder import responder
        resp, match, tmpl, _ = responder.match_and_respond("How much is Sondesh?", user_id="test_price_inquiry", channel="whatsapp")
        self.assertTrue(match)
        self.assertNotEqual(tmpl, "order_draft_receipt")
        self.assertEqual(tmpl, "sondesh_price_general")

    def test_admin_update_menu_endpoint(self):
        """Verify POST /admin/update-menu updates menu rotation and reloads responder."""
        headers = {"X-Admin-Key": config.ADMIN_API_KEY} if config.ADMIN_API_KEY else {}
        res = self.client.post(
            "/admin/update-menu",
            headers=headers,
            json={
                "week": "Test Rotation Week",
                "vegetarian": [{"item": "Test Sondesh Special", "price_inr": 499}]
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "success")

    def test_collection_2_and_collection_1_full_precision_menu_details(self):
        """Verify Collection 2, Collection 1, and Kanchagolla responses list all flavours, items, Kanchagolla & Jolbhora explicitly."""
        from responder import responder

        # Collection 2 check
        resp2, match2, tmpl2, _ = responder.match_and_respond("collection 2 options", user_id="test_col2_user", channel="whatsapp")
        self.assertTrue(match2)
        self.assertEqual(tmpl2, "signature_collection")
        self.assertIn("Classic / Rose Sondesh", resp2)
        self.assertIn("Real Vanilla / Cinnamon (Dalchini)", resp2)
        self.assertIn("Thandai / Medjool Dates / Chocolate", resp2)
        self.assertIn("Mahua Sondesh", resp2)
        self.assertIn("Jolbhora (Nolen Gur liquid fill)", resp2)
        self.assertIn("Kanchagolla (Classic / Nolen Gur)", resp2)

        # Collection 1 check
        resp1, match1, tmpl1, _ = responder.match_and_respond("collection 1 options", user_id="test_col1_user", channel="whatsapp")
        self.assertTrue(match1)
        self.assertEqual(tmpl1, "aamar_mishti_collection")
        self.assertIn("Aam Ada", resp1)
        self.assertIn("Jolbhora (Nolen Gur liquid fill)", resp1)
        self.assertIn("Kanchagolla", resp1)

        # Kanchagolla check
        resp_k, match_k, tmpl_k, _ = responder.match_and_respond("kanchagolla price", user_id="test_k_user", channel="whatsapp")
        self.assertTrue(match_k)
        self.assertEqual(tmpl_k, "kanchagolla")
        self.assertIn("Aamar Mishti Collection", resp_k)
        self.assertIn("Signature A2 Collection", resp_k)
        self.assertIn("₹450", resp_k)
        self.assertIn("₹600", resp_k)
        self.assertIn("₹700", resp_k)

    def test_individual_flavour_and_pack_size_queries(self):
        """Verify queries for individual flavours, Mahua special, and pack size breakdowns."""
        from responder import responder

        # Mahua price check
        resp, match, tmpl, imgs = responder.match_and_respond("what are the price of mahua", user_id="test_mahua_user", channel="whatsapp")
        self.assertTrue(match)
        self.assertEqual(tmpl, "mahua_signature_special")
        self.assertIn("₹900", resp)
        self.assertIn("₹1600", resp)
        self.assertIn("₹2900", resp)
        self.assertEqual(imgs, ["/images/Signature%20Sondesh.jpeg"])

        # Classic & Rose price check
        resp, match, tmpl, imgs = responder.match_and_respond("rose sondesh price", user_id="test_rose_user", channel="whatsapp")
        self.assertTrue(match)
        self.assertEqual(tmpl, "sondesh_classic_rose")
        self.assertIn("₹450", resp)
        self.assertIn("₹600", resp)
        self.assertEqual(len(imgs), 2)

        # Vanilla / Mango / Cinnamon check
        resp, match, tmpl, imgs = responder.match_and_respond("real vanilla price", user_id="test_vanilla_user", channel="whatsapp")
        self.assertTrue(match)
        self.assertEqual(tmpl, "sondesh_mango_kesar")
        self.assertIn("₹550", resp)
        self.assertIn("₹650", resp)

        # Thandai / Dates / Nolen Gur check
        resp, match, tmpl, imgs = responder.match_and_respond("thandai price", user_id="test_thandai_user", channel="whatsapp")
        self.assertTrue(match)
        self.assertEqual(tmpl, "sondesh_nolen_gur")
        self.assertIn("₹600", resp)
        self.assertIn("₹700", resp)

        # Jolbhora check
        resp, match, tmpl, imgs = responder.match_and_respond("price of jolbhora", user_id="test_jol_user", channel="whatsapp")
        self.assertTrue(match)
        self.assertEqual(tmpl, "jolbhora")
        self.assertIn("₹750", resp)
        self.assertIn("₹900", resp)

        # Pack sizes (250g, 500g, 1kg)
        resp250, match250, tmpl250, _ = responder.match_and_respond("250g box options", user_id="test_250_user", channel="whatsapp")
        self.assertTrue(match250)
        self.assertEqual(tmpl250, "pack_size_250g")
        self.assertIn("Classic / Rose: ₹450", resp250)
        self.assertIn("Mahua: ₹900", resp250)

        resp500, match500, tmpl500, _ = responder.match_and_respond("500g box options", user_id="test_500_user", channel="whatsapp")
        self.assertTrue(match500)
        self.assertEqual(tmpl500, "pack_size_500g")
        self.assertIn("Classic / Rose: ₹800", resp500)
        self.assertIn("Mahua: ₹1600", resp500)

        resp1k, match1k, tmpl1k, _ = responder.match_and_respond("1kg box options", user_id="test_1k_user", channel="whatsapp")
        self.assertTrue(match1k)
        self.assertEqual(tmpl1k, "pack_size_1kg")
        self.assertIn("Classic / Rose: ₹1500", resp1k)
        self.assertIn("Mahua: ₹2900", resp1k)

    def test_weekly_meals_and_bento_order_calculation(self):
        """Verify order calculation for weekly bentos, combos, and curries."""
        from responder import responder

        # Single meal order
        resp, match, tmpl, _ = responder.match_and_respond("I want to order 2 veg bento", user_id="test_bento_u1", channel="whatsapp")
        self.assertTrue(match)
        self.assertEqual(tmpl, "order_draft_receipt")
        self.assertIn("Veg Bento Box", resp)
        self.assertIn("2 boxes", resp)
        self.assertIn("₹800", resp)

        # Multi-item meal + sweet order
        resp2, match2, tmpl2, _ = responder.match_and_respond(
            "Please deliver 1 non veg bento and 1 classic sondesh 500g",
            user_id="test_bento_u2",
            channel="whatsapp"
        )
        self.assertTrue(match2)
        self.assertEqual(tmpl2, "order_draft_receipt")
        self.assertIn("Non-Veg Bento Box", resp2)
        self.assertIn("Classic Sondesh", resp2)
        self.assertIn("₹1,500", resp2)

if __name__ == "__main__":
    unittest.main()




