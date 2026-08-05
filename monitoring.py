import logging
import os
import sys

from config import config

_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def get_logger(name: str) -> logging.Logger:
    """Returns a configured logger. Replaces bare print() so a real log
    aggregator (or `docker compose logs`) can filter by severity."""
    logger = logging.getLogger(name)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=getattr(logging, _LEVEL, logging.INFO),
            format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            stream=sys.stdout,
        )
        # httpx logs a line per outbound Meta call at INFO, which drowns out ours.
        logging.getLogger("httpx").setLevel(logging.WARNING)
    return logger


log = get_logger("camellia")


class DeliveryHealthMonitor:
    """
    Watches outbound Meta API calls and alerts the owner once when sending
    starts failing consistently.

    The failure this exists for is a silently expired WHATSAPP_ACCESS_TOKEN: the
    bot keeps accepting webhooks and looks healthy, but every reply is dropped,
    and nobody notices until a customer complains. Counts consecutive failures
    per channel so a single network blip does not page anyone.
    """

    def __init__(self, failure_threshold: int = 3):
        self.failure_threshold = failure_threshold
        self._consecutive_failures = {}
        self._alerted = set()

    def record_success(self, channel: str) -> None:
        if self._consecutive_failures.get(channel):
            log.info("Outbound delivery on %s recovered.", channel)
        self._consecutive_failures[channel] = 0
        self._alerted.discard(channel)

    def record_failure(self, channel: str, detail: str) -> None:
        count = self._consecutive_failures.get(channel, 0) + 1
        self._consecutive_failures[channel] = count
        log.error("Outbound delivery failed on %s (%s consecutive): %s", channel, count, detail)

        if count >= self.failure_threshold and channel not in self._alerted:
            self._alerted.add(channel)
            self._send_owner_alert(channel, count, detail)

    def _send_owner_alert(self, channel: str, count: int, detail: str) -> None:
        message = (
            f"[CAMELLIA BOT ALERT] {count} consecutive send failures on {channel}. "
            f"Customers are NOT receiving replies. Most likely cause: expired "
            f"WHATSAPP_ACCESS_TOKEN. Last error: {detail[:200]}"
        )
        log.critical(message)

        # Best-effort WhatsApp alert. If the credential itself is what broke,
        # this cannot get through — the CRITICAL log line above is the fallback.
        if not (config.WHATSAPP_ACCESS_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID):
            return
        try:
            import httpx

            url = (
                f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/"
                f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
            )
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    url,
                    headers={"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": config.HUMAN_ESCALATION_PHONE.lstrip("+"),
                        "type": "text",
                        "text": {"body": message},
                    },
                )
        except Exception as exc:
            log.error("Could not deliver health alert to owner: %s", exc)

    def snapshot(self) -> dict:
        return {
            "consecutive_failures": dict(self._consecutive_failures),
            "channels_alerting": sorted(self._alerted),
            "healthy": not self._alerted,
        }


delivery_monitor = DeliveryHealthMonitor()
