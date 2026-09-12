#!/usr/bin/env python3
"""Готовит письмо в боте для вакансии, чей адрес скрыт за капчей Arbeitsagentur.

Делает за Никиту два шага: нажатие «✍️ Написать Anschreiben» и ответ с адресом
(и, если известно, с контактным лицом), после которого у письма появляется
кнопка отправки. Само письмо НЕ отправляется — это всегда его решение.

На сервере: ssh n8n-server python3 - <refnr> <e-mail> ["Frau Имя Фамилия"] < scripts/prepare_letter.py
"""
import html
import json
import re
import sqlite3
import sys
import time
import urllib.request

REFNR, EMAIL = sys.argv[1], sys.argv[2]
PERSON = sys.argv[3] if len(sys.argv) > 3 else ""
WF = "cgIAYOj2RrvvWKPC"
CHAT = 6092369283

db = sqlite3.connect("/root/.n8n/database.sqlite")
nodes = json.loads(db.execute("select nodes from workflow_entity where id=?", (WF,)).fetchone()[0])
trg = next(n for n in nodes if n["type"] == "n8n-nodes-base.telegramTrigger")
URL = f"http://127.0.0.1:5678/webhook/{trg['webhookId']}/webhook"
SECRET = f"{WF}_{trg['id']}"
JOB_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/" + REFNR


def post(update):
    req = urllib.request.Request(URL, method="POST", data=json.dumps(update).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-Telegram-Bot-Api-Secret-Token": SECRET})
    return urllib.request.urlopen(req).read().decode()[:40]


def last_letter():
    """Последнее письмо бота: текст без разметки, id сообщения и клавиатура."""
    for eid, in db.execute(
            "select id from execution_entity where workflowId=? and status='success' order by id desc limit 6", (WF,)):
        arr = json.loads(db.execute("select data from execution_data where executionId=?", (eid,)).fetchone()[0])
        ref = lambda v: arr[int(v)] if isinstance(v, str) and v.isdigit() else v

        def deep(v, d=0):
            v = ref(v)
            if isinstance(v, dict) and d < 10:
                return {k: deep(x, d + 1) for k, x in v.items()}
            if isinstance(v, list) and d < 10:
                return [deep(x, d + 1) for x in v]
            return v

        run = ref(ref(ref(arr[0])["resultData"])["runData"])
        if "Brief-Nachricht" not in run:
            continue
        first = lambda n: deep(ref(ref(ref(ref(run[n])[0])["data"])["main"])[0])[0]["json"]
        body, resp = first("Brief-Nachricht")["body"], first("Telegram API")
        if not resp.get("ok"):
            continue
        return body, resp["result"]["message_id"]
    return None, None


card = {"update_id": int(time.time()), "callback_query": {
    "id": "prep-%d" % time.time(), "from": {"id": CHAT},
    "message": {"message_id": 1, "chat": {"id": CHAT, "type": "private"}, "text": "Vorbereitung",
                "reply_markup": {"inline_keyboard": [[{"text": "🔗", "url": JOB_URL}]]}},
    "data": "w:" + REFNR}}
print("письмо:", post(card), flush=True)
time.sleep(45)

body, msg_id = last_letter()
if not body:
    sys.exit("письмо не найдено — проверь прогон вручную")
plain = html.unescape(re.sub(r"<[^>]+>", "", body["text"]))
text = EMAIL + (", Ansprechpartner: " + PERSON if PERSON else "")
reply = {"update_id": int(time.time()) + 1, "message": {
    "message_id": 2, "chat": {"id": CHAT, "type": "private"}, "from": {"id": CHAT}, "text": text,
    "reply_to_message": {"message_id": msg_id, "chat": {"id": CHAT}, "text": plain,
                         "reply_markup": body["reply_markup"]}}}
print("адрес:", post(reply), flush=True)
time.sleep(40)
body, msg_id = last_letter()
btn = [b["text"] for row in (body or {}).get("reply_markup", {}).get("inline_keyboard", []) for b in row
       if b["text"].startswith("📤")]
print("готово:", btn[0] if btn else "БЕЗ кнопки отправки — посмотреть письмо в боте")
