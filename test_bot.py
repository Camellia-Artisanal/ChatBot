import os
import json
import sqlite3
import unittest
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

    def test_responder_explicit_escalation(self):
        """Sensitive queries (refund/complaint) must trigger explicit escalation."""
        response, was_matched, tmpl_id, img_url = responder.match_and_respond("I want a refund for my order")
        self.assertFalse(was_matched)
        self.assertEqual(tmpl_id, "explicit_escalation")

    def test_responder_unmatched_out_of_syllabus(self):
        """Out-of-syllabus query must escalate without guessing."""
        response, was_matched, tmpl_id, img_url = responder.match_and_respond("Do you sell Chinese Hakka Noodles?")
        self.assertFalse(was_matched)
        self.assertIsNone(tmpl_id)
        self.assertIn("connect you with our team", response)

    def test_whatsapp_adapter_flow(self):
        """PRD Requirement #2: WhatsApp integration handling."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "919876543210",
                            "id": "wmid_123",
                            "type": "text",
                            "text": {"body": "Hi, what Sondesh flavours do you have?"}
                        }]
                    }
                }]
            }]
        }
        parsed = whatsapp_adapter.parse_webhook_message(payload)
        self.assertIsNotNone(parsed)
        sender, msg_id, text = parsed
        self.assertEqual(sender, "919876543210")
        
        result = whatsapp_adapter.process_incoming_message(sender, text)
        self.assertTrue(result["was_matched"])
        self.assertEqual(result["template_id"], "sondesh_price_general")

    def test_database_analytics(self):
        """Verifies database logging and analytics summary."""
        db.log_conversation("user1", "instagram", "hi", "hello", was_matched=True, template_id="greeting")
        db.log_conversation("user2", "whatsapp", "unknown", "escalated", was_matched=False)

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

if __name__ == "__main__":
    unittest.main()
