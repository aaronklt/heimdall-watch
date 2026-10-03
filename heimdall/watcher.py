"""Veille temps réel : mots-clés dans les news + variations brutales de prix -> alerte Telegram.

Usage :
  python -m heimdall.watcher          # tourne en continu (sur le serveur)
  python -m heimdall.watcher --once   # un seul passage (test)
  python -m heimdall.watcher --dry    # affiche les alertes sans les envoyer
"""
import argparse
import json
import re
import time
import traceback

from heimdall import config, market, news, telegram
from heimdall.http import post_json
from heimdall.universe import get_universe

STATE_FILE = config.STATE_DIR / "watcher.json"
LOOP_SECONDS = 300
PRICE_EVERY_N_LOOPS = 2  # prix toutes les 10 min : reste dans le quota gratuit CoinGecko
NEWS_COOLDOWN_H = 3

KEYWORD_PATTERNS = {
    level: [(kw, re.compile(rf"\b{re.escape(kw)}\b")) for kw in kws]
    for level, kws in config.ALERT_KEYWORDS.items()
}


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    # Premier lancement : on ignore l'historique pour ne pas envoyer 200 vieilles alertes.
    return {"last_news_ts": time.time() - 3600, "seen_links": [], "cooldowns": {}}


def save_state(state: dict) -> None:
    state["seen_links"] = state["seen_links"][-1000:]
    now = time.time()
    state["cooldowns"] = {k: v for k, v in state["cooldowns"].items() if v > now}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def _on_cooldown(state: dict, key: str, hours: float) -> bool:
    now = time.time()
    if state["cooldowns"].get(key, 0) > now:
        return True
    state["cooldowns"][key] = now + hours * 3600
    return False


def check_news(state: dict, universe: list[dict]) -> list[dict]:
    items = news.tag_coins(news.fetch_news(since_ts=state["last_news_ts"] - 600), universe)
    alerts = []
    for it in items:
        if it["link"] in state["seen_links"]:
            continue
        state["seen_links"].append(it["link"])
        state["last_news_ts"] = max(state["last_news_ts"], it["published_ts"])
        # Mots-clés cherchés dans le titre seulement : les résumés génèrent trop de faux positifs.
        title = it["title"].lower()
        hits = {lvl: [kw for kw, p in pats if p.search(title)] for lvl, pats in KEYWORD_PATTERNS.items()}
        is_reddit = it["source"].startswith("Reddit")
        crypto_context = it["coins"] or any(w in title for w in config.CRYPTO_CONTEXT_WORDS)
        critical = hits["critical"] and crypto_context and (it["coins"] or not is_reddit)
        official = hits["macro"] and it["source"] in config.OFFICIAL_SOURCES
        coin_event = hits["coin"] and it["coins"]
        if not (critical or official or coin_event):
            continue
        critical = critical or official
        kws = hits["critical"] + hits["macro"] + hits["coin"]
        key = f"news:{kws[0]}:{','.join(it['coins'][:3]) or 'global'}"
        if _on_cooldown(state, key, NEWS_COOLDOWN_H):
            continue
        alerts.append({"type": "news", "level": "critical" if critical else "info", "keywords": kws, **it})
    return alerts


def check_prices(state: dict, universe: list[dict]) -> list[dict]:
    alerts = []
    for row in market.market_rows([c["id"] for c in universe]):
        ch = row["change_1h_pct"]
        if ch is None:
            continue
        threshold = config.PRICE_ALERT_1H_TOP20 if (row["rank"] or 999) <= 20 else config.PRICE_ALERT_1H_OTHERS
        if abs(ch) < threshold:
            continue
        if _on_cooldown(state, f"price:{row['symbol']}:{'up' if ch > 0 else 'down'}", config.PRICE_ALERT_COOLDOWN_H):
            continue
        alerts.append({"type": "price", **row})
    return alerts


def format_alert(a: dict) -> str:
    e = telegram.escape
    if a["type"] == "price":
        arrow = "🚀" if a["change_1h_pct"] > 0 else "📉"
        return (f"{arrow} <b>{e(a['symbol'])}</b> {a['change_1h_pct']:+.1f}% en 1h "
                f"(24h : {a['change_24h_pct'] or 0:+.1f}%) · {a['price_usd']} $")
    icon = "🔴" if a["level"] == "critical" else "🟡"
    coins = f" · <b>{e(', '.join(a['coins'][:5]))}</b>" if a["coins"] else ""
    return (f"{icon} <b>{e(a['keywords'][0].upper())}</b>{coins}\n"
            f"{e(a['title'])}\n"
            f"<i>{e(a['source'])}</i> · <a href=\"{e(a['link'])}\">lire</a>")


def fire_routine(alerts: list[dict]) -> None:
    """Étape 2 (optionnelle) : demande à une routine Claude d'analyser les alertes critiques."""
    if not (config.ROUTINE_FIRE_URL and config.ROUTINE_FIRE_TOKEN):
        return
    critical = [a for a in alerts if a.get("level") == "critical"]
    if not critical:
        return
    text = "\n\n".join(f"{a['title']}\n{a['source']} {a['link']}\ncryptos: {', '.join(a['coins'])}" for a in critical)
    post_json(config.ROUTINE_FIRE_URL, {"text": text}, headers={
        "Authorization": f"Bearer {config.ROUTINE_FIRE_TOKEN}",
        "anthropic-beta": "experimental-cc-routine-2026-04-01",
        "anthropic-version": "2023-06-01",
    })


def run_once(state: dict, loop_index: int, dry: bool) -> None:
    universe = get_universe()
    alerts = check_news(state, universe)
    if loop_index % PRICE_EVERY_N_LOOPS == 0:
        alerts += check_prices(state, universe)
    save_state(state)
    if not alerts:
        return
    msg = "🛡️ <b>Heimdall — Alerte</b>\n\n" + "\n\n".join(format_alert(a) for a in alerts)
    if dry:
        print(msg)
        return
    telegram.send(msg)
    fire_routine(alerts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--ping", action="store_true", help="envoie un message de test et s'arrête")
    args = ap.parse_args()
    if args.ping:
        n = len(get_universe())
        telegram.send(f"🛡️ <b>Heimdall — veille opérationnelle</b>\n"
                      f"Je surveille {n} cryptos et les news toutes les 5 minutes.")
        return
    state = load_state()
    loop_index = 0
    while True:
        try:
            run_once(state, loop_index, args.dry)
        except Exception:
            if args.once:
                raise  # passage unique (GitHub Actions) : l'erreur doit se voir (croix rouge)
            traceback.print_exc()  # en continu : une source en panne ne doit pas arrêter la veille
        if args.once:
            break
        loop_index += 1
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
