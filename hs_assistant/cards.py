from __future__ import annotations

import json
import threading
import urllib.request

from .config import CACHE_DIR

LOCALE = "zhCN"
CARDS_URL = f"https://api.hearthstonejson.com/v1/latest/{LOCALE}/cards.json"
CARDS_PATH = CACHE_DIR / f"cards_{LOCALE}.json"


class CardDatabase:
    """Card names and text from HearthstoneJSON, downloaded once and cached."""

    def __init__(self) -> None:
        self._cards: dict[str, dict] = {}
        self.ready = False
        self.error: str | None = None

    def load_async(self) -> None:
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            if not CARDS_PATH.exists():
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(CARDS_URL, headers={"User-Agent": "hs-assistant"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    CARDS_PATH.write_bytes(resp.read())
            cards = json.loads(CARDS_PATH.read_text(encoding="utf-8"))
            self._cards = {c["id"]: c for c in cards if "id" in c}
            self.ready = True
        except Exception as exc:  # noqa: BLE001 - names are optional, keep running
            self.error = str(exc)

    def get(self, card_id: str) -> dict | None:
        return self._cards.get(card_id) if card_id else None

    def name(self, card_id: str) -> str | None:
        card = self.get(card_id)
        return card.get("name") if card else None

    def text(self, card_id: str) -> str:
        card = self.get(card_id)
        if not card:
            return ""
        # Text after "@" is an alternate wording (e.g. upgraded hero power).
        text = card.get("text", "").split("@", 1)[0]
        for tag in ("<b>", "</b>", "<i>", "</i>", "[x]", "$", "#"):
            text = text.replace(tag, "")
        return " ".join(text.split())
