"""
Meaning match for Custom-response variations, in shadow.

A message heading for a handoff on a mapping with saved phrases is checked in
the background: does it mean the same as one of those phrases, and does the
mapping's reply answer it? The result only ever goes to variation_shadow, which
nothing in the reply path reads, so a customer never sees it. It is the
evidence for whether meaning match can be trusted with live traffic.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from camellia.ai_responder import ai_responder
from camellia.config import config
from camellia.database import db
from camellia.monitoring import get_logger

log = get_logger("camellia.variation_shadow")

WOULD_SEND_CONFIDENCE = 0.85
_SHADOW_WORKERS = 2
_SHADOW_QUEUE_LIMIT = 64


def match(message: str, phrases: List[str], reply: str) -> Dict[str, Any]:
    """Which saved phrase this message means, or none. Failure is "no match"."""
    numbered = "\n".join(f"{index}. {phrase}" for index, phrase in enumerate(phrases, 1))
    prompt = (
        "A food brand's Instagram bot has a REPLY and the customer phrases that trigger it.\n"
        'Return exactly one JSON object: {"phrase":<number or 0>,"confidence":<0 to 1>}.\n\n'
        "RULES:\n"
        "- Pick a phrase only if the MESSAGE asks the same thing as it, in any language or spelling.\n"
        "- Return 0 if the REPLY would not fully answer the MESSAGE.\n"
        "- Return 0 if the MESSAGE names a city, date, quantity or product the REPLY does not cover.\n"
        "- When unsure, return 0.\n\n"
        f"PHRASES:\n{numbered}\n\nREPLY:\n{reply}\n\nMESSAGE:\n{message}\n"
    )
    raw, ok, reason = ai_responder._query_groq(
        prompt, model=config.GROQ_ROUTER_MODEL, response_format={"type": "json_object"},
        max_tokens=150, reasoning_effort="low",
    )
    if not ok:
        return {"ok": False, "reason": reason}
    try:
        parsed = json.loads(raw)
        number, confidence = int(parsed.get("phrase") or 0), float(parsed.get("confidence") or 0)
    except (AttributeError, TypeError, ValueError):
        return {"ok": False, "reason": "invalid_json"}
    if not 0 < number <= len(phrases):
        return {"ok": True, "phrase": None, "confidence": confidence}
    return {"ok": True, "phrase": phrases[number - 1], "confidence": min(max(confidence, 0.0), 1.0)}


class VariationShadow:
    def __init__(self):
        self._pool: Optional[ThreadPoolExecutor] = None
        self._pending = 0
        self._lock = threading.Lock()

    def record_shadow(self, message: str, route: Dict[str, Any]) -> None:
        """Queues the check and returns at once. Never raises: the reply is already decided."""
        phrases = list(route.get("custom_response_variations") or [])
        reply = str(route.get("source_description") or "").strip()
        if not config.VARIATION_SHADOW_ENABLED or not phrases or not reply:
            return
        try:
            with self._lock:
                if self._pending >= _SHADOW_QUEUE_LIMIT:
                    log.warning("Variation shadow queue is full; skipping this message.")
                    return
                self._pending += 1
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(
                        max_workers=_SHADOW_WORKERS, thread_name_prefix="variation-shadow",
                    )
            self._pool.submit(self._run, message, phrases, reply, route.get("routing_id"))
        except Exception as e:
            with self._lock:
                self._pending = max(0, self._pending - 1)
            log.warning("Could not queue a variation shadow check: %s", e)

    def _run(self, message: str, phrases: List[str], reply: str, routing_id: Optional[int]) -> None:
        started = time.perf_counter()
        result: Dict[str, Any] = {"ok": False, "reason": "unrun"}
        try:
            result = match(message, phrases, reply)
            phrase, confidence = result.get("phrase"), result.get("confidence")
            db.record_variation_shadow(
                routing_id=routing_id, user_message=message, matched_phrase=phrase,
                confidence=confidence,
                would_send=bool(phrase and confidence >= WOULD_SEND_CONFIDENCE),
                detail=result.get("reason", "ok"),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as e:
            log.warning("Variation shadow check failed: %s", e)
        finally:
            with self._lock:
                self._pending = max(0, self._pending - 1)


variation_shadow = VariationShadow()
