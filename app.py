from flask import Flask, render_template, jsonify, request, session
import hashlib
import hmac
import urllib.parse
import sqlite3
import time
import secrets

app = Flask(__name__)

# -------------------------------------------------
# CONFIG
# -------------------------------------------------

app.secret_key = secrets.token_hex(32)

DB = "penguin.db"

ADMIN_ID = 1028007008

BNB_ADDRESS = "0x54990f6F781D81Fc36B76144dfF8637c337062de"

# Mining temel hızı
BASE_MINING_RATE = 0.000001


import os
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

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

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    # USERS
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            balance REAL DEFAULT 0,
            mining INTEGER DEFAULT 0,
            mining_rate REAL DEFAULT 0.000001,
            vip_name TEXT DEFAULT 'Free',
            vip_multiplier REAL DEFAULT 1,
            referrals INTEGER DEFAULT 0,
            referral_code TEXT UNIQUE,
            referred_by TEXT,
            last_update REAL
        )
    """)

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
            package_id INTEGER,
            txid TEXT,
            receipt TEXT,
            status TEXT DEFAULT 'pending',
            created_at REAL
        )
    """)

    # Default VIP packages
    packages = [
        ("Starter Miner", 0.01, 2, 30),
        ("Pro Miner", 0.03, 5, 30),
        ("Ultra Miner", 0.07, 10, 30),
        ("Legend Miner", 0.15, 25, 30)
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

    conn.commit()
    conn.close()


# -------------------------------------------------
# USER
# -------------------------------------------------

def create_user(username):

    conn = get_db()

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
                last_update
            )
            VALUES (?, 0, 0, ?, 'Free', 1, 0, ?, ?)
        """, (
            username,
            BASE_MINING_RATE,
            referral_code,
            now
        ))

        conn.commit()

    conn.close()


def get_user(username):

    create_user(username)

    conn = get_db()

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

        elapsed = now - user["last_update"]

        earned = (
            elapsed
            * BASE_MINING_RATE
            * user["vip_multiplier"]
        )

        new_balance = user["balance"] + earned

        conn.execute("""
            UPDATE users
            SET balance = ?,
                last_update = ?,
                mining_rate = ?
            WHERE username = ?
        """, (
            new_balance,
            now,
            BASE_MINING_RATE * user["vip_multiplier"],
            username
        ))

    else:

        conn.execute("""
            UPDATE users
            SET last_update = ?
            WHERE username = ?
        """, (
            now,
            username
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

@app.route("/api/user")
def api_user():

    username = request.args.get(
        "username",
        "demo"
    )

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

    username = request.json.get(
        "username",
        "demo"
    )

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

    username = request.json.get(
        "username",
        "demo"
    )

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

    username = data.get("username", "demo")

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

    conn = get_db()

    package = conn.execute(
        "SELECT * FROM vip_packages WHERE id = ?",
        (package_id,)
    ).fetchone()

    if not package:

        conn.close()

        return jsonify({
            "success": False,
            "message": "Paket bulunamadı."
        }), 404

    conn.execute("""
        INSERT INTO orders
        (
            username,
            package_id,
            txid,
            receipt,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, 'pending', ?)
    """, (
        username,
        package_id,
        txid,
        receipt,
        time.time()
    ))

    conn.commit()
    conn.close()

    return jsonify({

        "success": True,

        "message":
        "Ödeme talebin admin onayına gönderildi."

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

    username = str(
        data.get("username", "")
    ).strip()

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
        conn.execute("BEGIN IMMEDIATE")

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

        cursor = conn.execute("""
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

        withdrawal_id = cursor.lastrowid

        conn.commit()

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

    username = request.args.get(
        "username",
        "demo"
    ).strip()

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

        conn.execute("BEGIN IMMEDIATE")

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

        conn.execute("BEGIN IMMEDIATE")

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
    username = request.args.get("username", "demo").strip()

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

    conn.execute("""
        UPDATE users
        SET vip_name = ?,
            vip_multiplier = ?,
            mining_rate = ?
        WHERE username = ?
    """, (
        package["name"],
        package["multiplier"],
        BASE_MINING_RATE * package["multiplier"],
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
# LEADERBOARD
# -------------------------------------------------

@app.route("/api/leaderboard")
def leaderboard():

    conn = get_db()

    users = conn.execute("""
        SELECT username, balance, vip_name
        FROM users
        ORDER BY balance DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    return jsonify([

        {
            "username": u["username"],
            "balance": u["balance"],
            "vip": u["vip_name"]
        }

        for u in users

    ])


# -------------------------------------------------
# RUN
# -------------------------------------------------

if __name__ == "__main__":

    init_db()

    print("🐧 Penguin Mining App çalışıyor...")
    print("🗄️ Database hazır")
    print("💎 VIP sistemi hazır")
    print("💳 BNB ödeme sistemi hazır")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
