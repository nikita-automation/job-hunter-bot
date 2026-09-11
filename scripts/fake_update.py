#!/usr/bin/env python3
"""Шлёт в «Job Hunter: Aktionen» поддельный апдейт Telegram — как будто Никита нажал кнопку.

На сервере:  ssh n8n-server python3 - '<json апдейта>' < scripts/fake_update.py
             ssh n8n-server python3 - @/tmp/update.json < scripts/fake_update.py
Секрет вебхука n8n считает как `${workflowId}_${nodeId}` Telegram Trigger-а
(n8n-nodes-base/dist/nodes/Telegram/GenericFunctions.js, getSecretToken).
"""
import json
import sqlite3
import sys
import urllib.request

WF_ID = "cgIAYOj2RrvvWKPC"
db = sqlite3.connect("/root/.n8n/database.sqlite")
nodes = json.loads(db.execute("select nodes from workflow_entity where id=?", (WF_ID,)).fetchone()[0])
trigger = next(n for n in nodes if n["type"] == "n8n-nodes-base.telegramTrigger")
url = f"http://127.0.0.1:5678/webhook/{trigger['webhookId']}/webhook"
secret = f"{WF_ID}_{trigger['id']}"

arg = sys.argv[1]
update = json.load(open(arg[1:])) if arg.startswith("@") else json.loads(arg)
update.setdefault("update_id", 900000000)
req = urllib.request.Request(url, method="POST", data=json.dumps(update).encode(),
                             headers={"Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": secret})
print(urllib.request.urlopen(req).read().decode()[:200])
