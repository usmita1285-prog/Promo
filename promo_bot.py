# -*- coding: utf-8 -*-
"""
PREMIUM PROMO BOT  -  single file, full working
------------------------------------------------
Features
  * Coloured buttons (blue = primary, green = success, red = danger)  [Bot API 9.4+]
  * /start card with user photo, name, username, chat id, mini bio
  * Force-join channels + Verify button
  * Add unlimited channels (bot admin + user admin check)
  * Make Post (copy any message to one / all of your channels)
  * Analyze channel, Delete channel, My Channels
  * Personal promo link  ->  t.me/<bot>?start=p_<random_id>
    anyone who opens it must join ALL channels of the link owner
  * Clone Bot (user gives a bot token -> same bot runs on his token)
  * Leaderboard, Profile, Admin panel, Broadcast, owner notifications

Install :  pip install aiohttp
Run     :  python promo_bot.py
"""

import asyncio
import html
import logging
import os
import re
import secrets
import signal
import sqlite3
import tempfile
import time
from datetime import datetime

import aiohttp

# =====================  CONFIG  =====================
BOT_TOKEN = os.getenv("BOT_TOKEN") or "9135082560:AAEKmT17drYWbePrqEfGVvpa3sa93HNo0qM"   # <-- yahin token paste karo
ADMIN_IDS = [8030963146]                 # your telegram user id(s)
# Ye channels HAR user ke link me automatically add rahenge (force join jaisa).
# Public channel  : "@name"  ya  "https://t.me/name"
# Private channel : {"title": "VIP", "chat": -1001234567890, "url": "https://t.me/+AbCdEfGh"}
# Bot ko in channels me admin banao (warna membership check nahi ho paata aur user ko block nahi kiya jata).
FORCE_CHANNELS = [
    "https://t.me/+evLFh-9-khE4MzQ1",
    # "https://t.me/your_second_channel",
    # {"title": "VIP", "chat": -1001234567890, "url": "https://t.me/+AbCdEfGh"},
]
BRAND = "PROMO BOT"
DB_PATH = os.getenv("DB_PATH") or "promo.db"
MAX_CLONES_PER_USER = 3
# ====================================================

# ---- premium icon set (unicode) ----
E = {
    # status
    "ok": "✓", "no": "\u2718", "warn": "\u26A0", "arrow": "\u2192", "bullet": "\u2022",
    "tri": "\u25B8", "diamond": "\u25C6", "star": "\u2605", "spark": "\u2726",
    "back": "↲", "fwd": "\u25B6", "plus": "\u2295", "minus": "\u2296",
    "rec": "\u25C9", "rec_off": "\u25CB",
    # dividers / borders
    "div": "\u2501" * 16, "div_eq": "\u2550" * 16, "div_dash": "\u2508" * 16,
    "block_on": "\u25A0", "block_off": "\u25A1",
    "border_top": "\u2550" * 16, "border_mid": "\u2501" * 16, "border_bot": "\u2550" * 16,
    # process state
    "play": "‣", "stop": "\u25A0", "pause": "\u2759\u2759", "refresh": "\u21BB",
    "running": "\u25B6", "stopped": "■", "restarting": "\u21BB", "stop_bot": "■",
    # security / access
    "lock": "\u25A3", "unlock": "\u25A2", "secure": "\u25C8", "key": "\u2756",
    "shield": "\u25C7", "ban": "\u2694", "trash": "\u2716", "eye": "\u25C9",
    # people
    "user": "\u25C8", "users": "\u25CE", "crown": "\u2654",
    # money / commerce
    "wallet": "\u25C6", "premium": "⌬", "lifetime": "\u2736", "gift": "\u2726",
    "ticket": "\u273F", "trophy": "\u2605",
    # data / analytics
    "graph": "\u25AA", "stats": "\u25AA", "chart_up": "\u25B2", "plan": "\u25A4",
    # comms
    "broadcast": "⚑", "chat": "\u25AB",
    # storage / files
    "folder": "\u25B8", "upload": "\u25B4", "download": "\u25BE", "cloud": "\u2601",
    # tools / time / energy
    "settings": "⚙", "cog": "\u2699", "bolt": "\u26A1", "clock": "\u23F1",
}

# Optional: premium custom-emoji ids for buttons (works only if bot owner has Telegram Premium)
# Example: {"add": "5368324170671202286"}   -> leave empty if unsure
BTN_EMOJI_ID = {}

P, G, R = "primary", "success", "danger"   # blue / green / red

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("promo")

# ---------------- database ----------------
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY, first_name TEXT, username TEXT,
  ref_code TEXT UNIQUE, referred_by INTEGER, verified INTEGER DEFAULT 0, joined_at INTEGER);
CREATE TABLE IF NOT EXISTS channels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, chat_id INTEGER, title TEXT,
  username TEXT, invite TEXT, added_at INTEGER, UNIQUE(owner_id, chat_id));
CREATE TABLE IF NOT EXISTS clones(
  token TEXT PRIMARY KEY, owner_id INTEGER, bot_username TEXT, created INTEGER);
CREATE TABLE IF NOT EXISTS promo_joins(
  owner_id INTEGER, user_id INTEGER, ts INTEGER, UNIQUE(owner_id, user_id));
CREATE TABLE IF NOT EXISTS seen(bot_id INTEGER, user_id INTEGER, UNIQUE(bot_id, user_id));
CREATE TABLE IF NOT EXISTS bot_chats(
  bot_id INTEGER, chat_id INTEGER, title TEXT, username TEXT, added_by INTEGER, ts INTEGER,
  UNIQUE(bot_id, chat_id));
"""
_db.executescript(SCHEMA)
_db.commit()


def q(sql, args=()):
    cur = _db.execute(sql, args)
    _db.commit()
    return cur.fetchall()


# ---------------- helpers ----------------
def esc(s):
    return html.escape(str(s or ""))


def fullname(u):
    return " ".join(x for x in [u.get("first_name"), u.get("last_name")] if x) or "User"


def B(text, cb=None, url=None, style=None, copy=None, emoji=None):
    b = {"text": text}
    if cb:
        b["callback_data"] = cb
    if url:
        b["url"] = url
    if copy:
        b["copy_text"] = {"text": copy}
    if style:
        b["style"] = style
    if emoji and BTN_EMOJI_ID.get(emoji):
        b["icon_custom_emoji_id"] = BTN_EMOJI_ID[emoji]
    return b


def KB(*rows):
    return {"inline_keyboard": [list(r) for r in rows]}


STATE = {}     # user_id -> dict(mode=...)
PENDING = {}   # user_id -> owner id of promo link used
TASKS = set()
MAIN = None    # main Bot object


class Bot:
    def __init__(self, token, session):
        self.token = token
        self.s = session
        self.url = f"https://api.telegram.org/bot{token}/"
        self.id = None
        self.username = None

    async def api(self, method, **p):
        p = {k: v for k, v in p.items() if v is not None}
        try:
            async with self.s.post(self.url + method, json=p,
                                   timeout=aiohttp.ClientTimeout(total=75)) as r:
                d = await r.json()
        except Exception as e:
            log.warning("%s failed: %s", method, e)
            return None
        if not d.get("ok"):
            log.info("%s -> %s", method, d.get("description"))
            return None
        return d["result"]

    async def send(self, chat_id, text, markup=None):
        return await self.api("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                              reply_markup=markup, link_preview_options={"is_disabled": True})


async def _upload(self, method, fields, file_field, filename, data):
    fd = aiohttp.FormData()
    for k, v in fields.items():
        fd.add_field(k, str(v))
    fd.add_field(file_field, data, filename=filename)
    try:
        async with self.s.post(self.url + method, data=fd, timeout=aiohttp.ClientTimeout(total=120)) as r:
            d = await r.json()
    except Exception as e:
        log.warning("%s upload failed: %s", method, e)
        return None
    if not d.get("ok"):
        log.info("%s -> %s", method, d.get("description"))
        return None
    return d["result"]


Bot.upload = _upload


async def show(bot, msg, text, markup):
    """Edit the message the user clicked; if impossible, resend."""
    cid, mid = msg["chat"]["id"], msg["message_id"]
    if "photo" in msg:
        r = await bot.api("editMessageCaption", chat_id=cid, message_id=mid, caption=text,
                          parse_mode="HTML", reply_markup=markup)
    else:
        r = await bot.api("editMessageText", chat_id=cid, message_id=mid, text=text,
                          parse_mode="HTML", reply_markup=markup,
                          link_preview_options={"is_disabled": True})
    if r is None:
        await bot.api("deleteMessage", chat_id=cid, message_id=mid)
        await bot.send(cid, text, markup)


# ---------------- data helpers ----------------
def upsert_user(bot, u):
    uid = u["id"]
    q("INSERT OR IGNORE INTO users(user_id,first_name,username,ref_code,joined_at) VALUES(?,?,?,?,?)",
      (uid, fullname(u), u.get("username"), secrets.token_hex(4), int(time.time())))
    q("UPDATE users SET first_name=?, username=? WHERE user_id=?", (fullname(u), u.get("username"), uid))
    q("INSERT OR IGNORE INTO seen VALUES(?,?)", (bot.id, uid))


def get_user(uid):
    r = q("SELECT * FROM users WHERE user_id=?", (uid,))
    return r[0] if r else None


def user_channels(uid):
    return q("SELECT * FROM channels WHERE owner_id=? ORDER BY id", (uid,))


def promo_count(uid):
    return q("SELECT COUNT(*) c FROM promo_joins WHERE owner_id=?", (uid,))[0]["c"]


def link_for(bot, uid):
    return f"https://t.me/{bot.username}?start=p_{get_user(uid)['ref_code']}"


def force_list():
    """Normalise FORCE_CHANNELS -> list of {chat, url, title}.
    chat=None means: private invite link without chat id (join button shown, membership cannot be checked)."""
    out = []
    for c in FORCE_CHANNELS:
        if isinstance(c, dict):
            chat = c.get("chat")
            url = c.get("url") or (f"https://t.me/{str(chat).lstrip('@')}" if isinstance(chat, str) else None)
            out.append({"chat": chat, "url": url, "title": c.get("title") or str(chat)})
        elif isinstance(c, str):
            c = c.strip()
            if "t.me/+" in c or "joinchat" in c:
                out.append({"chat": None, "url": c, "title": "Private Channel"})
                continue
            m = re.search(r"(?:t\.me/|@)([A-Za-z0-9_]{4,})/?$", c)
            if m:
                out.append({"chat": "@" + m.group(1), "url": f"https://t.me/{m.group(1)}", "title": "@" + m.group(1)})
            else:
                log.error("FORCE_CHANNELS entry samajh nahi aayi: %s", c)
    return out


def required(owner):
    items = force_list()
    seen = {str(i["chat"]).lower() for i in items}
    if owner:
        for r in user_channels(owner):
            keys = {str(r["chat_id"]), ("@" + r["username"]).lower() if r["username"] else ""}
            if keys & seen:
                continue
            items.append({"chat": r["chat_id"], "url": r["invite"], "title": r["title"]})
    return items


async def is_member(bot, chat, uid):
    res = await bot.api("getChatMember", chat_id=chat, user_id=uid)
    if res is None and MAIN is not None and MAIN is not bot:
        res = await MAIN.api("getChatMember", chat_id=chat, user_id=uid)
    if res is None:
        log.warning("Membership check fail for %s -> bot us channel me admin nahi hai? user ko pass kar diya", chat)
        return True  # cannot verify (bot not admin) -> do not block user
    if res["status"] == "restricted":
        return bool(res.get("is_member"))
    return res["status"] not in ("left", "kicked")


async def process_gate(bot, uid, owner, u, final=False):
    """Returns (items, miss). items = ALL required channels with 'joined' flag."""
    items = []
    for c in required(owner):
        if c["chat"] is None:          # private link without chat id -> cannot check
            joined = final
        else:
            joined = await is_member(bot, c["chat"], uid)
        items.append({**c, "joined": joined})
    miss = [c for c in items if not c["joined"]]
    if miss:
        q("UPDATE users SET verified=0 WHERE user_id=?", (uid,))
        return items, miss
    q("UPDATE users SET verified=1 WHERE user_id=?", (uid,))
    if owner and owner != uid:
        exists = q("SELECT 1 FROM promo_joins WHERE owner_id=? AND user_id=?", (owner, uid))
        if not exists:
            q("INSERT OR IGNORE INTO promo_joins VALUES(?,?,?)", (owner, uid, int(time.time())))
            await bot.send(owner, f"{E['spark']} <b>New promo join!</b>\n"
                                  f"{E['user']} {esc(fullname(u))} ne tumhara link use karke sab channels join kiye.\n"
                                  f"{E['star']} Total joins: <b>{promo_count(owner)}</b>")
    return items, miss


# ---------------- texts & keyboards ----------------
def profile_text(u, bio=None):
    row = get_user(u["id"])
    since = datetime.fromtimestamp(row["joined_at"]).strftime("%d %b %Y")
    mini = bio or f"{fullname(u)} is a promoter on {BRAND} since {since}."
    un = f"@{u['username']}" if u.get("username") else "N/A"
    return (
        f"{E['border_top']}\n"
        f"<blockquote><b>{E['spark']} {BRAND} {E['spark']}</b>\n{E['crown']} Premium Channel Promotion System {E['crown']}</blockquote>\n"
        f"{E['border_mid']}\n"
        f"{E['user']} <b>Name:</b> {esc(fullname(u))}\n"
        f"{E['users']} <b>Username:</b> {esc(un)}\n"
        f"{E['rec']} <b>Chat ID:</b> <code>{u['id']}</code>\n"
        f"{E['diamond']} <b>Channels:</b> {len(user_channels(u['id']))}\n"
        f"{E['wallet']} <b>Promo Joins:</b> {promo_count(u['id'])}\n"
        f"{E['stats']} <b>Next goal:</b> {bar(promo_count(u['id']), ((promo_count(u['id']) // 10) + 1) * 10)} "
        f"{promo_count(u['id'])}/{((promo_count(u['id']) // 10) + 1) * 10}\n"
        f"{E['div_dash']}\n"
        f"{E['spark']} <b>Mini Bio</b>\n<blockquote>{esc(mini)}</blockquote>\n"
        f"{E['border_bot']}"
    )


def menu_markup(uid):
    rows = [
        [B(f"{E['plus']} Add Channel", "add", style=G, emoji="add"),
         B(f"{E['play']} Make Post", "post", style=P, emoji="post")],
        [B(f"{E['graph']} Analyze Channel", "an", style=P),
         B(f"{E['trash']} Delete Channel", "dl", style=R)],
        [B(f"{E['plan']} My Channels", "my", style=P),
         B(f"{E['key']} My Promo Link", "link", style=G)],
        [B(f"{E['trophy']} Leaderboard", "lb", style=P),
         B(f"{E['user']} Profile", "prof", style=P)],
        [B(f"{E['cloud']} Clone Bot", "clone", style=G)],
    ]
    if uid in ADMIN_IDS:
        rows.append([B(f"{E['settings']} Admin Panel", "adm", style=R)])
    return KB(*rows)


def gate_markup(items):
    rows = []
    for c in items:
        if not c.get("url"):
            continue
        if c["joined"]:
            rows.append([B(f"{E['ok']} {c['title']} - Joined", url=c["url"], style=G)])
        else:
            rows.append([B(f"{E['plus']} Join {c['title']}", url=c["url"], style=P)])
    rows.append([B(f"{E['ok']} Verify", "verify", style=G)])
    return KB(*rows)


def bar(n, goal):
    """Progress bar using block icons."""
    goal = max(goal, 1)
    on = min(10, int(10 * n / goal))
    return E["block_on"] * on + E["block_off"] * (10 - on)


def back_row(text="Back", cb="menu"):
    icon = E["stop"] if text == "Cancel" else E["back"]
    return [B(f"{icon} {text}", cb, style=R)]


MENU_TEXT = (f"{E['div_eq']}\n<blockquote><b>{E['diamond']} MAIN MENU</b></blockquote>\n{E['div_eq']}\n"
             f"{E['cog']} Neeche se option choose karo {E['arrow']}")


async def send_card(bot, chat_id, u, items, miss):
    bio = None
    chat = await bot.api("getChat", chat_id=u["id"])
    if chat:
        bio = chat.get("bio")
    text = profile_text(u, bio)
    if miss:
        text += (f"\n\n{E['warn']} <b>Bot use karne ke liye neeche ke {len(items)} channels join karo</b> "
                 f"({len(miss)} baaki), phir <b>Verify</b> dabao.")
        markup = gate_markup(items)
    else:
        markup = menu_markup(u["id"])
    photos = await bot.api("getUserProfilePhotos", user_id=u["id"], limit=1)
    if photos and photos.get("total_count"):
        fid = photos["photos"][0][-1]["file_id"]
        r = await bot.api("sendPhoto", chat_id=chat_id, photo=fid, caption=text,
                          parse_mode="HTML", reply_markup=markup)
        if r:
            return
    await bot.send(chat_id, text, markup)


def link_card(bot, uid):
    chs = user_channels(uid)
    u = get_user(uid)
    lst = "\n".join(f"{E['tri']} {esc(c['title'])}" for c in chs) or "-"
    auto = "\n".join(f"{E['lifetime']} {esc(c['title'])}" for c in force_list())
    if auto:
        lst += f"\n\n{E['key']} <b>Auto-added (sponsor) channels:</b>\n{auto}"
    link = link_for(bot, uid)
    text = (f"{E['border_top']}\n<blockquote><b>{E['spark']} YOUR PROMO LINK</b></blockquote>\n{E['border_mid']}\n"
            f"{E['secure']} <b>Bot:</b> @{bot.username}\n"
            f"{E['rec']} <b>Chat ID:</b> <code>{uid}</code>\n"
            f"{E['lifetime']} <b>Random ID:</b> <code>{u['ref_code']}</code>\n"
            f"{E['diamond']} <b>Channels ({len(chs)}):</b>\n{lst}\n\n"
            f"{E['div_dash']}\n{E['key']} <b>Link:</b>\n<code>{link}</code>\n{E['gift']} Jitne log join karenge utna leaderboard me upar!\n\n"
            f"{E['shield']} Jo bhi is link se start karega use ye sab channels join karne padenge.\n{E['border_bot']}")
    markup = KB(
        [B(f"{E['download']} Copy Link", copy=link, style=G)],
        [B(f"{E['upload']} Share Link", url=f"https://t.me/share/url?url={link}&text=Join%20all%20channels%20and%20get%20premium%20promo", style=P)],
        back_row(),
    )
    return text, markup


# ---------------- message handlers ----------------
async def on_message(bot, m):
    if m["chat"]["type"] != "private" or "from" not in m:
        return
    u = m["from"]
    uid = u["id"]
    cid = m["chat"]["id"]
    upsert_user(bot, u)
    text = m.get("text") or ""

    fo = m.get("forward_origin")
    if uid in ADMIN_IDS and not STATE.get(uid) and fo and fo.get("type") == "channel":
        ch = fo["chat"]
        await bot.send(cid, f"{E['rec']} <b>{esc(ch.get('title'))}</b>\n{E['key']} Chat ID: <code>{ch['id']}</code>\n\n"
                            f"{E['tri']} FORCE_CHANNELS me aise daalo:\n"
                            f"<code>{{\"title\": \"{esc(ch.get('title'))}\", \"chat\": {ch['id']}, \"url\": \"INVITE_LINK\"}}</code>")
        return

    if text.startswith("/start"):
        STATE.pop(uid, None)
        payload = text.split(maxsplit=1)[1] if len(text.split()) > 1 else ""
        owner = None
        if payload.startswith("p_"):
            r = q("SELECT user_id FROM users WHERE ref_code=?", (payload[2:],))
            if r:
                owner = r[0]["user_id"]
                if owner != uid:
                    q("UPDATE users SET referred_by=? WHERE user_id=?", (owner, uid))
            else:
                await bot.send(cid, f"{E['warn']} Ye promo link purana/invalid hai, isliye sirf main channels dikh rahe hain.")
        PENDING[uid] = owner
        items, miss = await process_gate(bot, uid, owner, u)
        await send_card(bot, cid, u, items, miss)
        return

    row = get_user(uid)
    if not row or not row["verified"]:
        await bot.send(cid, f"{E['lock']} Pehle /start karke channels join + verify karo.")
        return

    if text == "/menu":
        STATE.pop(uid, None)
        await bot.send(cid, MENU_TEXT, menu_markup(uid))
        return
    if text == "/admin" and uid in ADMIN_IDS:
        await admin_panel(bot, cid)
        return

    st = STATE.get(uid)
    if not st:
        await bot.send(cid, f"{E['diamond']} <b>Menu ke liye</b> /menu {E['arrow']} dabao.", menu_markup(uid))
        return

    mode = st["mode"]
    if mode == "add":
        await add_channel(bot, uid, cid, m)
    elif mode == "invite":
        await save_invite(bot, uid, cid, m, st)
    elif mode == "post":
        await do_post(bot, uid, cid, m, st)
    elif mode == "clone":
        await do_clone(bot, uid, cid, m)
    elif mode == "broadcast" and uid in ADMIN_IDS:
        await do_broadcast(bot, cid, m)


def candidates(bot, uid):
    """Channels where this user just made the bot admin (auto-detected) and not yet added."""
    have = {r["chat_id"] for r in user_channels(uid)}
    rows = q("SELECT * FROM bot_chats WHERE bot_id=? AND added_by=? ORDER BY ts DESC", (bot.id, uid))
    return [r for r in rows if r["chat_id"] not in have]


def add_kb(bot, uid):
    rows = [[B(f"{E['plus']} Add: {c['title']}", f"ac:{c['chat_id']}", style=G)] for c in candidates(bot, uid)]
    rows.append([B(f"{E['ok']} Done - Get Link", "add_done", style=G)])
    rows.append(back_row("Cancel", "cancel"))
    return KB(*rows)


async def register_channel(bot, uid, ident):
    chat = await bot.api("getChat", chat_id=ident)
    if not chat or chat["type"] not in ("channel", "supergroup"):
        return {"ok": False, "text": f"{E['no']} Channel nahi mila. Bot ko pehle channel me <b>admin</b> banao."}
    me = await bot.api("getChatMember", chat_id=chat["id"], user_id=bot.id)
    if not me or me["status"] != "administrator":
        return {"ok": False, "text": f"{E['warn']} Bot is channel me admin nahi hai. Admin banao (invite-link permission ke sath) aur dobara try karo."}
    um = await bot.api("getChatMember", chat_id=chat["id"], user_id=uid)
    if not um or um["status"] not in ("creator", "administrator"):
        return {"ok": False, "text": f"{E['ban']} Tum is channel ke admin nahi ho."}
    if q("SELECT 1 FROM channels WHERE owner_id=? AND chat_id=?", (uid, chat["id"])):
        return {"ok": False, "text": f"{E['warn']} Ye channel pehle se add hai."}
    username = chat.get("username")
    invite = f"https://t.me/{username}" if username else None
    if not invite:   # private channel -> create a join link (does NOT revoke the old one)
        r = await bot.api("createChatInviteLink", chat_id=chat["id"], name="Promo Bot")
        invite = (r or {}).get("invite_link")
    if not invite:
        invite = await bot.api("exportChatInviteLink", chat_id=chat["id"])
    q("INSERT INTO channels(owner_id,chat_id,title,username,invite,added_at) VALUES(?,?,?,?,?,?)",
      (uid, chat["id"], chat.get("title"), username, invite, int(time.time())))
    row = q("SELECT id FROM channels WHERE owner_id=? AND chat_id=?", (uid, chat["id"]))[0]
    return {"ok": True, "title": chat.get("title"), "id": row["id"], "need_invite": not invite}


async def finish_add(bot, uid, cid, res, msg=None):
    if not res["ok"]:
        STATE[uid] = {"mode": "add"}
        text, mk = res["text"], add_kb(bot, uid)
    elif res["need_invite"]:
        STATE[uid] = {"mode": "invite", "id": res["id"]}
        text = (f"{E['ok']} <b>Added:</b> {esc(res['title'])}\n\n"
                f"{E['lock']} Ye <b>private</b> channel hai aur bot join-link nahi bana paya.\n"
                f"{E['key']} Is channel ka <b>invite link</b> (<code>https://t.me/+xxxx</code>) yahan bhejo.")
        mk = KB(back_row("Cancel", "cancel"))
    else:
        STATE[uid] = {"mode": "add"}
        n = len(user_channels(uid))
        text = (f"{E['ok']} <b>Added:</b> {esc(res['title'])}\n{E['diamond']} Total channels: <b>{n}</b>\n\n"
                f"{E['plus']} Aur channel add karo ya <b>Done</b> dabao.")
        mk = add_kb(bot, uid)
    if msg:
        await show(bot, msg, text, mk)
    else:
        await bot.send(cid, text, mk)


async def add_channel(bot, uid, cid, m):
    ident = None
    fo = m.get("forward_origin")
    t = (m.get("text") or "").strip()
    if fo and fo.get("type") == "channel":
        ident = fo["chat"]["id"]
    elif m.get("forward_from_chat"):
        ident = m["forward_from_chat"]["id"]
    elif "t.me/+" in t or "joinchat" in t:
        await bot.send(cid, f"{E['warn']} Private invite link se channel pehchana nahi jata.\n"
                            f"{E['tri']} Bot ko channel me <b>Admin</b> banao - channel neeche button me aa jayega,\n"
                            f"{E['tri']} ya us channel ka koi <b>post forward</b> karo.", add_kb(bot, uid))
        return
    else:
        mt = re.search(r"(?:t\.me/|@)([A-Za-z0-9_]{4,})", t)
        if t.lstrip("-").isdigit():
            ident = int(t)
        elif mt:
            ident = "@" + mt.group(1)
    if ident is None:
        await bot.send(cid, f"{E['no']} Invalid. <code>@channel</code> bhejo, post forward karo, ya bot ko admin banao.", add_kb(bot, uid))
        return
    res = await register_channel(bot, uid, ident)
    await finish_add(bot, uid, cid, res)


async def save_invite(bot, uid, cid, m, st):
    t = (m.get("text") or "").strip()
    mt = re.search(r"https?://t\.me/(?:\+|joinchat/)[A-Za-z0-9_-]+", t)
    if not mt:
        await bot.send(cid, f"{E['no']} Sahi invite link bhejo: <code>https://t.me/+xxxx</code>", KB(back_row("Cancel", "cancel")))
        return
    q("UPDATE channels SET invite=? WHERE id=? AND owner_id=?", (mt.group(0), st["id"], uid))
    STATE[uid] = {"mode": "add"}
    await bot.send(cid, f"{E['ok']} Invite link save ho gaya.\n{E['plus']} Aur channel add karo ya <b>Done</b> dabao.", add_kb(bot, uid))


async def on_my_chat_member(bot, mc):
    """Auto-detect when the bot is made admin in a channel (works for private channels too)."""
    chat, new, frm = mc["chat"], mc["new_chat_member"], mc["from"]
    if chat["type"] not in ("channel", "supergroup"):
        return
    if new["status"] == "administrator":
        q("INSERT OR REPLACE INTO bot_chats VALUES(?,?,?,?,?,?)",
          (bot.id, chat["id"], chat.get("title"), chat.get("username"), frm["id"], int(time.time())))
        if not q("SELECT 1 FROM channels WHERE owner_id=? AND chat_id=?", (frm["id"], chat["id"])):
            await bot.send(frm["id"], f"{E['ok']} Bot <b>{esc(chat.get('title'))}</b> me admin ban gaya.\n"
                                      f"{E['plus']} Is channel ko apni list me add karna hai?",
                           KB([B(f"{E['plus']} Add this channel", f"ac:{chat['id']}", style=G)]))
    else:
        q("DELETE FROM bot_chats WHERE bot_id=? AND chat_id=?", (bot.id, chat["id"]))


async def do_post(bot, uid, cid, m, st):
    target = st["target"]
    chs = user_channels(uid) if target == "all" else q(
        "SELECT * FROM channels WHERE owner_id=? AND id=?", (uid, int(target)))
    ok = bad = 0
    for c in chs:
        r = await bot.api("copyMessage", chat_id=c["chat_id"], from_chat_id=cid, message_id=m["message_id"])
        ok, bad = (ok + 1, bad) if r else (ok, bad + 1)
        await asyncio.sleep(0.3)
    STATE.pop(uid, None)
    await bot.send(cid, f"<blockquote><b>{E['broadcast']} POST REPORT</b></blockquote>\n"
                        f"{E['ok']} Sent: <b>{ok}</b>\n{E['no']} Failed: <b>{bad}</b>",
                   KB([B(f"{E['refresh']} Post Again", "post", style=G)], back_row()))


async def do_clone(bot, uid, cid, m):
    tok = (m.get("text") or "").strip()
    await bot.api("deleteMessage", chat_id=cid, message_id=m["message_id"])  # hide token
    if not re.match(r"^\d{6,}:[A-Za-z0-9_-]{30,}$", tok):
        await bot.send(cid, f"{E['no']} Token format galat hai. @BotFather se token lo aur dobara bhejo.",
                       KB(back_row("Cancel", "cancel")))
        return
    if q("SELECT 1 FROM clones WHERE token=?", (tok,)) or tok == BOT_TOKEN:
        await bot.send(cid, f"{E['warn']} Ye bot already clone/run ho raha hai.", KB(back_row()))
        return
    if q("SELECT COUNT(*) c FROM clones WHERE owner_id=?", (uid,))[0]["c"] >= MAX_CLONES_PER_USER and uid not in ADMIN_IDS:
        await bot.send(cid, f"{E['ban']} Max {MAX_CLONES_PER_USER} clones allowed.", KB(back_row()))
        return
    nb = Bot(tok, bot.s)
    me = await nb.api("getMe")
    if not me:
        await bot.send(cid, f"{E['stop_bot']} Token invalid hai.", KB(back_row("Cancel", "cancel")))
        return
    q("INSERT INTO clones VALUES(?,?,?,?)", (tok, uid, me["username"], int(time.time())))
    spawn(run_bot(nb))
    STATE.pop(uid, None)
    await bot.send(cid, f"<blockquote><b>{E['cloud']} CLONE SUCCESS</b></blockquote>\n"
                        f"{E['restarting']} Bot start ho gaya.\n{E['running']} <b>Status: Running</b>\n"
                        f"{E['ok']} Tumhara bot live hai: @{esc(me['username'])}\n"
                        f"{E['bolt']} Us bot me <code>/start</code> karke use karo - features bilkul same hain.",
                   KB([B(f"{E['fwd']} Open @{me['username']}", url=f"https://t.me/{me['username']}", style=P)], back_row()))


async def do_broadcast(bot, cid, m):
    STATE.pop(cid, None)
    users = q("SELECT user_id FROM seen WHERE bot_id=?", (bot.id,))
    ok = 0
    for r in users:
        if await bot.api("copyMessage", chat_id=r["user_id"], from_chat_id=cid, message_id=m["message_id"]):
            ok += 1
        await asyncio.sleep(0.05)
    await bot.send(cid, f"{E['ok']} Broadcast done: <b>{ok}/{len(users)}</b>", KB(back_row()))


async def admin_panel(bot, cid, msg=None):
    t = (f"{E['border_top']}\n<blockquote><b>{E['settings']} ADMIN PANEL</b></blockquote>\n{E['border_mid']}\n"
         f"{E['running']} Bot status: <b>Running</b> {E['bolt']}\n"
         f"{E['pause']} Broadcast: idle\n"
         f"{E['users']} Users: <b>{q('SELECT COUNT(*) c FROM users')[0]['c']}</b>\n"
         f"{E['diamond']} Channels: <b>{q('SELECT COUNT(*) c FROM channels')[0]['c']}</b>\n"
         f"{E['premium']} Clones: <b>{q('SELECT COUNT(*) c FROM clones')[0]['c']}</b>\n"
         f"{E['star']} Promo joins: <b>{q('SELECT COUNT(*) c FROM promo_joins')[0]['c']}</b>\n{E['border_bot']}")
    kb = KB([B(f"{E['broadcast']} Broadcast", "bc", style=G), B(f"{E['refresh']} Refresh", "adm", style=P)],
            [B(f"{E['download']} Backup Now", "bk", style=G)], back_row())
    if msg:
        await show(bot, msg, t, kb)
    else:
        await bot.send(cid, t, kb)


# ---------------- callbacks ----------------
def channel_list_kb(prefix, rows, style=P, extra=None):
    kb = [[B(f"{E['tri']} {r['title']}", f"{prefix}:{r['id']}", style=style)] for r in rows]
    if extra:
        kb.append(extra)
    kb.append(back_row())
    return KB(*kb)


async def on_callback(bot, cb):
    u = cb["from"]
    uid = u["id"]
    msg = cb.get("message")
    data = cb.get("data", "")
    if not msg:
        return
    cid = msg["chat"]["id"]
    upsert_user(bot, u)

    async def ans(text=None, alert=False):
        await bot.api("answerCallbackQuery", callback_query_id=cb["id"], text=text, show_alert=alert)

    # ---- verify ----
    if data == "verify":
        owner = PENDING.get(uid, get_user(uid)["referred_by"])
        items, miss = await process_gate(bot, uid, owner, u, final=True)
        if miss:
            await ans(f"{E['warn']} Abhi {len(miss)} channel join karna baaki hai! Sab join karo.", True)
            await bot.api("editMessageReplyMarkup", chat_id=cid, message_id=msg["message_id"],
                          reply_markup=gate_markup(items))
            return
        await ans(f"{E['ok']} Verified!")
        await show(bot, msg, profile_text(u) + f"\n\n{E['unlock']} {E['ok']} <b>Verified!</b>", menu_markup(uid))
        return

    row = get_user(uid)
    if not row or not row["verified"]:
        await ans(f"{E['lock']} Pehle /start karke verify karo.", True)
        return
    await ans()

    if data == "menu":
        STATE.pop(uid, None)
        await show(bot, msg, MENU_TEXT, menu_markup(uid))

    elif data == "cancel":
        STATE.pop(uid, None)
        await show(bot, msg, MENU_TEXT, menu_markup(uid))

    elif data == "prof":
        await show(bot, msg, profile_text(u), KB([B(f"{E['refresh']} Refresh", "prof", style=P)], back_row()))

    elif data == "add":
        STATE[uid] = {"mode": "add"}
        await show(bot, msg,
                   f"<blockquote><b>{E['plus']} ADD CHANNEL</b></blockquote>\n"
                   f"{E['folder']} 1. Bot ko apne channel me <b>Admin</b> banao (public ya private)\n"
                   f"{E['folder']} 2. Channel neeche <b>green button</b> me aa jayega - bas dabao\n"
                   f"{E['folder']} Ya <code>@username</code> bhejo / channel ka koi post <b>forward</b> karo\n"
                   f"{E['folder']} Jitne chahe channels add karo, phir <b>Done</b> dabao.",
                   add_kb(bot, uid))

    elif data.startswith("ac:"):
        res = await register_channel(bot, uid, int(data[3:]))
        await finish_add(bot, uid, cid, res, msg)

    elif data == "add_done":
        STATE.pop(uid, None)
        if not user_channels(uid):
            await show(bot, msg, f"{E['warn']} Abhi tak koi channel add nahi hua.", menu_markup(uid))
            return
        text, mk = link_card(bot, uid)
        await show(bot, msg, text, mk)

    elif data == "link":
        if not user_channels(uid):
            await show(bot, msg, f"{E['warn']} Pehle kam se kam 1 channel add karo.", menu_markup(uid))
            return
        text, mk = link_card(bot, uid)
        await show(bot, msg, text, mk)

    elif data == "my":
        chs = user_channels(uid)
        lst = "\n".join(f"{E['folder']} <b>{esc(c['title'])}</b> {('@' + c['username']) if c['username'] else '(private)'}" for c in chs) or f"{E['rec_off']} Koi channel nahi."
        auto = "\n".join(f"{E['lifetime']} {esc(c['title'])}" for c in force_list())
        if auto:
            lst += f"\n\n{E['key']} <b>Auto-added (sponsor):</b>\n{auto}"
        await show(bot, msg, f"<blockquote><b>{E['plan']} MY CHANNELS ({len(chs)})</b></blockquote>\n{E['div']}\n{lst}", KB(back_row()))

    elif data == "post":
        chs = user_channels(uid)
        if not chs:
            await show(bot, msg, f"{E['warn']} Pehle channel add karo.", menu_markup(uid))
            return
        await show(bot, msg, f"<blockquote><b>{E['broadcast']} MAKE POST</b></blockquote>\n{E['fwd']} Kahan post karna hai?",
                   channel_list_kb("pt", chs, extra=[B(f"{E['star']} All Channels", "pt:all", style=G)]))

    elif data.startswith("pt:"):
        STATE[uid] = {"mode": "post", "target": data[3:]}
        await show(bot, msg, f"{E['fwd']} Ab wo <b>post/message</b> bhejo (text, photo, video, kuch bhi).",
                   KB(back_row("Cancel", "cancel")))

    elif data == "an":
        chs = user_channels(uid)
        if not chs:
            await show(bot, msg, f"{E['warn']} Pehle channel add karo.", menu_markup(uid))
            return
        await show(bot, msg, f"<blockquote><b>{E['graph']} ANALYZE</b></blockquote>\n{E['fwd']} Channel choose karo:",
                   channel_list_kb("an", chs))

    elif data.startswith("an:"):
        r = q("SELECT * FROM channels WHERE owner_id=? AND id=?", (uid, int(data[3:])))
        if not r:
            return
        c = r[0]
        chat = await bot.api("getChat", chat_id=c["chat_id"])
        cnt = await bot.api("getChatMemberCount", chat_id=c["chat_id"])
        me = await bot.api("getChatMember", chat_id=c["chat_id"], user_id=bot.id)
        admin = me and me["status"] == "administrator"
        desc = (chat or {}).get("description") or "-"
        t = (f"<blockquote><b>{E['chart_up']} {esc(c['title'])}</b></blockquote>\n"
             f"{E['eye']} Members: <b>{cnt if cnt is not None else 'N/A'}</b>\n"
             f"{E['users']} Username: {('@' + c['username']) if c['username'] else 'Private'}\n"
             f"{E['rec']} Chat ID: <code>{c['chat_id']}</code>\n"
             f"{E['shield']} Bot admin: {E['running'] + ' Yes' if admin else E['stopped'] + ' No'}\n"
             f"{E['star']} Promo joins (your link): <b>{promo_count(uid)}</b>\n"
             f"{E['stats']} Growth: {bar(promo_count(uid), ((promo_count(uid) // 10) + 1) * 10)}\n"
             f"{E['clock']} Added: {datetime.fromtimestamp(c['added_at']).strftime('%d %b %Y')}\n"
             f"{E['chat']} About: {esc(desc[:200])}")
        await show(bot, msg, t, KB([B(f"{E['refresh']} Refresh", data, style=P)], back_row("Back", "an")))

    elif data == "dl":
        chs = user_channels(uid)
        if not chs:
            await show(bot, msg, f"{E['warn']} Koi channel nahi hai.", menu_markup(uid))
            return
        await show(bot, msg, f"<blockquote><b>{E['trash']} DELETE CHANNEL</b></blockquote>\n{E['warn']} Kaun sa hatana hai?",
                   channel_list_kb("dl", chs, style=R))

    elif data.startswith("dl:"):
        r = q("SELECT * FROM channels WHERE owner_id=? AND id=?", (uid, int(data[3:])))
        if r:
            await show(bot, msg, f"{E['warn']} <b>{esc(r[0]['title'])}</b> delete karna hai?",
                       KB([B(f"{E['trash']} Yes, Delete", f"dly:{r[0]['id']}", style=R),
                           B(f"{E['minus']} No", "menu", style=G)]))

    elif data.startswith("dly:"):
        q("DELETE FROM channels WHERE owner_id=? AND id=?", (uid, int(data[4:])))
        await show(bot, msg, f"{E['ok']} Channel delete ho gaya.", menu_markup(uid))

    elif data == "lb":
        rows = q("""SELECT p.owner_id, COUNT(*) c, u.first_name FROM promo_joins p
                    JOIN users u ON u.user_id=p.owner_id GROUP BY p.owner_id ORDER BY c DESC LIMIT 10""")
        medals = ["\u2654", "\u2605", "\u2726"]
        lst = "\n".join(f"{medals[i] if i < 3 else E['bullet']} <b>{esc(r['first_name'])}</b> {E['arrow']} {r['c']}"
                        for i, r in enumerate(rows)) or "Abhi koi data nahi."
        await show(bot, msg, f"<blockquote><b>{E['ticket']} {E['trophy']} TOP PROMOTERS</b></blockquote>\n{E['div']}\n{lst}", KB(back_row()))

    elif data == "clone":
        STATE[uid] = {"mode": "clone"}
        await show(bot, msg,
                   f"<blockquote><b>{E['cloud']} CLONE BOT</b></blockquote>\n"
                   f"{E['tri']} @BotFather se naya bot banao\n"
                   f"{E['tri']} Uska <b>token</b> yahan bhejo\n"
                   f"{E['tri']} Tumhara bot bilkul isi jaisa ban jayega.\n\n"
                   f"{E['lock']} Token message turant delete kar diya jayega.",
                   KB(back_row("Cancel", "cancel")))

    elif data == "adm" and uid in ADMIN_IDS:
        await admin_panel(bot, cid, msg)

    elif data == "bk" and uid in ADMIN_IDS:
        ok = await backup_db(MAIN, force=True)
        await bot.send(cid, f"{E['ok']} Backup tumhari chat me bhej diya." if ok else f"{E['warn']} Backup nahi ho paya (logs dekho).")

    elif data == "bc" and uid in ADMIN_IDS:
        STATE[uid] = {"mode": "broadcast"}
        await show(bot, msg, f"{E['broadcast']} Broadcast message bhejo (kuch bhi).", KB(back_row("Cancel", "cancel")))


# ---------------- runner ----------------
def spawn(coro):
    t = asyncio.create_task(coro)
    TASKS.add(t)
    t.add_done_callback(TASKS.discard)


async def safe(bot, u):
    try:
        if "message" in u:
            await on_message(bot, u["message"])
        elif "callback_query" in u:
            await on_callback(bot, u["callback_query"])
        elif "my_chat_member" in u:
            await on_my_chat_member(bot, u["my_chat_member"])
    except Exception:
        log.exception("handler error")


async def run_bot(bot):
    me = await bot.api("getMe")
    if not me:
        log.error("Invalid token: %s...", bot.token[:10])
        return
    bot.id, bot.username = me["id"], me["username"]
    log.info("Started @%s", bot.username)
    await bot.api("deleteWebhook")
    await bot.api("setMyCommands", commands=[
        {"command": "start", "description": "Start the bot"},
        {"command": "menu", "description": "Open main menu"}])
    offset = 0
    while True:
        ups = await bot.api("getUpdates", offset=offset, timeout=30,
                            allowed_updates=["message", "callback_query", "my_chat_member"])
        if ups is None:
            await asyncio.sleep(3)
            continue
        for u in ups:
            offset = u["update_id"] + 1
            spawn(safe(bot, u))


# ---------- DB backup/restore via Telegram (Render free disk is temporary) ----------
BK = {"changes": -1, "msg": None, "ok": True}
BK_NAME = "promo_backup.db"


def _users_count():
    return q("SELECT COUNT(*) c FROM users")[0]["c"]


async def backup_db(bot, force=False):
    """Send DB file to the first admin's private chat and pin it."""
    if not ADMIN_IDS or not BK["ok"] or _users_count() == 0:
        return False
    if not force and _db.total_changes == BK["changes"]:
        return False
    tmp = os.path.join(tempfile.gettempdir(), BK_NAME)
    if os.path.exists(tmp):
        os.remove(tmp)
    dst = sqlite3.connect(tmp)
    _db.backup(dst)
    dst.close()
    with open(tmp, "rb") as f:
        data = f.read()
    aid = ADMIN_IDS[0]
    r = await bot.upload("sendDocument", {"chat_id": aid, "disable_notification": "true",
                                          "caption": f"{BRAND} database backup (auto) - delete mat karna"},
                         "document", BK_NAME, data)
    if not r:
        return False
    await bot.api("pinChatMessage", chat_id=aid, message_id=r["message_id"], disable_notification=True)
    if BK["msg"]:
        await bot.api("deleteMessage", chat_id=aid, message_id=BK["msg"])
    BK["msg"] = r["message_id"]
    BK["changes"] = _db.total_changes
    log.info("DB backup sent (%s bytes)", len(data))
    return True


async def restore_db(bot):
    """If DB is empty (fresh Render disk) restore it from the pinned backup in admin chat."""
    global _db
    if _users_count() > 0 or not ADMIN_IDS:
        BK["changes"] = _db.total_changes
        return
    for attempt in range(3):
        try:
            chat = await bot.api("getChat", chat_id=ADMIN_IDS[0])
            pm = (chat or {}).get("pinned_message")
            doc = (pm or {}).get("document")
            if not doc or doc.get("file_name") != BK_NAME:
                if chat is not None:
                    log.info("Koi backup nahi mila - fresh start")
                    BK["changes"] = _db.total_changes
                    return
                raise RuntimeError("getChat failed")
            f = await bot.api("getFile", file_id=doc["file_id"])
            async with bot.s.get(f"https://api.telegram.org/file/bot{bot.token}/{f['file_path']}") as r:
                data = await r.read()
            tmp = os.path.join(tempfile.gettempdir(), "restore.db")
            with open(tmp, "wb") as fh:
                fh.write(data)
            src = sqlite3.connect(tmp)
            src.backup(_db)
            src.close()
            _db.executescript(SCHEMA)
            _db.commit()
            BK["msg"] = pm["message_id"]
            BK["changes"] = _db.total_changes
            log.info("DB restored from Telegram backup: %s users", _users_count())
            return
        except Exception as e:
            log.warning("restore try %s failed: %s", attempt + 1, e)
            await asyncio.sleep(3)
    BK["ok"] = False   # never overwrite a good backup with an empty DB
    log.error("Restore fail - backups pause kiye gaye taaki purana backup safe rahe")


async def backup_loop():
    await asyncio.sleep(60)
    while True:
        try:
            await backup_db(MAIN)
        except Exception:
            log.exception("backup error")
        await asyncio.sleep(240)


async def web_server():
    """Tiny web server so Render 'Web Service' sees an open port (health check)."""
    from aiohttp import web

    async def hello(_):
        return web.Response(text="Promo bot is running")

    app = web.Application()
    app.add_routes([web.get("/", hello), web.get("/health", hello)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Web server on port %s", port)


async def keep_alive(s):
    """Self-ping so the free Render instance does not fall asleep."""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        await asyncio.sleep(600)
        try:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)):
                pass
        except Exception:
            pass


async def main():
    global MAIN
    if "PASTE_YOUR" in BOT_TOKEN:
        raise SystemExit("BOT_TOKEN set nahi hai! promo_bot.py me token paste karo.")
    await web_server()
    async with aiohttp.ClientSession() as s:
        MAIN = Bot(BOT_TOKEN, s)
        await restore_db(MAIN)
        spawn(run_bot(MAIN))
        spawn(keep_alive(s))
        spawn(backup_loop())
        for r in q("SELECT token FROM clones"):
            spawn(run_bot(Bot(r["token"], s)))
        stop = asyncio.Event()
        try:
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
        except Exception:
            pass
        await stop.wait()
        try:
            await backup_db(MAIN)   # last backup before Render stops the instance
        except Exception:
            log.exception("final backup error")


if __name__ == "__main__":
    asyncio.run(main())
