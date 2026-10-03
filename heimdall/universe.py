"""Univers suivi : top N des cryptos supportées par Tangem, hors stablecoins et actifs adossés."""
import json
import re
import time

from heimdall import config
from heimdall.http import get_json
from heimdall.market import coins_markets

TANGEM_API = "https://api.tangem.org/v1/coins"
CACHE_FILE = config.CACHE_DIR / "universe.json"
CACHE_TTL_S = 24 * 3600


def _excluded_ids() -> set[str]:
    ids = set(config.EXCLUDED_IDS)
    for cat in config.EXCLUDED_CATEGORIES:
        try:
            ids.update(c["id"] for c in coins_markets(category=cat, per_page=250))
        except Exception as e:  # une catégorie renommée ne doit pas tout bloquer
            print(f"[universe] catégorie {cat} ignorée : {e}")
        time.sleep(8)  # limite de débit de l'API gratuite
    return ids


def _looks_pegged(c: dict) -> bool:
    """Prix quasi immobile sur 7 et 30 jours : stablecoin ou actif adossé (bon du Trésor, or...)."""
    ch7 = c.get("price_change_percentage_7d_in_currency")
    ch30 = c.get("price_change_percentage_30d_in_currency")
    return ch7 is not None and ch30 is not None and abs(ch7) < 1.5 and abs(ch30) < 3


def _excluded_by_name(c: dict) -> bool:
    return bool(re.search(config.EXCLUDED_NAME_PATTERN, c["name"]))


def _tangem_supported(ids: list[str]) -> set[str]:
    supported = set()
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        d = get_json(TANGEM_API, params={"ids": ",".join(chunk), "active": "true"})
        supported.update(c["id"] for c in d.get("coins", []))
    return supported


def build_universe(size: int = config.UNIVERSE_SIZE) -> list[dict]:
    excluded = _excluded_ids()
    candidates = []
    for page in (1, 2):
        candidates += coins_markets(page=page)
        time.sleep(8)
    candidates = [c for c in candidates
                  if c["id"] not in excluded and not _looks_pegged(c) and not _excluded_by_name(c)]
    supported = _tangem_supported([c["id"] for c in candidates])
    universe = [
        {"id": c["id"], "symbol": c["symbol"].upper(), "name": c["name"], "rank": c.get("market_cap_rank")}
        for c in candidates if c["id"] in supported
    ][:size]
    return universe


def get_universe(force: bool = False) -> list[dict]:
    """Univers mis en cache 24h pour ménager les API gratuites."""
    # La date est stockée dans le fichier : sur GitHub Actions, la date du fichier est celle du checkout.
    if not force and CACHE_FILE.exists():
        cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and time.time() - cached.get("built_at", 0) < CACHE_TTL_S:
            return cached["coins"]
    universe = build_universe()
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({"built_at": time.time(), "coins": universe}, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    return universe


if __name__ == "__main__":
    u = get_universe(force=True)
    print(f"{len(u)} cryptos suivies :")
    print(", ".join(c["symbol"] for c in u))
