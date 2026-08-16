
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
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS licenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        device_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        activated_at TEXT,
        expires_at TEXT,
        request_ip TEXT)""")
    c.commit(); c.close()

def make_key():
    chars = string.ascii_uppercase + string.digits
    while True:
        key = "KMK-" + "-".join("".join(secrets.choice(chars) for _ in range(5)) for _ in range(4))
        c = db()
        exists = c.execute("SELECT 1 FROM licenses WHERE key=?", (key,)).fetchone()
        c.close()
        if not exists: return key

def tg(method, payload):
    if not BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload, timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def notify_admin(key, device_id, ip):
    text = (f"🔐 KladMapk — новый запрос лицензии\n\n"
            f"Ключ: {key}\nУстройство: {device_id}\nIP: {ip}\nДата: {now_iso()}\n\n"
            "Нажми «Активировать», чтобы выдать лицензию.")
    kb = {"inline_keyboard":[[
        {"text":"✅ Активировать","callback_data":f"approve:{key}"},
        {"text":"❌ Отклонить","callback_data":f"reject:{key}"}
    ]]}
    return tg("sendMessage", {"chat_id": ADMIN_ID, "text": text, "reply_markup": kb})

@app.get("/")
def home():
    return jsonify({"service":"KladMapk License Server","status":"ok"})

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.post("/license/request")
def license_request():
    data = request.get_json(silent=True) or {}
    device_id = str(data.get("device_id","")).strip()
    if not device_id:
        return jsonify({"ok":False,"error":"device_id is required"}), 400
    c = db()
    old = c.execute("SELECT key,status FROM licenses WHERE device_id=? AND status IN ('pending','active') ORDER BY id DESC LIMIT 1",(device_id,)).fetchone()
    if old:
        c.close()
        return jsonify({"ok":True,"status":old["status"],"key":old["key"] if old["status"]=="active" else None})
    key = make_key()
    c.execute("INSERT INTO licenses(key,device_id,status,created_at,request_ip) VALUES(?,?,?,?,?)",
              (key,device_id,"pending",now_iso(),request.remote_addr or ""))
    c.commit(); c.close()
    t = notify_admin(key,device_id,request.remote_addr or "")
    return jsonify({"ok":True,"status":"pending","key":None,"telegram_ok":t.get("ok",False)})

@app.post("/license/check")
def license_check():
    data = request.get_json(silent=True) or {}
    key = str(data.get("key","")).strip().upper()
    device_id = str(data.get("device_id","")).strip()
    if not key or not device_id:
        return jsonify({"ok":False,"active":False,"error":"key and device_id are required"}), 400
    c = db(); row = c.execute("SELECT * FROM licenses WHERE key=?",(key,)).fetchone(); c.close()
    if not row: return jsonify({"ok":True,"active":False,"reason":"not_found"})
    if row["status"] != "active": return jsonify({"ok":True,"active":False,"reason":row["status"]})
    if row["device_id"] != device_id: return jsonify({"ok":True,"active":False,"reason":"device_mismatch"})
    if row["expires_at"] and datetime.now(timezone.utc) >= datetime.fromisoformat(row["expires_at"]):
        c=db(); c.execute("UPDATE licenses SET status='expired' WHERE key=?",(key,)); c.commit(); c.close()
        return jsonify({"ok":True,"active":False,"reason":"expired"})
    return jsonify({"ok":True,"active":True,"expires_at":row["expires_at"]})

def approve(key):
    expires=(datetime.now(timezone.utc)+timedelta(days=LICENSE_DAYS)).isoformat()
    c=db(); row=c.execute("SELECT status FROM licenses WHERE key=?",(key,)).fetchone()
    if not row: c.close(); return "Лицензия не найдена."
    if row["status"]=="active": c.close(); return "Лицензия уже активирована."
    c.execute("UPDATE licenses SET status='active',activated_at=?,expires_at=? WHERE key=?",(now_iso(),expires,key))
    c.commit(); c.close()
    return f"Лицензия {key} активирована на {LICENSE_DAYS} дней."

def reject(key):
    c=db(); row=c.execute("SELECT 1 FROM licenses WHERE key=?",(key,)).fetchone()
    if row: c.execute("UPDATE licenses SET status='rejected' WHERE key=?",(key,)); c.commit()
    c.close()
    return f"Лицензия {key} отклонена." if row else "Лицензия не найдена."

@app.post("/telegram/webhook")
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return "forbidden",403
    u=request.get_json(silent=True) or {}
    q=u.get("callback_query")
    if q:
        uid=str(q.get("from",{}).get("id",""))
        if uid != str(ADMIN_ID):
            tg("answerCallbackQuery",{"callback_query_id":q.get("id"),"text":"Нет доступа."})
            return jsonify({"ok":True})
        data=q.get("data","")
        action,key=(data.split(":",1)+[""])[:2] if ":" in data else (data,"")
        msg=approve(key) if action=="approve" else reject(key) if action=="reject" else "Неизвестная команда."
        tg("answerCallbackQuery",{"callback_query_id":q.get("id"),"text":msg[:180]})
        tg("editMessageReplyMarkup",{"chat_id":q["message"]["chat"]["id"],"message_id":q["message"]["message_id"],"reply_markup":{"inline_keyboard":[]}})
        tg("sendMessage",{"chat_id":ADMIN_ID,"text":msg})
    return jsonify({"ok":True})

def set_webhook():
    if BOT_TOKEN and BASE_URL:
        p={"url":f"{BASE_URL}/telegram/webhook"}
        if WEBHOOK_SECRET: p["secret_token"]=WEBHOOK_SECRET
        tg("setWebhook",p)

init_db()
set_webhook()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
