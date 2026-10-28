# Premium Promo Bot

Telegram promo bot: force join, channel add, promo link, make post, analyze, clone bot, leaderboard, admin panel.

## 1. Config (promo_bot.py ke upar)
- `BOT_TOKEN`      -> BotFather token
- `ADMIN_IDS`      -> apni Telegram user id
- `FORCE_CHANNELS` -> jo channels har user ke link me auto-add rahenge (bot ko in me admin banao)

## 2. GitHub
Repo **PRIVATE** rakho (token code me hai). Saari files upload karo.

## 3. Render (Web Service)
1. render.com -> New -> Blueprint (render.yaml auto detect) ya New -> Web Service
2. Build Command : `pip install -r requirements.txt`
3. Start Command : `python promo_bot.py`
4. Deploy. Logs me `Started @yourbot` dikhna chahiye.

## Note
Render free plan ka disk temporary hai: redeploy/restart par `promo.db` reset ho jata hai
(users, channels, clones). Permanent data ke liye paid Persistent Disk (mount path /data) lo
aur code me `DB_PATH = "/data/promo.db"` kar do.

## Local run
    pip install -r requirements.txt
    python promo_bot.py
