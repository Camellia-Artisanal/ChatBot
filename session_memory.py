import re
from collections import OrderedDict
from typing import Dict, Any

class SessionMemoryManager:
    """
    Manages in-memory active chat sessions keyed by (user_id, channel).
    Extracts customer facts (Name) and maintains conversation turn history
    for multi-turn context and human escalation handoffs.
    """
    def __init__(self, max_history_turns: int = 5, max_sessions: int = 5000):
        self.max_history_turns = max_history_turns
        self.max_sessions = max_sessions
        # Structure: {(user_id, channel): {"facts": {"name": str}, "turns": [...]}}
        # Ordered so the least-recently-touched session can be evicted at capacity.
        self._sessions: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()

    def get_session(self, user_id: str, channel: str) -> Dict[str, Any]:
        key = (str(user_id), str(channel))
        if key not in self._sessions:
            self._sessions[key] = {
                "facts": {
                    "name": None
                },
                "turns": [] # List of {"role": "user"|"assistant", "content": str}
            }
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
        else:
            self._sessions.move_to_end(key)
        return self._sessions[key]

    def clear_session(self, user_id: str, channel: str) -> None:
        key = (str(user_id), str(channel))
        if key in self._sessions:
            del self._sessions[key]

    def extract_and_update_facts(self, user_id: str, channel: str, text: str) -> Dict[str, Any]:
        """Extracts customer attributes like Name from user message."""
        session = self.get_session(user_id, channel)
        facts = session["facts"]
        clean_text = text.strip()

        # Extract Name
        # Patterns: "my name is Shoaib", "i am Shoaib", "i'm Shoaib", "call me Shoaib", "name: Shoaib"
        name_patterns = [
            r"(?:my\s+name\s+is|i\s+am|i'm|call\s+me|name\s*[:=])\s+([A-Z][a-z]+|[a-z]+)",
        ]
        for pattern in name_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                extracted_name = match.group(1).capitalize()
                # Ignore common words misidentified as names
                ignored_words = {"a", "an", "the", "asking", "looking", "ordering", "wanting", "having", "here", "there"}
                if extracted_name.lower() not in ignored_words and len(extracted_name) >= 2:
                    facts["name"] = extracted_name
                    break

        return facts

    def add_turn(self, user_id: str, channel: str, user_message: str, assistant_response: str) -> None:
        """Appends user input and bot response to session history."""
        session = self.get_session(user_id, channel)
        turns = session["turns"]

        turns.append({"role": "user", "content": user_message})
        turns.append({"role": "assistant", "content": assistant_response})

        # Keep history bounded to max_history_turns * 2 (turns pair = user + assistant)
        if len(turns) > self.max_history_turns * 2:
            session["turns"] = turns[-(self.max_history_turns * 2):]

    def get_context_summary(self, user_id: str, channel: str) -> str:
        """Generates a text summary of active customer facts for Ollama system prompt."""
        session = self.get_session(user_id, channel)
        facts = session["facts"]
        
        summary_lines = []
        if facts["name"]:
            summary_lines.append(f"Customer Name: {facts['name']}")

        if not summary_lines:
            return "No prior customer details recorded."
        return "\n".join(summary_lines)

    def get_handoff_brief(self, user_id: str, channel: str, escalation_reason: str = "Unmatched Query") -> Dict[str, Any]:
        """Generates a structured handoff brief for human staff when escalating."""
        session = self.get_session(user_id, channel)
        facts = session["facts"]
        turns = session["turns"]

        turn_history = []
        for t in turns:
            role = "Customer" if t["role"] == "user" else "Bot"
            turn_history.append(f"{role}: {t['content']}")

        return {
            "customer_name": facts["name"] or "Unknown",
            "recent_chat_history": turn_history,
            "escalation_reason": escalation_reason
        }

# Global Singleton Memory Manager
session_memory = SessionMemoryManager()
