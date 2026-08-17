import os
import sqlite3
import secrets
import string
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

DB_PATH = os.getenv("DB_PATH", "licenses.db")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "1496817191")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
LICENSE_DAYS = int(os.getenv("LICENSE_DAYS", "30"))

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        device_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        activated_at TEXT,
        expires_at TEXT,
        request_ip TEXT
    )""")
    conn.commit()
    conn.close()

def make_key():
    chars = string.ascii_uppercase + string.digits
    while True:
        key = "KMK-" + "-".join(
            "".join(secrets.choice(chars) for _ in range(5))
            for _ in range(4)
        )
        conn = db()
        exists = conn.execute(
            "SELECT 1 FROM licenses WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        if not exists:
            return key

def tg(method, payload):
    if not BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload,
            timeout=15
        )
        return response.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

def notify_admin(key, device_id, ip):
    text = (
        "🔐 KladMapk — новый запрос лицензии\n\n"
        f"Ключ: {key}\n"
        f"Устройство: {device_id}\n"
        f"IP: {ip}\n"
        f"Дата: {now_iso()}\n\n"
        "Нажми «Активировать», чтобы выдать лицензию."
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Активировать", "callback_data": f"approve:{key}"},
        {"text": "❌ Отклонить", "callback_data": f"reject:{key}"}
    ]]}
    return tg("sendMessage", {
        "chat_id": ADMIN_ID,
        "text": text,
        "reply_markup": keyboard
    })

@app.get("/")
def home():
    return jsonify({"service": "KladMapk License Server", "status": "ok"})

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.post("/license/request")
def license_request():
    data = request.get_json(silent=True) or {}
    device_id = str(data.get("device_id", "")).strip()
    if not device_id:
        return jsonify({"ok": False, "error": "device_id is required"}), 400

    conn = db()
    old = conn.execute(
        """SELECT key, status FROM licenses
           WHERE device_id = ? AND status IN ('pending', 'active')
           ORDER BY id DESC LIMIT 1""",
        (device_id,)
    ).fetchone()

    if old:
        conn.close()
        return jsonify({
            "ok": True,
            "status": old["status"],
            "key": old["key"] if old["status"] == "active" else None
        })

    key = make_key()
    conn.execute(
        """INSERT INTO licenses
           (key, device_id, status, created_at, request_ip)
           VALUES (?, ?, ?, ?, ?)""",
        (key, device_id, "pending", now_iso(), request.remote_addr or "")
    )
    conn.commit()
    conn.close()

    result = notify_admin(key, device_id, request.remote_addr or "")
    return jsonify({
        "ok": True,
        "status": "pending",
        "key": None,
        "telegram_ok": result.get("ok", False)
    })

@app.post("/license/check")
def license_check():
    data = request.get_json(silent=True) or {}
    key = str(data.get("key", "")).strip().upper()
    device_id = str(data.get("device_id", "")).strip()

    if not key or not device_id:
        return jsonify({
            "ok": False,
            "active": False,
            "error": "key and device_id are required"
        }), 400

    conn = db()
    row = conn.execute(
        "SELECT * FROM licenses WHERE key = ?", (key,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"ok": True, "active": False, "reason": "not_found"})
    if row["status"] != "active":
        return jsonify({"ok": True, "active": False, "reason": row["status"]})
    if row["device_id"] != device_id:
        return jsonify({"ok": True, "active": False, "reason": "device_mismatch"})

    if row["expires_at"]:
        expires = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) >= expires:
            conn = db()
            conn.execute(
                "UPDATE licenses SET status = 'expired' WHERE key = ?", (key,)
            )
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "active": False, "reason": "expired"})

    return jsonify({
        "ok": True,
        "active": True,
        "expires_at": row["expires_at"]
    })

def approve(key):
    expires = (
        datetime.now(timezone.utc) +
        timedelta(days=LICENSE_DAYS)
    ).isoformat()

    conn = db()
    row = conn.execute(
        "SELECT status FROM licenses WHERE key = ?", (key,)
    ).fetchone()

    if not row:
        conn.close()
        return "Лицензия не найдена."
    if row["status"] == "active":
        conn.close()
        return "Лицензия уже активирована."

    conn.execute(
        """UPDATE licenses
           SET status = 'active', activated_at = ?, expires_at = ?
           WHERE key = ?""",
        (now_iso(), expires, key)
    )
    conn.commit()
    conn.close()
    return f"Лицензия {key} активирована на {LICENSE_DAYS} дней."

def reject(key):
    conn = db()
    row = conn.execute(
        "SELECT 1 FROM licenses WHERE key = ?", (key,)
    ).fetchone()

    if row:
        conn.execute(
            "UPDATE licenses SET status = 'rejected' WHERE key = ?", (key,)
        )
        conn.commit()
    conn.close()

    return f"Лицензия {key} отклонена." if row else "Лицензия не найдена."

@app.post("/telegram/webhook")
def webhook():
    if (
        WEBHOOK_SECRET and
        request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET
    ):
        return "forbidden", 403

    update = request.get_json(silent=True) or {}
    message = update.get("message")

    if message:
        chat_id = message.get("chat", {}).get("id")
        text = str(message.get("text", "")).strip()

        if text == "/start":
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": (
                    "👋 KladMapk License Bot\n\n"
                    "Бот для управления лицензиями KladMapk.\n\n"
                    "/start — информация\n"
                    "/id — показать ваш Telegram ID"
                )
            })
        elif text == "/id":
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": f"Ваш Telegram ID: {chat_id}"
            })
        return jsonify({"ok": True})

    callback = update.get("callback_query")

    if callback:
        user_id = str(callback.get("from", {}).get("id", ""))

        if user_id != str(ADMIN_ID):
            tg("answerCallbackQuery", {
                "callback_query_id": callback.get("id"),
                "text": "Нет доступа."
            })
            return jsonify({"ok": True})

        data = callback.get("data", "")
        if ":" in data:
            action, key = data.split(":", 1)
        else:
            action, key = data, ""

        if action == "approve":
            result = approve(key)
        elif action == "reject":
            result = reject(key)
        else:
            result = "Неизвестная команда."

        tg("answerCallbackQuery", {
            "callback_query_id": callback.get("id"),
            "text": result[:180]
        })

        msg = callback.get("message", {})
        if msg.get("chat") and msg.get("message_id"):
            tg("editMessageReplyMarkup", {
                "chat_id": msg["chat"]["id"],
                "message_id": msg["message_id"],
                "reply_markup": {"inline_keyboard": []}
            })

        tg("sendMessage", {
            "chat_id": ADMIN_ID,
            "text": result
        })

    return jsonify({"ok": True})

def set_webhook():
    if not BOT_TOKEN:
        print("Webhook skipped: TELEGRAM_BOT_TOKEN is missing.")
        return
    if not BASE_URL:
        print("Webhook skipped: BASE_URL is missing.")
        return

    payload = {"url": f"{BASE_URL}/telegram/webhook"}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET

    result = tg("setWebhook", payload)
    print("Telegram webhook:", result)

init_db()
set_webhook()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000"))
    )
