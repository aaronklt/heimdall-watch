"""Veille temps réel : mots-clés dans les news + variations brutales de prix -> alerte Telegram.

Usage :
  python -m heimdall.watcher          # tourne en continu (sur le serveur)
  python -m heimdall.watcher --once   # un seul passage (test)
  python -m heimdall.watcher --dry    # affiche les alertes sans les envoyer
"""
import argparse
import copy
import hashlib
import json
import re
import time
import traceback
from datetime import datetime, timezone

from heimdall import config, market, news, telegram
from heimdall.http import post_json
from heimdall.universe import get_universe

STATE_FILE = config.STATE_DIR / "watcher.json"
# Historique de prix : hors dépôt (cache GitHub Actions), sinon un commit de 30 Ko toutes les 5 minutes.
PRICES_FILE = config.DATA_DIR / "prices" / "history.json"
LOOP_SECONDS = 300
PRICE_EVERY_N_LOOPS = 2  # prix toutes les 10 min : reste dans le quota gratuit CoinGecko
NEWS_COOLDOWN_H = 3

KEYWORD_PATTERNS = {
    level: [(kw, re.compile(rf"\b{re.escape(kw)}\b")) for kw in kws]
    for level, kws in config.ALERT_KEYWORDS.items()
}


def load_state() -> dict:
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if PRICES_FILE.exists():
            try:
                state["prices"] = json.loads(PRICES_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return state
    # Premier lancement : on ignore l'historique pour ne pas envoyer 200 vieilles alertes.
    return {"last_news_ts": time.time() - 3600, "seen_links": [], "cooldowns": {}}


def _ensure(state: dict) -> dict:
    """Champs ajoutés le 2026-10-19 : historique de prix, journal des alertes, statistiques de santé."""
    state.setdefault("prices", {})        # id -> [[ts, prix], ...] sur ~2 h
    state.setdefault("sent", [])          # [[ts, clé], ...] : alertes réellement envoyées
    state.setdefault("health", {})        # date -> compteurs du jour
    state.setdefault("source_fail", {})   # source -> échecs consécutifs
    return state


def _health(state: dict) -> dict:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    h = state["health"].setdefault(day, {"runs": 0, "alerts": 0, "price_checks": 0, "price_refreshes": 0,
                                         "errors": 0})
    for old in sorted(state["health"])[:-8]:
        del state["health"][old]
    return h


def save_state(state: dict) -> None:
    state["seen_links"] = state["seen_links"][-1000:]
    now = time.time()
    state["cooldowns"] = {k: v for k, v in state["cooldowns"].items() if v > now}
    state["sent"] = [s for s in state.get("sent", []) if s[0] > now - 8 * 86400]
    prices = state.pop("prices", {})
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    PRICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRICES_FILE.write_text(json.dumps(prices), encoding="utf-8")
    state["prices"] = prices


def _on_cooldown(state: dict, key: str, hours: float) -> bool:
    now = time.time()
    if state["cooldowns"].get(key, 0) > now:
        return True
    state["cooldowns"][key] = now + hours * 3600
    return False


def check_news(state: dict, universe: list[dict]) -> list[dict]:
    items, failed = news.fetch_news(since_ts=state["last_news_ts"] - 3 * 3600, with_failures=True)
    for src in news.config.RSS_FEEDS:
        state["source_fail"][src] = state["source_fail"].get(src, 0) + 1 if src in failed else 0
    items = news.tag_coins(items, universe)
    alerts = []
    for it in items:
        if it["link"] in state["seen_links"]:
            continue
        state["seen_links"].append(it["link"])
        # Une date dans le futur (fuseau mal déclaré par un flux) ne doit pas faire sauter les news suivantes.
        state["last_news_ts"] = max(state["last_news_ts"], min(it["published_ts"], time.time()))
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
        # Sans crypto identifiée, la clé inclut le titre : deux piratages différents dans les 3 h doivent alerter tous les deux.
        subject = ",".join(it["coins"][:3]) or hashlib.sha1(it["title"].encode()).hexdigest()[:8]
        key = f"news:{kws[0]}:{subject}"
        if _on_cooldown(state, key, NEWS_COOLDOWN_H):
            continue
        alerts.append({"type": "news", "level": "critical" if critical else "info", "keywords": kws, "_key": key, **it})
    return alerts


def _own_change_1h(state: dict, cid: str, price: float, now: float) -> float | None:
    """Variation sur ~1 h calculée avec nos propres relevés (toutes les 5 à 10 min).

    La variation 1 h fournie par CoinGecko ne semble se mettre à jour qu'une fois par heure (99 alertes prix
    sur 100 tombaient dans les 5 premières minutes de l'heure) : nos relevés détectent plus tôt.
    """
    hist = [p for p in state["prices"].get(cid, []) if p[0] > now - 2 * 3600]
    hist.append([now, price])
    state["prices"][cid] = hist
    old = [p for p in hist if 50 * 60 <= now - p[0] <= 75 * 60]
    if not old or not old[0][1]:
        return None
    return round((price / old[0][1] - 1) * 100, 2)


def check_prices(state: dict, universe: list[dict]) -> list[dict]:
    alerts = []
    now = time.time()
    h = _health(state)
    h["price_checks"] += 1
    rows = market.market_rows([c["id"] for c in universe])
    btc = next((r for r in rows if r["id"] == "bitcoin"), None)
    if btc:  # diagnostic : le prix de BTC a-t-il changé depuis le relevé précédent ?
        last = state["prices"].get("bitcoin", [[0, None]])[-1][1]
        h["price_refreshes"] += int(btc["price_usd"] != last)
    for row in rows:
        if not row["price_usd"]:
            continue
        top20 = (row["rank"] or 999) <= 20
        own = _own_change_1h(state, row["id"], row["price_usd"], now)
        cg = row["change_1h_pct"]
        ch = max((x for x in (own, cg) if x is not None), key=abs, default=None)
        threshold = config.PRICE_ALERT_1H_TOP20 if top20 else config.PRICE_ALERT_1H_OTHERS
        if ch is not None and abs(ch) >= threshold:
            key = f"price:{row['symbol']}:{'up' if ch > 0 else 'down'}"
            if not _on_cooldown(state, key, config.PRICE_ALERT_COOLDOWN_H):
                alerts.append({"type": "price", "window": "1h", "_key": key, **row, "change_1h_pct": ch})
                continue
        ch24 = row["change_24h_pct"]
        threshold24 = config.PRICE_ALERT_24H_TOP20 if top20 else config.PRICE_ALERT_24H_OTHERS
        if ch24 is not None and abs(ch24) >= threshold24:
            key = f"price24:{row['symbol']}:{'up' if ch24 > 0 else 'down'}"
            if not _on_cooldown(state, key, config.PRICE_ALERT_24H_COOLDOWN_H):
                alerts.append({"type": "price", "window": "24h", "_key": key, **row})
    # On ne garde l'historique que des cryptos suivies.
    ids = {r["id"] for r in rows}
    state["prices"] = {k: v for k, v in state["prices"].items() if k in ids}
    return alerts


def format_alert(a: dict) -> str:
    e = telegram.escape
    if a["type"] == "price" and a.get("window") == "24h":
        arrow = "🚀" if a["change_24h_pct"] > 0 else "📉"
        return (f"{arrow} <b>{e(a['symbol'])}</b> {a['change_24h_pct']:+.1f} % en 24 heures "
                f"(1 heure : {a['change_1h_pct'] or 0:+.1f} %) · {a['price_usd']} $")
    if a["type"] == "price":
        arrow = "🚀" if a["change_1h_pct"] > 0 else "📉"
        return (f"{arrow} <b>{e(a['symbol'])}</b> {a['change_1h_pct']:+.1f} % en 1 heure "
                f"(24 heures : {a['change_24h_pct'] or 0:+.1f} %) · {a['price_usd']} $")
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


def heartbeat_due(state: dict) -> bool:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if now.hour != config.HEARTBEAT_HOUR_UTC or state.get("last_heartbeat") == today:
        return False
    state["last_heartbeat"] = today
    return True


def heartbeat_message(state: dict) -> str:
    """Bilan de santé des dernières 24 h : si ce message n'arrive pas un matin, la veille est en panne."""
    now = time.time()
    days = sorted(state["health"])[-2:]
    runs = sum(state["health"][d]["runs"] for d in days)
    errors = sum(state["health"][d]["errors"] for d in days)
    checks = sum(state["health"][d]["price_checks"] for d in days)
    refreshes = sum(state["health"][d]["price_refreshes"] for d in days)
    sent = [k for t, k in state["sent"] if t > now - 86400]
    down = [s for s, n in state["source_fail"].items() if n >= 6]
    lines = ["🛡️ <b>Heimdall — Veille des dernières 24 heures</b>",
             f"Passages : {runs} sur les 2 derniers jours (attendu : environ 288 par jour)",
             f"Alertes envoyées en 24 heures : {len(sent)} "
             f"(prix : {sum(k.startswith('price') for k in sent)}, news : {sum(k.startswith('news') for k in sent)})",
             f"Prix de BTC rafraîchi à {refreshes} relevés sur {checks}"]
    lines.append(f"⚠️ Sources en panne : {', '.join(down)}" if down else "Sources de news : toutes répondent")
    if errors:
        lines.append(f"⚠️ Passages en erreur : {errors}")
    return "\n".join(lines)


def run_once(state: dict, loop_index: int, dry: bool) -> None:
    _ensure(state)
    h = _health(state)
    h["runs"] += 1
    before = copy.deepcopy(state)
    universe = get_universe()
    alerts = check_news(state, universe)
    if loop_index % PRICE_EVERY_N_LOOPS == 0:
        alerts += check_prices(state, universe)
    messages = []
    if alerts:
        messages.append(("🛡️ <b>Heimdall — Alerte</b>\n\n" + "\n\n".join(format_alert(a) for a in alerts), False))
    if heartbeat_due(state):
        messages.append((heartbeat_message(state), True))
    if dry:
        for m, _ in messages:
            print(m)
        return
    try:
        for m, silent in messages:
            telegram.send(m, silent=silent)
    except Exception:
        # Envoi raté : on n'enregistre pas l'état, pour que le passage suivant redétecte et renvoie les alertes.
        before["health"] = state["health"]
        _health(before)["errors"] += 1
        save_state(before)
        raise
    now = time.time()
    state["sent"] += [[now, a.get("_key", "")] for a in alerts]
    _health(state)["alerts"] += len(alerts)
    save_state(state)
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
