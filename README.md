# 🛡️ Heimdall — veille temps réel

Toutes les 5 minutes (GitHub Actions), Heimdall lit les news crypto, SEC et Fed, surveille les variations de prix
des 100 principales cryptos supportées par Tangem, et envoie une alerte Telegram quand un événement important survient.

Aucun secret dans ce dépôt : le token Telegram et la clé CoinGecko sont dans les Secrets GitHub
(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `COINGECKO_API_KEY`).

Réglages (sources, mots-clés, seuils) : `heimdall/config.py`.
Test local : `python -m heimdall.watcher --once --dry`
