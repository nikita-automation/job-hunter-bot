#!/usr/bin/env python3
"""Последние письма бота: доставлено ли сообщение, кому и есть ли кнопка отправки.

На сервере: ssh n8n-server python3 - [сколько] < scripts/letters.py
"""
import json
import sqlite3
import sys

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 8
db = sqlite3.connect("/root/.n8n/database.sqlite")
ids = [r[0] for r in db.execute(
    "select id from execution_entity where workflowId='cgIAYOj2RrvvWKPC' order by id desc limit ?", (LIMIT,))]

for eid in sorted(ids):
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
    lines = body["text"].split("\n")
    head = lines[0].replace("<b>", "").replace("</b>", "")
    to = next((x for x in lines if x.startswith("📧") or x.startswith("📮")), "")
    btn = [b["text"] for row in body.get("reply_markup", {}).get("inline_keyboard", []) for b in row
           if b["text"].startswith("📤")]
    print(eid, "| доставлено:", resp.get("ok"), "|", head[:90])
    print("      ", to[:70], "|", btn[0] if btn else "БЕЗ кнопки отправки")
