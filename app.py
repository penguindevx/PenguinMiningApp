from flask import Flask, render_template, jsonify, request, session
import hashlib
import hmac
import urllib.parse
import sqlite3
import psycopg
from psycopg.rows import dict_row
import time
import secrets
import requests
from pathlib import Path

app = Flask(__name__)

# -------------------------------------------------
# CONFIG
# -------------------------------------------------

app.secret_key = secrets.token_hex(32)

DB = "penguin.db"

ADMIN_ID = 1028007008

BNB_ADDRESS = "0x54990f6F781D81Fc36B76144dfF8637c337062de"

# Mining temel hızı
BASE_MINING_RATE = 0.00000579


import os
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

def get_telegram_user():
    init_data = request.headers.get("X-Telegram-Init-Data", "").strip()
    if not init_data:
        init_data = request.args.get("_tg_init_data", "").strip()

    user_data = validate_telegram_init_data(init_data)
    if not user_data:
        return None

    return user_data

def telegram_send_message(chat_id, text, reply_markup=None):
    if not BOT_TOKEN or not chat_id:
        return False

    try:
        payload = {
            "chat_id": int(chat_id),
            "text": text,
            "parse_mode": "HTML"
        }

        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=8
        )

        return response.ok
    except Exception as e:
        print("Telegram mesaj hatası:", e)
        return False


PUBLIC_APP_URL = "https://penguinminingapp.onrender.com"


def make_admin_action_token(order_id, action, timestamp):
    raw = f"{order_id}:{action}:{timestamp}".encode()
    return hmac.new(
        BOT_TOKEN.encode(),
        raw,
        hashlib.sha256
    ).hexdigest()


def verify_admin_action_token(order_id, action, timestamp, token):
    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - timestamp) > 86400:
        return False

    expected = make_admin_action_token(
        order_id,
        action,
        timestamp
    )

    return hmac.compare_digest(
        expected,
        str(token or "")
    )


def telegram_api(method, payload=None):
    if not BOT_TOKEN:
        print("BOT_TOKEN bulunamadı; Telegram API çağrısı yapılamadı.")
        return None

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload or {},
            timeout=8
        )

        if not response.ok:
            print(
                f"Telegram API hatası [{method}]: "
                f"{response.status_code} {response.text}"
            )
            return None

        return response.json()

    except Exception as e:
        print(f"Telegram API bağlantı hatası [{method}]:", e)
        return None


def telegram_answer_callback(callback_query_id, text="", show_alert=False):
    return telegram_api(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert
        }
    )


def telegram_edit_message(message, text, reply_markup=None):
    if not message:
        return None

    chat = message.get("chat") or {}
    message_id = message.get("message_id")

    if not chat.get("id") or not message_id:
        return None

    payload = {
        "chat_id": chat["id"],
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }

    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    return telegram_api("editMessageText", payload)


def telegram_remove_buttons(message):
    if not message:
        return None

    chat = message.get("chat") or {}
    message_id = message.get("message_id")

    if not chat.get("id") or not message_id:
        return None

    return telegram_api(
        "editMessageReplyMarkup",
        {
            "chat_id": chat["id"],
            "message_id": message_id,
            "reply_markup": {
                "inline_keyboard": []
            }
        }
    )


def notify_admin_new_order(order_id, username, package, txid):
    if not BOT_TOKEN:
        print("BOT_TOKEN bulunamadı; admin bildirimi gönderilemedi.")
        return

    text = (
        "🐧 <b>YENİ VIP SİPARİŞİ</b>\n\n"
        f"👤 Kullanıcı: <b>{username}</b>\n"
        f"💎 Paket: <b>{package['name']}</b>\n"
        f"💰 Fiyat: <b>{package['price_bnb']} BNB</b>\n"
        f"⚡ Mining: <b>{package['multiplier']}x</b>\n"
        f"🧾 TXID: <code>{txid}</code>\n"
        f"🆔 Sipariş: <b>#{order_id}</b>"
    )

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ ONAYLA",
                    "callback_data": f"order:approve:{order_id}"
                },
                {
                    "text": "❌ REDDET",
                    "callback_data": f"order:reject:{order_id}"
                }
            ]
        ]
    }

    telegram_send_message(
        ADMIN_ID,
        text,
        keyboard
    )



def notify_admin_new_withdrawal(withdrawal_id, username, amount_penguin, amount_usdt, wallet_address):
    timestamp = int(time.time())

    approve_token = make_admin_action_token(
        withdrawal_id,
        "withdraw_approve",
        timestamp
    )
    reject_token = make_admin_action_token(
        withdrawal_id,
        "withdraw_reject",
        timestamp
    )

    approve_url = (
        f"{PUBLIC_APP_URL}/admin/withdraw-action"
        f"?withdrawal_id={withdrawal_id}"
        f"&action=approve"
        f"&ts={timestamp}"
        f"&token={approve_token}"
    )

    reject_url = (
        f"{PUBLIC_APP_URL}/admin/withdraw-action"
        f"?withdrawal_id={withdrawal_id}"
        f"&action=reject"
        f"&ts={timestamp}"
        f"&token={reject_token}"
    )

    text = (
        "🐧 <b>YENİ ÇEKİM TALEBİ</b>\\n\\n"
        f"👤 Kullanıcı: <b>{username}</b>\\n"
        f"💰 Miktar: <b>{amount_penguin:,.0f} PENGUIN</b>\\n"
        f"💵 Değer: <b>{amount_usdt:.2f} USDT</b>\\n"
        f"👛 Cüzdan: <code>{wallet_address}</code>\\n"
        f"🆔 Çekim: <b>#{withdrawal_id}</b>"
    )

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ ONAYLA", "url": approve_url},
            {"text": "❌ REDDET", "url": reject_url}
        ]]
    }

    telegram_send_message(
        ADMIN_ID,
        text,
        keyboard
    )

def get_request_username():
    tg_user = get_telegram_user()
    if tg_user and tg_user.get("id"):
        return tg_user.get("username") or ("tg_" + str(tg_user["id"]))

    # Telegram Mini App doğrulaması yoksa username fallback kullanma.
    # Aksi halde yanlış/yeni kullanıcı hesabı oluşup bakiye ve VIP
    # sıfırlanmış gibi görünebilir.
    return ""

def validate_telegram_init_data(init_data):
    if not init_data or not BOT_TOKEN:
        return None

    try:
        data = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data)
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        user_data = data.get("user")
        if not user_data:
            return None

        import json
        return json.loads(user_data)

    except Exception as e:
        print("Telegram initData doğrulama hatası:", e)
        return None


# -------------------------------------------------
# DATABASE
# -------------------------------------------------

class DBWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        if params is None:
            return self.conn.execute(sql)
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def insert_and_get_id(conn, sql, params=None):
    """Insert a row and return its generated id on both SQLite and PostgreSQL."""
    if isinstance(conn, DBWrapper):
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("Inserted row id alınamadı")
        return row["id"] if isinstance(row, dict) else row[0]
    cur = conn.execute(sql, params)
    return cur.lastrowid


def get_db():
    database_url = os.environ.get("DATABASE_URL")

    if database_url:
        conn = psycopg.connect(
            database_url,
            row_factory=dict_row
        )
        return DBWrapper(conn)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_postgres():
    conn = get_db()

    # Render/Supabase may start with an empty PostgreSQL database.
    # Create every table required by the application before seeding VIPs.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            balance DOUBLE PRECISION DEFAULT 0,
            mining INTEGER DEFAULT 0,
            mining_rate DOUBLE PRECISION DEFAULT 0.00000579,
            vip_name TEXT DEFAULT 'Free',
            vip_multiplier DOUBLE PRECISION DEFAULT 1,
            referrals INTEGER DEFAULT 0,
            referral_code TEXT UNIQUE,
            referred_by TEXT,
            last_update DOUBLE PRECISION,
            telegram_user_id BIGINT UNIQUE,
            pending_mining DOUBLE PRECISION DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vip_packages (
            id BIGSERIAL PRIMARY KEY,
            name TEXT UNIQUE,
            price_bnb DOUBLE PRECISION,
            multiplier DOUBLE PRECISION,
            duration_days INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id BIGSERIAL PRIMARY KEY,
            username TEXT,
            telegram_user_id BIGINT,
            package_id BIGINT,
            txid TEXT,
            receipt TEXT,
            status TEXT DEFAULT 'pending',
            created_at DOUBLE PRECISION
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id BIGSERIAL PRIMARY KEY,
            username TEXT,
            amount_penguin DOUBLE PRECISION,
            amount_usdt DOUBLE PRECISION,
            wallet_address TEXT,
            network TEXT DEFAULT 'BEP20',
            status TEXT DEFAULT 'pending',
            created_at DOUBLE PRECISION,
            processed_at DOUBLE PRECISION,
            admin_id BIGINT
        )
    """)

    packages = [
        ("Starter Miner", 0.15, 2, 0),
        ("Pro Miner", 0.30, 5, 0),
        ("Ultra Miner", 0.60, 10, 0),
        ("Legend Miner", 1.00, 20, 0),
        ("Elite Miner", 2.00, 35, 0),
        ("Master Miner", 4.00, 55, 0),
        ("Penguin King", 7.00, 80, 0),
        ("Penguin Emperor", 10.00, 110, 0),
        ("Penguin Titan", 15.00, 150, 0),
        ("Penguin Supreme", 20.00, 200, 0)
    ]

    for package in packages:
        conn.execute("""
            UPDATE vip_packages
            SET price_bnb = %s,
                multiplier = %s,
                duration_days = %s,
                active = 1
            WHERE name = %s
        """, (package[1], package[2], package[3], package[0]))

        conn.execute("""
            INSERT INTO vip_packages
            (name, price_bnb, multiplier, duration_days, active)
            SELECT %s, %s, %s, %s, 1
            WHERE NOT EXISTS (
                SELECT 1
                FROM vip_packages
                WHERE name = %s
            )
        """, (
            package[0],
            package[1],
            package[2],
            package[3],
            package[0]
        ))

    conn.execute(
        "UPDATE users SET mining_rate = %s WHERE vip_name = 'Free'",
        (BASE_MINING_RATE,)
    )

    conn.commit()
    conn.close()


def init_db():

    # PostgreSQL/Render: schema + VIP seed is handled by migrate_postgres().
    if os.environ.get("DATABASE_URL"):
        migrate_postgres()
        return

    conn = get_db()

    # USERS
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            balance REAL DEFAULT 0,
            mining INTEGER DEFAULT 0,
            mining_rate REAL DEFAULT 0.00001,
            vip_name TEXT DEFAULT 'Free',
            vip_multiplier REAL DEFAULT 1,
            referrals INTEGER DEFAULT 0,
            referral_code TEXT UNIQUE,
            referred_by TEXT,
            last_update REAL,
            telegram_user_id INTEGER,
            pending_mining REAL DEFAULT 0
        )
    """)

    # USERS migration
    columns = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "telegram_user_id" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN telegram_user_id INTEGER")
    if "pending_mining" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN pending_mining REAL DEFAULT 0")

    # VIP PACKAGES
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vip_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            price_bnb REAL,
            multiplier REAL,
            duration_days INTEGER,
            active INTEGER DEFAULT 1
        )
    """)

    # ORDERS
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            telegram_user_id INTEGER,
            package_id INTEGER,
            txid TEXT,
            receipt TEXT,
            status TEXT DEFAULT 'pending',
            created_at REAL
        )
    """)

    # WITHDRAWALS
    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            amount_penguin REAL,
            amount_usdt REAL,
            wallet_address TEXT,
            network TEXT DEFAULT 'BEP20',
            status TEXT DEFAULT 'pending',
            created_at REAL,
            processed_at REAL,
            admin_id INTEGER
        )
    """)

    # ORDERS migration
    order_columns = [row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()]
    if "telegram_user_id" not in order_columns:
        conn.execute("ALTER TABLE orders ADD COLUMN telegram_user_id INTEGER")

    # Default VIP packages
    packages = [
        ("Starter Miner", 0.15, 2, 0),
        ("Pro Miner", 0.30, 5, 0),
        ("Ultra Miner", 0.60, 10, 0),
        ("Legend Miner", 1.00, 20, 0),
        ("Elite Miner", 2.00, 35, 0),
        ("Master Miner", 4.00, 55, 0),
        ("Penguin King", 7.00, 80, 0),
        ("Penguin Emperor", 10.00, 110, 0),
        ("Penguin Titan", 15.00, 150, 0),
        ("Penguin Supreme", 20.00, 200, 0)
    ]

    for package in packages:

        try:

            conn.execute("""
                INSERT INTO vip_packages
                (name, price_bnb, multiplier, duration_days)
                VALUES (?, ?, ?, ?)
            """, package)

        except sqlite3.IntegrityError:
            pass

    # Update existing VIP packages to the current unlimited configuration.
    for package in packages:
        conn.execute("""
            UPDATE vip_packages
            SET price_bnb = ?, multiplier = ?, duration_days = ?
            WHERE name = ?
        """, (package[1], package[2], package[3], package[0]))

    # Ensure existing Free users use the current base mining rate
    conn.execute("UPDATE users SET mining_rate = ? WHERE vip_name = 'Free'", (BASE_MINING_RATE,))

    # Replace old default VIP packages with the current 0.13-15 BNB set
    # Existing VIP packages are preserved; INSERT OR IGNORE adds only missing packages.
    for package in packages:
        conn.execute("""
            INSERT OR IGNORE INTO vip_packages
            (name, price_bnb, multiplier, duration_days)
            VALUES (?, ?, ?, ?)
        """, package)

    conn.commit()
    conn.close()


# -------------------------------------------------
# USER
# -------------------------------------------------

def create_user(username, telegram_user_id=None):

    conn = get_db()

    user = None

    if telegram_user_id:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,)
        ).fetchone()

    if not user:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

    if not user:

        referral_code = secrets.token_hex(4).upper()

        now = time.time()

        conn.execute("""
            INSERT INTO users
            (
                username,
                balance,
                mining,
                mining_rate,
                vip_name,
                vip_multiplier,
                referrals,
                referral_code,
                last_update,
                telegram_user_id
            )
            VALUES (?, 0, 0, ?, 'Free', 1, 0, ?, ?, ?)
        """, (
            username,
            BASE_MINING_RATE,
            referral_code,
            now,
            telegram_user_id
        ))

        conn.commit()

    elif telegram_user_id and not user["telegram_user_id"]:
        conn.execute(
            "UPDATE users SET telegram_user_id = ? WHERE id = ?",
            (telegram_user_id, user["id"])
        )
        conn.commit()

    conn.close()


def get_user(username):
    tg_user = get_telegram_user()
    telegram_user_id = tg_user.get("id") if tg_user else None

    create_user(username, telegram_user_id)

    conn = get_db()

    if telegram_user_id:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,)
        ).fetchone()
    else:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

    conn.close()
    return user


# -------------------------------------------------
# MINING
# -------------------------------------------------

def update_mining(username):
    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    if not user:
        conn.close()
        return

    now = time.time()

    if user["mining"] == 1:
        last_update = user["last_update"] or now
        elapsed = max(0, now - last_update)

        earned = (
            elapsed
            * BASE_MINING_RATE
            * float(user["vip_multiplier"] or 1)
        )

        pending = float(user["pending_mining"] or 0) + earned

        conn.execute("""
            UPDATE users
            SET pending_mining = ?,
                last_update = ?,
                mining_rate = ?
            WHERE id = ?
        """, (
            pending,
            now,
            BASE_MINING_RATE * float(user["vip_multiplier"] or 1),
            user["id"]
        ))

    else:
        conn.execute("""
            UPDATE users
            SET last_update = ?
            WHERE id = ?
        """, (
            now,
            user["id"]
        ))

    conn.commit()
    conn.close()


# -------------------------------------------------
# HOME
# -------------------------------------------------

@app.route("/")
def home():

    return render_template("index.html")



# -------------------------------------------------
# TELEGRAM REFERRAL API
# -------------------------------------------------

BOT_REFERRAL_API_URL = "http://127.0.0.1:5001/api/referral"


def get_telegram_referral(telegram_user_id):
    if not telegram_user_id:
        return None

    try:
        key_path = Path.home() / "PenguinMiningApp" / ".app_referral_api_key"
        api_key = key_path.read_text().strip()

        if not api_key:
            return None

        response = requests.get(
            BOT_REFERRAL_API_URL,
            params={
                "user_id": telegram_user_id,
                "key": api_key
            },
            timeout=3
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if not data.get("success"):
            return None

        return data

    except Exception as e:
        print("❌ Telegram referral API hatası:", e)
        return None

# -------------------------------------------------
# USER API
# -------------------------------------------------

@app.route("/api/telegram-debug")
def telegram_debug():
    init_data = request.headers.get("X-Telegram-Init-Data", "").strip()
    user = validate_telegram_init_data(init_data)

    return jsonify({
        "header_received": bool(init_data),
        "telegram_valid": bool(user),
        "has_user": bool(user and user.get("id"))
    })


@app.route("/api/user")
def api_user():

    username = get_request_username()
    if not username:
        return jsonify({"success": False, "message": "Telegram kullanıcı doğrulaması gerekli."}), 401
    get_user(username)

    update_mining(username)

    user = get_user(username)

    telegram_referral = get_telegram_referral(
        user["telegram_user_id"]
    )

    referrals = user["referrals"]

    if telegram_referral:
        referrals = telegram_referral["referrals"]

    return jsonify({

        "username": user["username"],

        "balance": user["balance"],
        "pending_mining": user["pending_mining"],

        "mining": bool(user["mining"]),

        "mining_rate": user["mining_rate"],

        "vip_name": user["vip_name"],

        "vip_multiplier": user["vip_multiplier"],

        "referrals": referrals,

        "referral_code": user["referral_code"],

        "telegram_user_id": user["telegram_user_id"],

        "telegram_referrals": (
            telegram_referral["referrals"]
            if telegram_referral
            else 0
        )

    })


# -------------------------------------------------
# START MINING
# -------------------------------------------------

@app.route(
    "/api/mining/start",
    methods=["POST"]
)
def start_mining():

    username = get_request_username()
    if not username:
        return jsonify({"success": False, "message": "Telegram kullanıcı doğrulaması gerekli."}), 401
    get_user(username)

    update_mining(username)

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET mining = 1,
            last_update = ?
        WHERE username = ?
    """, (
        time.time(),
        username
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "message": "Mining başlatıldı"
    })


# -------------------------------------------------
# STOP MINING
# -------------------------------------------------

@app.route(
    "/api/mining/stop",
    methods=["POST"]
)
def stop_mining():

    username = get_request_username()
    if not username:
        return jsonify({"success": False, "message": "Telegram kullanıcı doğrulaması gerekli."}), 401
    get_user(username)

    update_mining(username)

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET mining = 0,
            last_update = ?
        WHERE username = ?
    """, (
        time.time(),
        username
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "message": "Mining durduruldu"
    })


# -------------------------------------------------
# CLAIM MINING
# -------------------------------------------------

@app.route("/api/mining/claim", methods=["POST"])
def claim_mining():

    username = get_request_username()

    if not username:
        return jsonify({
            "success": False,
            "message": "Telegram kullanıcı doğrulaması gerekli."
        }), 401

    update_mining(username)

    conn = get_db()

    user = conn.execute("""
        SELECT balance, pending_mining, mining
        FROM users
        WHERE username = ?
    """, (username,)).fetchone()

    if not user:
        conn.close()
        return jsonify({
            "success": False,
            "message": "Kullanıcı bulunamadı."
        }), 404

    pending = float(user["pending_mining"] or 0)
    balance = float(user["balance"] or 0)

    if pending <= 0:
        conn.close()
        return jsonify({
            "success": False,
            "message": "Claim edilecek birikmiş Penguin yok.",
            "balance": balance,
            "pending_mining": 0
        })

    new_balance = balance + pending

    conn.execute("""
        UPDATE users
        SET balance = ?,
            pending_mining = 0,
            last_update = ?
        WHERE username = ?
    """, (
        new_balance,
        time.time(),
        username
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "message": "Penguin başarıyla claim edildi.",
        "claimed": pending,
        "balance": new_balance,
        "pending_mining": 0,
        "mining": user["mining"]
    })


# -------------------------------------------------
# VIP PACKAGES
# -------------------------------------------------

@app.route("/api/vip")
def vip_packages():

    conn = get_db()

    packages = conn.execute("""
        SELECT *
        FROM vip_packages
        WHERE active = 1
        ORDER BY multiplier ASC
    """).fetchall()

    conn.close()

    return jsonify([
        {
            "id": p["id"],
            "name": p["name"],
            "price_bnb": p["price_bnb"],
            "multiplier": p["multiplier"],
            "duration_days": p["duration_days"]
        }
        for p in packages
    ])


# -------------------------------------------------
# PAYMENT INFO
# -------------------------------------------------

@app.route("/api/payment-info")
def payment_info():

    return jsonify({

        "network": "BNB Smart Chain (BEP-20)",

        "address": BNB_ADDRESS

    })


# -------------------------------------------------
# CREATE ORDER
# -------------------------------------------------

@app.route(
    "/api/order",
    methods=["POST"]
)
def create_order():
    data = request.json

    username = get_request_username()
    if not username:
        return jsonify({
            "success": False,
            "message": "Telegram kullanıcı doğrulaması gerekli."
        }), 401

    package_id = data.get("package_id")
    txid = data.get("txid", "").strip()
    receipt = data.get("receipt", "").strip()

    if not package_id:
        return jsonify({
            "success": False,
            "message": "Paket seçilmedi."
        }), 400

    if not txid:
        return jsonify({
            "success": False,
            "message": "TXID gerekli."
        }), 400

    telegram_user = get_telegram_user()
    telegram_user_id = telegram_user["id"] if telegram_user else None

    conn = get_db()

    package = conn.execute(
        "SELECT * FROM vip_packages WHERE id = ? AND active = 1",
        (package_id,)
    ).fetchone()

    if not package:
        conn.close()
        return jsonify({
            "success": False,
            "message": "Paket bulunamadı."
        }), 404

    order_id = insert_and_get_id(conn, """
        INSERT INTO orders
        (
            username,
            telegram_user_id,
            package_id,
            txid,
            receipt,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    """, (
        username,
        telegram_user_id,
        package_id,
        txid,
        receipt,
        time.time()
    ))

    conn.commit()
    conn.close()

    notify_admin_new_order(
        order_id,
        username,
        package,
        txid
    )

    return jsonify({
        "success": True,
        "message": "Ödeme talebin admin onayına gönderildi."
    })



# -------------------------------------------------
# WITHDRAW
# -------------------------------------------------

PENGUIN_PER_USDT = 15000.0
MIN_WITHDRAW_PENGUIN = 15000.0


def valid_bep20_address(address):

    if not address:
        return False

    address = address.strip()

    if len(address) != 42:
        return False

    if not address.startswith("0x"):
        return False

    return all(
        c in "0123456789abcdefABCDEF"
        for c in address[2:]
    )


@app.route(
    "/api/withdraw",
    methods=["POST"]
)
def create_withdrawal():

    data = request.get_json(silent=True) or {}

    username = get_request_username()
    if not username:
        return jsonify({"success": False, "message": "Telegram kullanıcı doğrulaması gerekli."}), 401

    wallet_address = str(
        data.get("wallet_address", "")
    ).strip()

    try:
        amount_penguin = float(
            data.get("amount_penguin", 0)
        )
    except (TypeError, ValueError):
        amount_penguin = 0

    if not username:

        return jsonify({
            "success": False,
            "message": "Kullanıcı bulunamadı."
        }), 400

    if amount_penguin < MIN_WITHDRAW_PENGUIN:

        return jsonify({
            "success": False,
            "message": (
                "Minimum çekim miktarı "
                "15.000 PENGUIN."
            )
        }), 400

    if not valid_bep20_address(
        wallet_address
    ):

        return jsonify({
            "success": False,
            "message": (
                "Geçerli bir BEP20 cüzdan "
                "adresi girin."
            )
        }), 400

    amount_usdt = (
        amount_penguin /
        PENGUIN_PER_USDT
    )

    # Önce güncel mining kazancını hesaba kat.
    update_mining(username)

    conn = get_db()

    try:

        # Aynı anda gelen iki çekim isteğinin
        # aynı bakiyeyi kullanmasını engelle.
        conn.execute("BEGIN")

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE username = ?
        """, (username,)).fetchone()

        if not user:

            conn.rollback()

            return jsonify({
                "success": False,
                "message": "Kullanıcı bulunamadı."
            }), 404

        balance = float(
            user["balance"] or 0
        )

        if amount_penguin > balance:

            conn.rollback()

            return jsonify({
                "success": False,
                "message": (
                    "Yetersiz PENGUIN bakiyesi."
                ),
                "balance": balance
            }), 400

        # Çekim talebi oluşturulduğu anda
        # miktar kullanıcı bakiyesinden ayrılır.
        new_balance = (
            balance -
            amount_penguin
        )

        conn.execute("""
            UPDATE users
            SET balance = ?
            WHERE username = ?
        """, (
            new_balance,
            username
        ))

        created_at = time.time()

        withdrawal_id = insert_and_get_id(conn, """
            INSERT INTO withdrawals (
                username,
                amount_penguin,
                amount_usdt,
                wallet_address,
                network,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, 'BEP20', 'pending', ?)
        """, (
            username,
            amount_penguin,
            amount_usdt,
            wallet_address,
            created_at
        ))

        conn.commit()
        notify_admin_new_withdrawal(
            withdrawal_id,
            username,
            amount_penguin,
            amount_usdt,
            wallet_address
        )


        return jsonify({
            "success": True,
            "message": (
                "Çekim talebin admin "
                "onayına gönderildi."
            ),
            "withdrawal_id": withdrawal_id,
            "amount_penguin": amount_penguin,
            "amount_usdt": amount_usdt,
            "wallet_address": wallet_address,
            "network": "BEP20",
            "status": "pending",
            "balance": new_balance
        })

    except Exception as e:

        conn.rollback()

        print(
            "❌ Withdraw oluşturma hatası:",
            e
        )

        return jsonify({
            "success": False,
            "message": (
                "Çekim oluşturulurken "
                "bir hata oluştu."
            )
        }), 500

    finally:
        conn.close()


@app.route("/api/withdrawals")
def withdrawals():

    username = get_request_username()
    if not username:
        return jsonify({"success": False, "message": "Telegram kullanıcı doğrulaması gerekli."}), 401

    if not username:
        return jsonify([])

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            username,
            amount_penguin,
            amount_usdt,
            wallet_address,
            network,
            status,
            created_at,
            processed_at
        FROM withdrawals
        WHERE username = ?
        ORDER BY created_at DESC
    """, (
        username,
    )).fetchall()

    conn.close()

    return jsonify([
        {
            "id": row["id"],
            "username": row["username"],
            "amount_penguin": row["amount_penguin"],
            "amount_usdt": row["amount_usdt"],
            "wallet_address": row["wallet_address"],
            "network": row["network"],
            "status": row["status"],
            "created_at": row["created_at"],
            "processed_at": row["processed_at"]
        }
        for row in rows
    ])


@app.route("/api/admin/withdrawals")
def admin_withdrawals():

    admin = request.args.get("admin", type=int)

    if admin != ADMIN_ID:

        return jsonify({
            "success": False,
            "message": "Yetkisiz erişim."
        }), 403

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            username,
            amount_penguin,
            amount_usdt,
            wallet_address,
            network,
            status,
            created_at
        FROM withdrawals
        ORDER BY created_at DESC
    """).fetchall()

    conn.close()

    return jsonify([
        {
            "id": row["id"],
            "username": row["username"],
            "amount_penguin": row["amount_penguin"],
            "amount_usdt": row["amount_usdt"],
            "wallet_address": row["wallet_address"],
            "network": row["network"],
            "status": row["status"],
            "created_at": row["created_at"]
        }
        for row in rows
    ])


# -------------------------------------------------
# ADMIN WITHDRAW APPROVE
# -------------------------------------------------

@app.route(
    "/api/admin/withdraw/approve",
    methods=["POST"]
)
def approve_withdrawal():

    data = request.get_json(
        silent=True
    ) or {}

    admin = data.get("admin")
    withdrawal_id = data.get(
        "withdrawal_id"
    )

    if admin != ADMIN_ID:

        return jsonify({
            "success": False,
            "message": "Yetkisiz erişim."
        }), 403

    conn = get_db()

    try:

        conn.execute("BEGIN")

        withdrawal = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id = ?
        """, (
            withdrawal_id,
        )).fetchone()

        if not withdrawal:

            conn.rollback()

            return jsonify({
                "success": False,
                "message": "Çekim bulunamadı."
            }), 404

        if withdrawal["status"] != "pending":

            conn.rollback()

            return jsonify({
                "success": False,
                "message": (
                    "Bu çekim zaten işlendi."
                ),
                "status": withdrawal["status"]
            })

        now = time.time()

        conn.execute("""
            UPDATE withdrawals
            SET status = 'approved',
                processed_at = ?,
                admin_id = ?
            WHERE id = ?
            AND status = 'pending'
        """, (
            now,
            ADMIN_ID,
            withdrawal_id
        ))

        conn.commit()

        return jsonify({
            "success": True,
            "message": "Çekim onaylandı.",
            "withdrawal_id": withdrawal_id,
            "status": "approved"
        })

    except Exception as e:

        conn.rollback()

        print(
            "❌ Withdraw approve hatası:",
            e
        )

        return jsonify({
            "success": False,
            "message": (
                "Çekim onaylanırken "
                "bir hata oluştu."
            )
        }), 500

    finally:
        conn.close()


# -------------------------------------------------
# ADMIN WITHDRAW REJECT
# -------------------------------------------------

@app.route(
    "/api/admin/withdraw/reject",
    methods=["POST"]
)
def reject_withdrawal():

    data = request.get_json(
        silent=True
    ) or {}

    admin = data.get("admin")
    withdrawal_id = data.get(
        "withdrawal_id"
    )

    if admin != ADMIN_ID:

        return jsonify({
            "success": False,
            "message": "Yetkisiz erişim."
        }), 403

    conn = get_db()

    try:

        conn.execute("BEGIN")

        withdrawal = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id = ?
        """, (
            withdrawal_id,
        )).fetchone()

        if not withdrawal:

            conn.rollback()

            return jsonify({
                "success": False,
                "message": "Çekim bulunamadı."
            }), 404

        if withdrawal["status"] != "pending":

            conn.rollback()

            return jsonify({
                "success": False,
                "message": (
                    "Bu çekim zaten işlendi."
                ),
                "status": withdrawal["status"]
            })

        # Red durumunda çekilen PENGUIN
        # kullanıcıya tamamen geri verilir.
        conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE username = ?
        """, (
            withdrawal["amount_penguin"],
            withdrawal["username"]
        ))

        now = time.time()

        conn.execute("""
            UPDATE withdrawals
            SET status = 'rejected',
                processed_at = ?,
                admin_id = ?
            WHERE id = ?
            AND status = 'pending'
        """, (
            now,
            ADMIN_ID,
            withdrawal_id
        ))

        conn.commit()

        return jsonify({
            "success": True,
            "message": (
                "Çekim reddedildi ve "
                "PENGUIN bakiyesi iade edildi."
            ),
            "withdrawal_id": withdrawal_id,
            "status": "rejected"
        })

    except Exception as e:

        conn.rollback()

        print(
            "❌ Withdraw reject hatası:",
            e
        )

        return jsonify({
            "success": False,
            "message": (
                "Çekim reddedilirken "
                "bir hata oluştu."
            )
        }), 500

    finally:
        conn.close()




# -------------------------------------------------
# ADMIN ORDERS
# -------------------------------------------------


@app.route("/api/my-orders")
def my_orders():
    username = get_request_username()
    if not username:
        return jsonify({"success": False, "message": "Telegram kullanıcı doğrulaması gerekli."}), 401

    if not username:
        return jsonify([])

    conn = get_db()

    orders = conn.execute("""
        SELECT
            orders.id,
            orders.txid,
            orders.receipt,
            orders.status,
            orders.created_at,
            vip_packages.name,
            vip_packages.price_bnb,
            vip_packages.multiplier
        FROM orders
        JOIN vip_packages
        ON orders.package_id = vip_packages.id
        WHERE orders.username = ?
        ORDER BY orders.created_at DESC
    """, (username,)).fetchall()

    conn.close()

    return jsonify([
        {
            "id": o["id"],
            "package": o["name"],
            "price_bnb": o["price_bnb"],
            "multiplier": o["multiplier"],
            "txid": o["txid"],
            "receipt": o["receipt"],
            "status": o["status"],
            "created_at": o["created_at"]
        }
        for o in orders
    ])


@app.route("/api/admin/orders")
def admin_orders():

    admin = request.args.get("admin", type=int)

    if admin != ADMIN_ID:

        return jsonify({
            "error": "Yetkisiz erişim."
        }), 403

    conn = get_db()

    orders = conn.execute("""
        SELECT
            orders.*,
            vip_packages.name,
            vip_packages.multiplier
        FROM orders
        JOIN vip_packages
        ON orders.package_id = vip_packages.id
        ORDER BY orders.created_at DESC
    """).fetchall()

    conn.close()

    return jsonify([

        {
            "id": o["id"],
            "username": o["username"],
            "package": o["name"],
            "multiplier": o["multiplier"],
            "txid": o["txid"],
            "receipt": o["receipt"],
            "status": o["status"]
        }

        for o in orders

    ])


# -------------------------------------------------
# ADMIN APPROVE
# -------------------------------------------------

@app.route(
    "/api/admin/order/approve",
    methods=["POST"]
)
def approve_order():

    data = request.json

    admin = data.get("admin")

    order_id = data.get("order_id")

    if admin != ADMIN_ID:

        return jsonify({
            "success": False,
            "message": "Yetkisiz erişim."
        }), 403

    conn = get_db()

    order = conn.execute(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,)
    ).fetchone()

    if not order:

        conn.close()

        return jsonify({
            "success": False,
            "message": "Sipariş bulunamadı."
        }), 404

    if order["status"] != "pending":

        conn.close()

        return jsonify({
            "success": False,
            "message": "Bu sipariş zaten işlendi."
        })

    package = conn.execute(
        "SELECT * FROM vip_packages WHERE id = ?",
        (order["package_id"],)
    ).fetchone()

    # Mevcut mining kazancını önce eski multiplier ile kaydet
    update_mining(order["username"])

    current_user = conn.execute("""
        SELECT vip_name, vip_multiplier
        FROM users
        WHERE username = ?
    """, (order["username"],)).fetchone()

    current_multiplier = float(current_user["vip_multiplier"] or 1)

    # İlk VIP satın alımında Free 1x üzerine ekleme yapma.
    # Sonraki tüm VIP satın alımlarında multiplier toplanır.
    if current_user["vip_name"] == "Free":
        new_multiplier = float(package["multiplier"])
    else:
        new_multiplier = current_multiplier + float(package["multiplier"])

    conn.execute("""
        UPDATE users
        SET vip_name = ?,
            vip_multiplier = ?,
            mining_rate = ?
        WHERE username = ?
    """, (
        package["name"],
        new_multiplier,
        BASE_MINING_RATE * new_multiplier,
        order["username"]
    ))

    conn.execute("""
        UPDATE orders
        SET status = 'approved'
        WHERE id = ?
    """, (
        order_id,
    ))

    conn.commit()
    conn.close()

    return jsonify({

        "success": True,

        "message": "VIP aktif edildi."

    })


# -------------------------------------------------
# ADMIN REJECT
# -------------------------------------------------

@app.route(
    "/api/admin/order/reject",
    methods=["POST"]
)
def reject_order():

    data = request.json

    admin = data.get("admin")

    order_id = data.get("order_id")

    if admin != ADMIN_ID:

        return jsonify({
            "success": False,
            "message": "Yetkisiz erişim."
        }), 403

    conn = get_db()

    conn.execute("""
        UPDATE orders
        SET status = 'rejected'
        WHERE id = ?
        AND status = 'pending'
    """, (
        order_id,
    ))

    conn.commit()
    conn.close()

    return jsonify({

        "success": True,

        "message": "Sipariş reddedildi."

    })


# -------------------------------------------------
# TELEGRAM ADMIN ORDER ACTION
# -------------------------------------------------


@app.route("/admin/withdraw-action")
def admin_withdraw_action():
    withdrawal_id = request.args.get("withdrawal_id", type=int)
    action = request.args.get("action", "")
    timestamp = request.args.get("ts", type=int)
    token = request.args.get("token", "")

    if not withdrawal_id or action not in ("approve", "reject") or not timestamp or not token:
        return "Geçersiz çekim işlemi.", 400

    token_action = f"withdraw_{action}"

    if not verify_admin_action_token(
        withdrawal_id,
        token_action,
        timestamp,
        token
    ):
        return "Geçersiz veya süresi dolmuş işlem bağlantısı.", 403

    conn = get_db()

    try:
        conn.execute("BEGIN")

        withdrawal = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id = ?
        """, (withdrawal_id,)).fetchone()

        if not withdrawal:
            conn.rollback()
            return "Çekim bulunamadı.", 404

        if withdrawal["status"] != "pending":
            conn.rollback()
            return (
                f"Bu çekim zaten işlendi. "
                f"Mevcut durum: {withdrawal['status']}"
            )

        now = time.time()

        if action == "approve":
            conn.execute("""
                UPDATE withdrawals
                SET status = 'approved',
                    processed_at = ?,
                    admin_id = ?
                WHERE id = ?
                AND status = 'pending'
            """, (
                now,
                ADMIN_ID,
                withdrawal_id
            ))

            message = (
                "🐧 <b>ÇEKİM TALEBİN ONAYLANDI!</b>\n\n"
                f"💰 Miktar: <b>{withdrawal['amount_penguin']:,.0f} PENGUIN</b>\n"
                f"💵 Değer: <b>{withdrawal['amount_usdt']:.2f} USDT</b>\n"
                f"👛 Cüzdan: <code>{withdrawal['wallet_address']}</code>\n"
                f"🆔 Çekim: <b>#{withdrawal_id}</b>"
            )

            result_text = "✅ Çekim onaylandı."

        else:
            conn.execute("""
                UPDATE users
                SET balance = balance + ?
                WHERE username = ?
            """, (
                withdrawal["amount_penguin"],
                withdrawal["username"]
            ))

            conn.execute("""
                UPDATE withdrawals
                SET status = 'rejected',
                    processed_at = ?,
                    admin_id = ?
                WHERE id = ?
                AND status = 'pending'
            """, (
                now,
                ADMIN_ID,
                withdrawal_id
            ))

            message = (
                "🐧 <b>ÇEKİM TALEBİN REDDEDİLDİ.</b>\n\n"
                f"💰 Miktar: <b>{withdrawal['amount_penguin']:,.0f} PENGUIN</b>\n"
                f"💵 Değer: <b>{withdrawal['amount_usdt']:.2f} USDT</b>\n"
                f"🆔 Çekim: <b>#{withdrawal_id}</b>\n\n"
                "💰 Çekilen PENGUIN bakiyene iade edildi."
            )

            result_text = "❌ Çekim reddedildi ve bakiye iade edildi."

        conn.commit()

        user = conn.execute("""
            SELECT telegram_user_id
            FROM users
            WHERE username = ?
        """, (
            withdrawal["username"],
        )).fetchone()

        if user and user["telegram_user_id"]:
            telegram_send_message(
                user["telegram_user_id"],
                message
            )

        return f"""
        <html>
        <body style="font-family:Arial;text-align:center;padding:40px">
            <h2>{result_text}</h2>
            <p>Çekim #{withdrawal_id}</p>
        </body>
        </html>
        """

    except Exception as e:
        conn.rollback()
        print("❌ Admin withdraw action hatası:", e)
        return "İşlem sırasında hata oluştu.", 500

    finally:
        conn.close()


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()

    if webhook_secret:
        received_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            ""
        )

        if not hmac.compare_digest(
            received_secret,
            webhook_secret
        ):
            return jsonify({"ok": False}), 403

    update = request.get_json(silent=True) or {}

    callback = update.get("callback_query")

    # Normal Telegram update'i ise hiçbir işlem yapma.
    if not callback:
        return jsonify({"ok": True})

    callback_id = callback.get("id")
    callback_data = str(callback.get("data") or "").strip()
    callback_user = callback.get("from") or {}

    # Callback'i sadece gerçek admin hesabı çalıştırabilir.
    try:
        callback_user_id = int(callback_user.get("id", 0))
    except (TypeError, ValueError):
        callback_user_id = 0

    if callback_user_id != ADMIN_ID:
        telegram_answer_callback(
            callback_id,
            "⛔ Bu işlem için yetkiniz yok.",
            True
        )
        return jsonify({"ok": True})

    parts = callback_data.split(":")

    if len(parts) != 3:
        telegram_answer_callback(
            callback_id,
            "❌ Geçersiz işlem.",
            True
        )
        return jsonify({"ok": True})

    entity, action, id_text = parts

    if entity != "order" or action not in ("approve", "reject"):
        telegram_answer_callback(
            callback_id,
            "❌ Geçersiz işlem.",
            True
        )
        return jsonify({"ok": True})

    try:
        order_id = int(id_text)
    except (TypeError, ValueError):
        telegram_answer_callback(
            callback_id,
            "❌ Geçersiz sipariş numarası.",
            True
        )
        return jsonify({"ok": True})

    conn = get_db()

    try:
        order = conn.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (order_id,)).fetchone()

        if not order:
            telegram_answer_callback(
                callback_id,
                "❌ Sipariş bulunamadı.",
                True
            )
            return jsonify({"ok": True})

        # Aynı sipariş ikinci kez işlenmesin.
        if order["status"] != "pending":
            telegram_answer_callback(
                callback_id,
                f"ℹ️ Sipariş zaten işlendi: {order['status']}",
                True
            )

            message = callback.get("message") or {}
            original_text = message.get("text") or ""

            telegram_edit_message(
                message,
                original_text,
                {"inline_keyboard": []}
            )

            return jsonify({"ok": True})

        package = conn.execute("""
            SELECT *
            FROM vip_packages
            WHERE id = ?
        """, (order["package_id"],)).fetchone()

        if not package:
            telegram_answer_callback(
                callback_id,
                "❌ VIP paketi bulunamadı.",
                True
            )
            return jsonify({"ok": True})

        message = callback.get("message") or {}
        original_text = message.get("text") or ""

        if action == "approve":
            # Siparişi önce atomik olarak kilitle.
            # Böylece aynı callback iki kez aynı anda gelse bile
            # VIP multiplier ikinci kez eklenmez.
            try:
                conn.execute("BEGIN IMMEDIATE")

                claimed = conn.execute("""
                    UPDATE orders
                    SET status = 'processing'
                    WHERE id = ?
                    AND status = 'pending'
                """, (order_id,))

                if claimed.rowcount != 1:
                    conn.rollback()

                    telegram_answer_callback(
                        callback_id,
                        "ℹ️ Bu sipariş zaten işleniyor veya işlendi.",
                        True
                    )

                    telegram_edit_message(
                        message,
                        original_text,
                        {"inline_keyboard": []}
                    )

                    return jsonify({"ok": True})

                # Kullanıcıyı aynı transaction içinde al.
                current_user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE username = ?
                """, (order["username"],)).fetchone()

                if not current_user:
                    raise RuntimeError("Kullanıcı bulunamadı.")

                now = time.time()

                # Mevcut mining kazancını eski multiplier ile hesapla.
                if current_user["mining"] == 1:
                    last_update = current_user["last_update"] or now
                    elapsed = max(0, now - last_update)

                    earned = (
                        elapsed
                        * BASE_MINING_RATE
                        * float(current_user["vip_multiplier"] or 1)
                    )

                    pending_mining = (
                        float(current_user["pending_mining"] or 0)
                        + earned
                    )
                else:
                    pending_mining = float(
                        current_user["pending_mining"] or 0
                    )

                current_multiplier = float(
                    current_user["vip_multiplier"] or 1
                )

                if current_user["vip_name"] == "Free":
                    new_multiplier = float(package["multiplier"])
                else:
                    new_multiplier = (
                        current_multiplier
                        + float(package["multiplier"])
                    )

                conn.execute("""
                    UPDATE users
                    SET vip_name = ?,
                        vip_multiplier = ?,
                        mining_rate = ?,
                        pending_mining = ?,
                        last_update = ?
                    WHERE username = ?
                """, (
                    package["name"],
                    new_multiplier,
                    BASE_MINING_RATE * new_multiplier,
                    pending_mining,
                    now,
                    order["username"]
                ))

                conn.execute("""
                    UPDATE orders
                    SET status = 'approved'
                    WHERE id = ?
                    AND status = 'processing'
                """, (order_id,))

                conn.commit()

            except Exception:
                conn.rollback()
                raise

            telegram_answer_callback(
                callback_id,
                "✅ VIP siparişi onaylandı."
            )

            status_text = (
                original_text +
                "\n\n"
                "🟢 <b>DURUM: ONAYLANDI</b>"
            )

            telegram_edit_message(
                message,
                status_text,
                {"inline_keyboard": []}
            )

            telegram_send_message(
                order["telegram_user_id"],
                (
                    "🐧 <b>VIP AKTİF EDİLDİ!</b>\n\n"
                    f"💎 Paket: <b>{package['name']}</b>\n"
                    f"⚡ Mining: <b>{new_multiplier}x</b>\n"
                    "♾️ Süre: <b>Sınırsız</b>\n\n"
                    "Madenciliğiniz güncellendi. 🚀"
                )
            )

        else:
            conn.execute("""
                UPDATE orders
                SET status = 'rejected'
                WHERE id = ?
                AND status = 'pending'
            """, (order_id,))

            conn.commit()

            telegram_answer_callback(
                callback_id,
                "❌ VIP siparişi reddedildi."
            )

            status_text = (
                original_text +
                "\n\n"
                "🔴 <b>DURUM: REDDEDİLDİ</b>"
            )

            telegram_edit_message(
                message,
                status_text,
                {"inline_keyboard": []}
            )

            telegram_send_message(
                order["telegram_user_id"],
                (
                    "🐧 <b>VIP SİPARİŞİ REDDEDİLDİ</b>\n\n"
                    f"💎 Paket: <b>{package['name']}</b>\n"
                    f"🧾 Sipariş: <b>#{order_id}</b>\n\n"
                    "Lütfen ödeme ve TXID bilgilerinizi kontrol edin."
                )
            )

        return jsonify({"ok": True})

    except Exception as e:
        conn.rollback()
        print("Telegram webhook callback hatası:", e)

        telegram_answer_callback(
            callback_id,
            "❌ İşlem sırasında hata oluştu.",
            True
        )

        return jsonify({"ok": True})

    finally:
        conn.close()


@app.route("/admin/order-action")
def admin_order_action():
    order_id = request.args.get("order_id", type=int)
    action = request.args.get("action", "").strip().lower()
    timestamp = request.args.get("ts")
    token = request.args.get("token", "")

    if action not in ("approve", "reject"):
        return "<h2>Geçersiz işlem.</h2>", 400

    if not order_id or not verify_admin_action_token(
        order_id,
        action,
        timestamp,
        token
    ):
        return "<h2>Geçersiz veya süresi dolmuş bağlantı.</h2>", 403

    conn = get_db()

    order = conn.execute("""
        SELECT *
        FROM orders
        WHERE id = ?
    """, (order_id,)).fetchone()

    if not order:
        conn.close()
        return "<h2>Sipariş bulunamadı.</h2>", 404

    if order["status"] != "pending":
        status = order["status"]
        conn.close()
        return (
            f"<h2>Bu sipariş zaten işlendi.</h2>"
            f"<p>Durum: {status}</p>"
        ), 200

    package = conn.execute("""
        SELECT *
        FROM vip_packages
        WHERE id = ?
    """, (order["package_id"],)).fetchone()

    if action == "approve":
        # Mevcut mining kazancını önce eski multiplier ile kaydet
        update_mining(order["username"])

        current_user = conn.execute("""
            SELECT vip_name, vip_multiplier
            FROM users
            WHERE username = ?
        """, (order["username"],)).fetchone()

        current_multiplier = float(current_user["vip_multiplier"] or 1)

        # İlk VIP satın alımında Free 1x üzerine ekleme yapma.
        # Sonraki tüm VIP satın alımlarında multiplier toplanır.
        if current_user["vip_name"] == "Free":
            new_multiplier = float(package["multiplier"])
        else:
            new_multiplier = current_multiplier + float(package["multiplier"])

        conn.execute("""
            UPDATE users
            SET vip_name = ?,
                vip_multiplier = ?,
                mining_rate = ?
            WHERE username = ?
        """, (
            package["name"],
            new_multiplier,
            BASE_MINING_RATE * new_multiplier,
            order["username"]
        ))

        conn.execute("""
            UPDATE orders
            SET status = 'approved'
            WHERE id = ?
            AND status = 'pending'
        """, (order_id,))

        conn.commit()
        conn.close()

        if order["telegram_user_id"]:
            telegram_send_message(
                order["telegram_user_id"],
                (
                    "🐧 <b>VIP PAKETİN AKTİF EDİLDİ!</b>\n\n"
                    f"💎 Paket: <b>{package['name']}</b>\n"
                    f"⚡ Mining: <b>{package['multiplier']}x</b>\n"
                    f"💰 Ödeme: <b>{package['price_bnb']} BNB</b>"
                )
            )

        return (
            "<h2>✅ Sipariş onaylandı.</h2>"
            "<p>VIP paket kullanıcı hesabında aktif edildi.</p>"
        ), 200

    conn.execute("""
        UPDATE orders
        SET status = 'rejected'
        WHERE id = ?
        AND status = 'pending'
    """, (order_id,))

    conn.commit()
    conn.close()

    if order["telegram_user_id"]:
        telegram_send_message(
            order["telegram_user_id"],
            (
                "🐧 <b>VIP ÖDEME TALEBİN REDDEDİLDİ.</b>\n\n"
                f"💎 Paket: <b>{package['name']}</b>\n"
                f"🆔 Sipariş: <b>#{order_id}</b>"
            )
        )

    return (
        "<h2>❌ Sipariş reddedildi.</h2>"
        "<p>Sipariş durumu reddedildi olarak güncellendi.</p>"
    ), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

init_db()
