#!/usr/bin/env python3
"""Время каждого узла и JSON первого item указанных узлов в прогоне.

На сервере: ssh n8n-server python3 - <executionId> "Узел 1" "Узел 2" < scripts/node_output.py
"""
import json
import sqlite3
import sys

exec_id, wanted = sys.argv[1], sys.argv[2:]
db = sqlite3.connect("/root/.n8n/database.sqlite")
arr = json.loads(db.execute("select data from execution_data where executionId=?", (exec_id,)).fetchone()[0])
ref = lambda v: arr[int(v)] if isinstance(v, str) and v.isdigit() else v


def deep(v, depth=0):
    v = ref(v)
    if depth > 12:
        return v
    if isinstance(v, dict):
        return {k: deep(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [deep(x, depth + 1) for x in v]
    return v


run = ref(ref(ref(arr[0])["resultData"])["runData"])
for name, idx in run.items():
    r0 = ref(ref(idx)[0])
    print(f"{name:26} {ref(r0.get('executionTime'))} ms")
for name in wanted:
    r0 = ref(ref(run[name])[0])
    main = ref(ref(r0["data"])["main"])
    first = ref(ref(main[0])[0]) if main and ref(main[0]) else None
    print(f"\n=== {name}")
    print(json.dumps(deep(first["json"]) if first else None, ensure_ascii=False, indent=1)[:6000])
