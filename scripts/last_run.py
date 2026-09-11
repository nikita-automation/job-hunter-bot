#!/usr/bin/env python3
"""Печатает последний прогон воркфлоу: статус и число items/ошибку по каждому узлу.

Запускается НА СЕРВЕРЕ: ssh n8n-server python3 - [workflowId] [--wait] < scripts/last_run.py
Данные прогонов n8n хранит в execution_data в формате flatted (строка-число = индекс в массиве).
"""
import json
import sqlite3
import sys
import time

WF = next((a for a in sys.argv[1:] if not a.startswith("--")), "IzgDXBQNgWbkPCmt")
db = sqlite3.connect("/root/.n8n/database.sqlite")


def last():
    return db.execute(
        "select id, status, startedAt, stoppedAt from execution_entity "
        "where workflowId=? order by id desc limit 1", (WF,)).fetchone()


row = last()
if "--wait" in sys.argv:
    for _ in range(200):
        if row and row[1] not in ("running", "new", "waiting"):
            break
        time.sleep(3)
        row = last()
print("execution:", row)

arr = json.loads(db.execute("select data from execution_data where executionId=?", (row[0],)).fetchone()[0])
ref = lambda v: arr[int(v)] if isinstance(v, str) and v.isdigit() else v
rr = ref(ref(arr[0])["resultData"])
for name, idx in ref(rr["runData"]).items():
    r0 = ref(ref(idx)[0])
    try:
        main = ref(ref(r0["data"])["main"])
        cnt = [len(ref(o)) if o is not None else 0 for o in main]
    except Exception:
        cnt = "n/a"
    err = ref(r0["error"]) if r0.get("error") else None
    msg = ref(err.get("message")) if isinstance(err, dict) else err
    print(f"  {name:26} items={cnt} {'ERR: ' + str(msg)[:300] if err else ''}")
if rr.get("error"):
    e = ref(rr["error"])
    print("RUN ERROR:", ref(e.get("message")) if isinstance(e, dict) else e)
