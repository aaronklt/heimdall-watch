"""Données de marché : CoinGecko (prix, volumes) et indice Fear & Greed."""
from heimdall import config
from heimdall.http import get_json

CG = "https://api.coingecko.com/api/v3"


def _cg_headers() -> dict:
    return {"x-cg-demo-api-key": config.COINGECKO_API_KEY} if config.COINGECKO_API_KEY else {}


def coins_markets(page: int = 1, per_page: int = 250, ids: list[str] | None = None, category: str | None = None) -> list[dict]:
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": per_page,
        "page": page,
        "price_change_percentage": "1h,24h,7d,30d",
    }
    if ids:
        params["ids"] = ",".join(ids)
    if category:
        params["category"] = category
    return get_json(f"{CG}/coins/markets", params=params, headers=_cg_headers())


def market_rows(ids: list[str]) -> list[dict]:
    """Ligne de marché compacte par crypto, prête à être lue par un humain ou par Claude."""
    rows = []
    for c in coins_markets(ids=ids):
        rows.append({
            "id": c["id"],
            "symbol": c["symbol"].upper(),
            "name": c["name"],
            "rank": c.get("market_cap_rank"),
            "price_usd": c.get("current_price"),
            "market_cap_usd": c.get("market_cap"),
            "volume_24h_usd": c.get("total_volume"),
            "change_1h_pct": _r(c.get("price_change_percentage_1h_in_currency")),
            "change_24h_pct": _r(c.get("price_change_percentage_24h_in_currency")),
            "change_7d_pct": _r(c.get("price_change_percentage_7d_in_currency")),
            "change_30d_pct": _r(c.get("price_change_percentage_30d_in_currency")),
            "from_ath_pct": _r(c.get("ath_change_percentage")),
        })
    rows.sort(key=lambda r: r["rank"] or 10**6)
    return rows


def global_market() -> dict:
    d = get_json(f"{CG}/global", headers=_cg_headers())["data"]
    return {
        "total_market_cap_usd": d["total_market_cap"].get("usd"),
        "market_cap_change_24h_pct": _r(d.get("market_cap_change_percentage_24h_usd")),
        "btc_dominance_pct": _r(d["market_cap_percentage"].get("btc")),
        "eth_dominance_pct": _r(d["market_cap_percentage"].get("eth")),
    }


def fear_greed(days: int = 7) -> list[dict]:
    d = get_json("https://api.alternative.me/fng/", params={"limit": days})
    return [{"value": int(x["value"]), "label": x["value_classification"], "timestamp": int(x["timestamp"])} for x in d["data"]]


def _r(x):
    return round(x, 2) if isinstance(x, (int, float)) else None
