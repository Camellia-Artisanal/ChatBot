import os
import json
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
        # Clear tables between tests
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions;")
            cursor.execute("DELETE FROM conversations;")
            cursor.execute("DELETE FROM escalations;")
            cursor.execute("UPDATE analytics SET value = 0;")
            conn.commit()

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

    def test_responder_sondesh_pricing(self):
        """Test pricing query returns correct predefined template and photo card."""
        response, was_matched, tmpl_id, img_url = responder.match_and_respond("How much is Sondesh?")
        self.assertTrue(was_matched)
        self.assertEqual(tmpl_id, "sondesh_price_general")
        self.assertIn("Camellia Artisanal", response)
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
        """Out-of-syllabus query must escalate without guessing."""
        response, was_matched, tmpl_id, img_url = responder.match_and_respond("Do you sell Chinese Hakka Noodles?")
        self.assertFalse(was_matched)
        self.assertTrue(tmpl_id is None or tmpl_id.startswith("ollama_"))
        self.assertIn("connect you with our team", response)

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
        res_post = self.client.post("/webhook/instagram", json=payload_organic)
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
        res_post_ad = self.client.post("/webhook/instagram", json=payload_ad)
        self.assertEqual(res_post_ad.status_code, 200)
        data_ad = res_post_ad.json()
        self.assertEqual(data_ad["results"][0]["action"], "auto_replied")

    @patch("httpx.Client.post")
    def test_ollama_in_context_query(self, mock_post):
        """Test Ollama RAG responds accurately for in-syllabus queries when deterministic match score is low."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "content": "We offer artisanal Nolen Gur Sondesh made with authentic date palm jaggery delivered fresh across Delhi NCR!"
            }
        }
        mock_post.return_value = mock_response

        # Ask a query that doesn't trigger explicit keywords or deterministic templates directly
        resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("Do you use organic sweeteners or natural preservatives in your sweets?")
        self.assertTrue(was_matched)
        self.assertEqual(tmpl_id, "ollama_rag")
        self.assertIn("Nolen Gur Sondesh", resp_text)

    @patch("httpx.Client.post")
    def test_ollama_out_of_context_refusal(self, mock_post):
        """Test Ollama RAG refuses out-of-syllabus query with OUT_OF_CONTEXT and triggers human escalation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "content": "OUT_OF_CONTEXT"
            }
        }
        mock_post.return_value = mock_response

        resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("What is the capital of France?")
        self.assertFalse(was_matched)
        self.assertEqual(tmpl_id, "ollama_out_of_context")
        self.assertIn("connect you with our team", resp_text)

    @patch("httpx.Client.post")
    def test_ollama_offline_fallback(self, mock_post):
        """Test graceful fallback to human escalation if Ollama server is unreachable/offline."""
        mock_post.side_effect = Exception("Connection refused to localhost:11434")

        resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("Can you send a violin player for a wedding?")
        self.assertFalse(was_matched)
        self.assertEqual(tmpl_id, "ollama_ollama_exception_Exception")

    def test_session_memory_fact_extraction(self):
        """Test extraction of customer name and dietary/allergy preferences in Session Memory."""
        from session_memory import session_memory
        session_memory.clear_session("test_mem_user", "whatsapp")

        facts = session_memory.extract_and_update_facts(
            "test_mem_user", "whatsapp", "Hi, my name is Shoaib and I have a nut allergy and diabetes"
        )
        self.assertEqual(facts["name"], "Shoaib")
        self.assertIn("Nut Allergy", facts["allergies"])
        self.assertIn("Diabetic / Sugar-Free Preference", facts["allergies"])

    def test_allergy_warning_guard_in_responder(self):
        """Test that responder automatically appends allergy/dietary warnings when user declared allergies."""
        from session_memory import session_memory
        user_id = "user_allergy_test"
        channel = "whatsapp"
        session_memory.clear_session(user_id, channel)

        # 1. User declares nut allergy
        responder.match_and_respond("I'm Shoaib and I have a nut allergy", user_id=user_id, channel=channel)

        # 2. User asks about Sondesh pricing
        resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("How much is Sondesh?", user_id=user_id, channel=channel)
        self.assertTrue(was_matched)
        self.assertIn("Nut Allergy", resp_text)
        self.assertIn("Dietary Note for Shoaib", resp_text)

    def test_jaggery_allergy_declaration(self):
        """Test declaring a jaggery allergy triggers allergy_declaration template, extracts allergy, and appends warning."""
        from session_memory import session_memory
        user_id = "user_jaggery_test"
        channel = "whatsapp"
        session_memory.clear_session(user_id, channel)

        # 1. User introduces name & declares jaggery allergy
        responder.match_and_respond("My name is Huzaifa", user_id=user_id, channel=channel)
        resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("I have a jaggery allergy", user_id=user_id, channel=channel)
        
        self.assertTrue(was_matched)
        self.assertEqual(tmpl_id, "allergy_declaration")
        self.assertIn("Jaggery / Nolen Gur Allergy", resp_text)
        self.assertIn("Dietary Note for Huzaifa", resp_text)

    def test_vegan_declaration(self):
        """Test declaring 'i am a vegan' matches allergy_declaration and extracts Strict Vegan preference."""
        from session_memory import session_memory
        user_id = "user_vegan_test"
        channel = "instagram"
        session_memory.clear_session(user_id, channel)

        resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("i am a vegan", user_id=user_id, channel=channel)
        
        self.assertTrue(was_matched)
        self.assertEqual(tmpl_id, "allergy_declaration")
        self.assertIn("Strict Vegan", resp_text)

    def test_escalation_handoff_brief_generation(self):
        """Test escalation handoff brief includes customer name, allergies, and recent chat history."""
        from session_memory import session_memory
        user_id = "user_handoff_test"
        channel = "instagram"
        session_memory.clear_session(user_id, channel)

        # 1. Establish session memory turns
        responder.match_and_respond("Call me Shoaib", user_id=user_id, channel=channel)
        responder.match_and_respond("I am diabetic", user_id=user_id, channel=channel)

        # 2. Trigger unmatched escalation
        resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond("Can you send a violin player for a wedding?", user_id=user_id, channel=channel)
        escalator.handle_escalation(user_id, channel, "Can you send a violin player for a wedding?", "Unmatched Query", {})

        escalation_record = db.get_escalations(limit=1)[0]
        
        context_data = json.loads(escalation_record["context_data"])
        self.assertIn("handoff_brief", context_data)
        brief = context_data["handoff_brief"]
        self.assertEqual(brief["customer_name"], "Shoaib")
        self.assertIn("Diabetic / Sugar-Free Preference", brief["allergies_and_dietary"])
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
        res = self.client.post("/api/reset-session", json={"user_id": user_id, "channel": channel})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "cleared")

        # 3. Verify memory is empty
        self.assertIsNone(session_memory.get_session(user_id, channel)["facts"]["name"])

if __name__ == "__main__":
    unittest.main()

