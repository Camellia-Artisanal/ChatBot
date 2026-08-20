import json
from responder import responder
from session_memory import session_memory

def run_simulation():
    agents = [
        {
            "id": "agent_1_ananya",
            "name": "Ananya Sharma (Corporate Event Coordinator)",
            "turns": [
                "Hi, I'm Ananya from DLF CyberCity. We have an office Diwali high-tea tomorrow afternoon. Can we order 25 boxes of Signature Sondesh (500g each)?",
                "Can you do custom branding or ribbons on the boxes, and what discount for bulk orders?",
                "How will delivery work for 25 boxes to CyberHub DLF Phase 2?"
            ]
        },
        {
            "id": "agent_2_debasish",
            "name": "Debasish Roy (Nostalgic Bengali NRI)",
            "turns": [
                "Nomoshkar! I am Debasish. Is the Jolbhora sondesh made with real liquid nolen gur inside like Kolkata, or is it dry?",
                "What is the price of Jolbhora Talgash and Kanchagolla in Collection 2?",
                "Are you delivering to Chittaranjan Park (CR Park) on Sunday morning?"
            ]
        },
        {
            "id": "agent_3_vikram",
            "name": "Dr. Vikram Malhotra (Health/Diabetic Inquirer)",
            "turns": [
                "Hello, my name is Dr. Vikram. Do you have sugar-free or low glycemic index sweets for elderly diabetic patients?",
                "What are the exact ingredients in your A2 cow milk norom pak?",
                "How many calories roughly per piece and what is the shelf life in fridge?"
            ]
        },
        {
            "id": "agent_4_siddharth",
            "name": "Siddharth Verma (Weekend Bento Lunch Seeker)",
            "turns": [
                "Hey! What is on the Bengali lunch menu for this Saturday?",
                "What is the price of the Mutton Kasha & Basonti Polao meal box?",
                "I want to order 2 Mutton meals and 1 Rui Macher Kalia meal to Indirapuram Ghaziabad. How do I place this order?"
            ]
        },
        {
            "id": "agent_5_meenakshi",
            "name": "Meenakshi Iyer (Confused Portions/Price Shopper)",
            "turns": [
                "Hi! How many pieces of sondesh do we get in a 250g box vs 500g box?",
                "Is Collection 1 250g Rs 450 or Rs 250?",
                "What is your best seller flavour among both collections?"
            ]
        },
        {
            "id": "agent_6_kabir",
            "name": "Kabir (Hinglish Casual Texter)",
            "turns": [
                "Bhai sondesh ka menu bhejo jaldi",
                "Noida sector 62 deliver hoga kya aaj shaam tak? Kitna time lagega?",
                "COD available hai ya sirf online pay?"
            ]
        },
        {
            "id": "agent_7_tanya",
            "name": "Tanya Kapoor (Allergy & Custom Assortment)",
            "turns": [
                "Hi, I have a severe nut allergy (cashew & pistachio). Are your sondesh made in a nut-free kitchen or do you have nut-free flavours?",
                "Can I get a 1kg box with purely Rose and Nolen Gur Norom Pak without any pistachio garnish?",
                "Can you send the UPI QR code to pay?"
            ]
        },
        {
            "id": "agent_8_rajesh",
            "name": "Rajesh Khanna (Frustrated Customer Escalation)",
            "turns": [
                "I placed an order 2 hours ago through WhatsApp and paid ₹1200 via GPay, but no one has confirmed or sent delivery tracking yet!",
                "Give me the manager's phone number right now.",
                "What is your kitchen address if I want to come pick it up?"
            ]
        },
        {
            "id": "agent_9_pooja",
            "name": "Pooja Agarwal (Bangalore Outstation Inquirer)",
            "turns": [
                "Hello! Can you courier 2 boxes of Nolen Gur Sondesh to Indiranagar, Bangalore?",
                "Oh, that's sad. Can my cousin in Gurgaon pick it up from your kitchen directly?",
                "What are your kitchen timings for self-pickup?"
            ]
        },
        {
            "id": "agent_10_rohan",
            "name": "Rohan Sengupta (Multi-Step Order Placement)",
            "turns": [
                "Hi! My name is Rohan.",
                "I want 2 boxes of 500g Rose Sondesh from Collection 1.",
                "Delivery address is Golf Course Road, Sector 54 Gurgaon, phone 9811223344.",
                "Confirm my order please."
            ]
        }
    ]

    results = []
    issues_found = []

    print("=" * 80)
    print("STARTING 10-HUMAN-AGENT SIMULATION TEST")
    print("=" * 80)

    for agent in agents:
        aid = agent["id"]
        aname = agent["name"]
        session_memory.clear_session(aid, "whatsapp")
        print(f"\n--- [AGENT]: {aname} ({aid}) ---")

        agent_log = {"agent": aname, "id": aid, "dialogue": []}

        for turn_idx, user_msg in enumerate(agent["turns"], 1):
            resp_text, was_matched, tmpl_id, img_url = responder.match_and_respond(
                user_msg, user_id=aid, channel="whatsapp"
            )
            facts = session_memory.get_facts(aid, "whatsapp")

            print(f"\n[Turn {turn_idx}] User: {user_msg}")
            print(f"[Turn {turn_idx}] Bot (matched={was_matched}, tmpl={tmpl_id}):\n{resp_text[:180]}...")
            if img_url:
                print(f"[Turn {turn_idx}] Images attached: {img_url}")

            turn_data = {
                "turn": turn_idx,
                "user_msg": user_msg,
                "bot_resp": resp_text,
                "was_matched": was_matched,
                "tmpl_id": tmpl_id,
                "img_url": img_url,
                "session_facts": facts
            }
            agent_log["dialogue"].append(turn_data)

            # Basic quality checks
            if not was_matched and tmpl_id not in ["explicit_escalation", "gemini_rag"]:
                issue = f"Agent {aname} Turn {turn_idx}: Unmatched fallback trigger for: '{user_msg}'"
                issues_found.append(issue)
            if not resp_text or len(resp_text.strip()) < 10:
                issue = f"Agent {aname} Turn {turn_idx}: Empty or extremely short response."
                issues_found.append(issue)

        results.append(agent_log)

    print("\n" + "=" * 80)
    print("SIMULATION SUMMARY")
    print("=" * 80)
    print(f"Total Agents Tested: {len(agents)}")
    print(f"Total Issues Detected: {len(issues_found)}")
    for iss in issues_found:
        print(f" - {iss}")

    with open("simulation_results.json", "w", encoding="utf-8") as f:
        json.dump({"results": results, "issues": issues_found}, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    run_simulation()
