"""Actualités : flux RSS des médias crypto, SEC, Fed, Reddit."""
import calendar
import html
import re
import time

import feedparser

from heimdall import config
from heimdall.http import get_text

# Symboles qui, même en majuscules, désignent souvent autre chose (sigles, mots anglais, Trump la personne).
AMBIGUOUS_SYMBOLS = {"ONE", "IT", "AI", "U", "M", "S", "GT", "OP", "W", "IP", "ME", "ZK", "ID", "XAI", "TRUMP", "STABLE", "SAFE", "SUN", "MOVE", "FLOW", "CORE", "GAS", "BP", "UB", "FF", "MON", "KITE", "SPX"}


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_news(since_ts: float = 0, max_per_feed: int = 40) -> list[dict]:
    items = []
    for source, url in config.RSS_FEEDS.items():
        try:
            feed = feedparser.parse(get_text(url))
        except Exception as e:
            print(f"[news] {source} indisponible : {e}")
            continue
        for e in feed.entries[:max_per_feed]:
            parsed = e.get("published_parsed") or e.get("updated_parsed")
            ts = calendar.timegm(parsed) if parsed else time.time()
            if ts <= since_ts:
                continue
            items.append({
                "source": source,
                "title": _clean(e.get("title", "")),
                "summary": _clean(e.get("summary", ""))[:400],
                "link": e.get("link", ""),
                "published_ts": ts,
            })
    items.sort(key=lambda x: x["published_ts"], reverse=True)
    return items


# Noms qui sont aussi des mots anglais courants : on ne les cherche que sous la forme "$SYMBOLE".
AMBIGUOUS_NAMES = {"near", "sky", "stable", "rain", "night", "pump", "gram", "lit", "core", "flow", "move", "safe", "sun"}


def _coin_patterns(universe: list[dict]) -> list[tuple[dict, re.Pattern]]:
    pats = []
    for c in universe:
        sym = c["symbol"]
        terms = [rf"\${re.escape(sym)}\b"]
        if c["name"].lower() not in AMBIGUOUS_NAMES:
            terms.append(rf"\b{re.escape(c['name'])}\b")
        if sym not in AMBIGUOUS_SYMBOLS and len(sym) >= 3:
            terms.append(rf"\b{re.escape(sym)}\b")
        # Recherche sensible à la casse : "Solana" et "SOL", pas "sol" ni "near".
        pats.append((c, re.compile("|".join(terms))))
    return pats


def tag_coins(items: list[dict], universe: list[dict]) -> list[dict]:
    """Ajoute à chaque news la liste des symboles des cryptos mentionnées."""
    pats = _coin_patterns(universe)
    for it in items:
        text = f"{it['title']} {it['summary']}"
        it["coins"] = [c["symbol"] for c, p in pats if p.search(text)]
    return items
