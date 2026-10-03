import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
STATE_DIR = DATA_DIR / "state"
CACHE_DIR = DATA_DIR / "cache"
SNAPSHOT_DIR = DATA_DIR / "snapshot"
SCORES_DIR = DATA_DIR / "scores"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
ROUTINE_FIRE_URL = os.getenv("ROUTINE_FIRE_URL", "")
ROUTINE_FIRE_TOKEN = os.getenv("ROUTINE_FIRE_TOKEN", "")

USER_AGENT = "Mozilla/5.0 (Heimdall crypto watcher)"

UNIVERSE_SIZE = 100

# Catégories CoinGecko exclues : actifs dont le prix ne bouge pas ou qui dupliquent un autre actif.
# Peu de catégories : l'API gratuite limite le nombre d'appels par minute.
EXCLUDED_CATEGORIES = [
    "stablecoins",
    "tokenized-gold",
    "tokenized-money-market-fund-mmfs",
    "wrapped-tokens",
    "liquid-staking-tokens",
]
# Filet de sécurité par le nom pour ce que les catégories ratent.
EXCLUDED_NAME_PATTERN = r"(?i)\b(wrapped|staked|restaked|bridged|tokenized|treasury|money market|fund|gold|usd|eur)\b"
# Exclusions manuelles (ids CoinGecko) si un actif passe entre les mailles.
EXCLUDED_IDS: set[str] = set()

RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/feed",
    "SEC": "https://www.sec.gov/news/pressreleases.rss",
    "Fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "Reddit r/CryptoCurrency": "https://www.reddit.com/r/CryptoCurrency/.rss",
}

# Mots-clés d'alerte, en minuscules. "critical" déclenche une alerte même sans crypto identifiée.
ALERT_KEYWORDS = {
    "critical": [
        "hack", "hacked", "exploit", "drained", "stolen", "breach",
        "bankrupt", "bankruptcy", "insolvent", "insolvency", "withdrawals halted",
        "halts withdrawals", "depeg", "de-peg", "rug pull", "emergency",
    ],
    # Macro : seulement depuis les sources officielles (les médias en parlent tous les jours).
    "macro": ["rate cut", "rate hike", "fomc", "federal funds rate", "executive order", "crypto", "digital asset", "stablecoin"],
    "coin": [
        "sec sues", "lawsuit", "charges", "delist", "delisting", "listing",
        "etf approved", "etf approval", "etf rejected", "etf filing",
        "partnership", "mainnet", "upgrade", "hard fork", "token unlock",
        "outage", "network halted", "acquisition", "whale",
    ],
}

OFFICIAL_SOURCES = {"SEC", "Fed"}
# Une alerte critique sans crypto identifiée doit au moins parler de crypto.
CRYPTO_CONTEXT_WORDS = ["crypto", "defi", "protocol", "exchange", "bridge", "wallet", "token", "blockchain",
                        "stablecoin", "bitcoin", "ethereum", "on-chain", "dex", "lending"]

# Seuils de variation de prix sur 1h (%) pour alerter.
PRICE_ALERT_1H_TOP20 = 4.0
PRICE_ALERT_1H_OTHERS = 7.0
# Une même crypto ne déclenche pas deux alertes prix en moins de X heures.
PRICE_ALERT_COOLDOWN_H = 6
