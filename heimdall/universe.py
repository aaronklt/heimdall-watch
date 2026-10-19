"""Univers suivi : top N des cryptos supportées par Tangem, hors stablecoins et actifs adossés."""
import json
import re
import time

from heimdall import config
from heimdall.http import get_json
from heimdall.market import coins_markets

TANGEM_API = "https://api.tangem.org/v1/coins"
STABLE_FILE = config.DATA_DIR / "universe.json"
CACHE_TTL_S = 20 * 3600  # un peu moins de 24 h : la routine de 8 h doit toujours recalculer
KEEP_RANK = 120          # un membre reste tant qu'il est dans le top 120 des cryptos éligibles
ENTER_RANK = 90          # un nouveau n'entre que s'il est dans le top 90
MAX_MISSING_DAYS = 3     # absent des réponses API 3 jours de suite : il sort


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


def _load_previous() -> list[dict]:
    """Univers de la veille, versionné dans le dépôt (la routine démarre chaque jour dans un conteneur vide)."""
    if STABLE_FILE.exists():
        try:
            return json.loads(STABLE_FILE.read_text(encoding="utf-8")).get("coins", [])
        except Exception as e:
            print(f"[universe] {STABLE_FILE.name} illisible : {e}")
    return []


def _entry(c: dict, missing: int = 0) -> dict:
    return {"id": c["id"], "symbol": c["symbol"].upper(), "name": c["name"], "rank": c.get("market_cap_rank"),
            "missing_days": missing}


def build_universe(size: int = config.UNIVERSE_SIZE) -> tuple[list[dict], list[str]]:
    """Univers stable : on garde les membres de la veille tant qu'ils restent raisonnablement bien classés.

    Règles (hystérésis, pour que la liste ne change pas tous les jours au gré des API) :
    - un membre reste tant que son rang reste dans le top KEEP_RANK, ou s'il manque dans les réponses
      depuis moins de MAX_MISSING_DAYS jours (panne ou réponse partielle d'une API) ;
    - un nouveau n'entre que s'il est dans le top ENTER_RANK ;
    - le filtre « prix immobile » (stablecoins non catégorisés) ne s'applique qu'aux nouveaux :
      une crypto calme comme BNB, TRX ou LEO ne doit pas sortir parce que la semaine a été tranquille.
    """
    excluded = _excluded_ids()
    raw = []
    for page in (1, 2):
        raw += coins_markets(page=page)
        time.sleep(8)
    previous = {c["id"]: c for c in _load_previous()}
    candidates = [c for c in raw if c["id"] not in excluded and not _excluded_by_name(c)
                  and (c["id"] in previous or not _looks_pegged(c))]
    supported = _tangem_supported([c["id"] for c in candidates])
    ranked = [c for c in candidates if c["id"] in supported]
    if previous and sum(c["id"] in previous for c in ranked) < 0.7 * len(previous):
        print("[universe] réponse Tangem ou CoinGecko anormalement courte : univers de la veille conservé")
        return list(previous.values()), ["univers de la veille conservé (réponse API incomplète)"]

    pos = {c["id"]: i + 1 for i, c in enumerate(ranked)}  # rang parmi les cryptos éligibles
    log, keep = [], []
    for cid, p in previous.items():
        if cid in pos:
            if pos[cid] <= KEEP_RANK:
                keep.append(_entry(next(c for c in ranked if c["id"] == cid)))
            else:
                log.append(f"sortie {p['symbol']} : rang éligible {pos[cid]} > {KEEP_RANK}")
        elif p.get("missing_days", 0) + 1 < MAX_MISSING_DAYS:
            keep.append({**p, "missing_days": p.get("missing_days", 0) + 1})
            log.append(f"{p['symbol']} absent des réponses API, conservé ({p.get('missing_days', 0) + 1}/{MAX_MISSING_DAYS})")
        else:
            log.append(f"sortie {p['symbol']} : absent des réponses API depuis {MAX_MISSING_DAYS} jours")
    kept = {c["id"] for c in keep}
    threshold = ENTER_RANK if previous else size
    for c in ranked:
        if pos[c["id"]] > threshold:
            break
        if c["id"] not in kept:
            keep.append(_entry(c))
            kept.add(c["id"])
            if previous:
                log.append(f"entrée {c['symbol'].upper()} : rang éligible {pos[c['id']]}")
    # Encore de la place (beaucoup de sorties) : on complète dans l'ordre du classement.
    for c in ranked:
        if len(keep) >= size:
            break
        if c["id"] not in kept:
            keep.append(_entry(c))
            kept.add(c["id"])
            log.append(f"entrée {c['symbol'].upper()} : complément, rang éligible {pos[c['id']]}")
    # Trop de membres (les gardés dépassent la taille) : on retire les moins bien classés.
    keep.sort(key=lambda c: pos.get(c["id"], c.get("rank") or 10**6))
    for c in keep[size:]:
        log.append(f"sortie {c['symbol']} : place prise par une crypto mieux classée")
    return keep[:size], log


def get_universe(force: bool = False) -> list[dict]:
    """Univers recalculé au plus une fois par 24 h, puis enregistré dans data/universe.json (versionné)."""
    if not force and STABLE_FILE.exists():
        cached = json.loads(STABLE_FILE.read_text(encoding="utf-8"))
        if time.time() - cached.get("built_at", 0) < CACHE_TTL_S:
            return cached["coins"]
    universe, log = build_universe()
    STABLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STABLE_FILE.write_text(json.dumps({"built_at": time.time(), "changes": log, "coins": universe},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
    for line in log:
        print(f"[universe] {line}")
    return universe


if __name__ == "__main__":
    u = get_universe(force=True)
    print(f"{len(u)} cryptos suivies :")
    print(", ".join(c["symbol"] for c in u))
