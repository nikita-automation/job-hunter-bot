#!/usr/bin/env python3
"""Заливает n8n/*.json на VPS через public API n8n (upsert по имени).

Запуск с мака:  python3 deploy.py [--activate] [search] [actions]
Без имён заливаются все n8n/*.json; `actions` — только job-hunter-actions.json и т. п.
Скрипт копирует JSON на сервер и выполняет там запрос к 127.0.0.1:5678,
ключ API читается на сервере из базы n8n и в вывод не попадает.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ACTIVATE = "--activate" in sys.argv
ONLY = [a for a in sys.argv[1:] if not a.startswith("--")]

REMOTE = r'''
import json, socket, sqlite3, sys, urllib.error, urllib.request
key = sqlite3.connect("/root/.n8n/database.sqlite").execute(
    "select apiKey from user_api_keys where label='Caude code'").fetchone()[0]
def api(method, path, body=None, tries=1):
    for attempt in range(1, tries + 1):
        req = urllib.request.Request("http://127.0.0.1:5678/api/v1" + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-N8N-API-KEY": key, "Content-Type": "application/json"})
        try:
            return json.load(urllib.request.urlopen(req, timeout=180))
        except urllib.error.HTTPError as e:
            msg = f"HTTP {e.code} {method} {path}: {e.read().decode()[:600]}"
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            msg = f"{method} {path}: {e}"
        if attempt == tries:
            sys.exit(msg)
        print(msg, "- повтор", flush=True)
raw = open(sys.argv[1]).read()
token = open("/root/nikitajobs-bot-token.txt").read().strip()
wf = json.loads(raw.replace("__TG_TOKEN__", token))
body = {k: wf[k] for k in ("name", "nodes", "connections", "settings")}
found = [w for w in api("GET", "/workflows?limit=250")["data"] if w["name"] == wf["name"]]
if found:
    wid = found[0]["id"]; api("PUT", f"/workflows/{wid}", body, tries=2); print("updated", wid, flush=True)
else:
    wid = api("POST", "/workflows", body)["id"]; print("created", wid, flush=True)
if sys.argv[2] == "1":
    # Telegram Trigger при включении регистрирует вебхук через пул соединений n8n,
    # и первая попытка иногда висит на «мёртвом» keep-alive — повтор проходит.
    api("POST", f"/workflows/{wid}/activate", tries=3)
    print("active:", api("GET", f"/workflows/{wid}")["active"], flush=True)
'''

SSH_OPTS = ["-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]

for f in sorted((ROOT / "n8n").glob("*.json")):
    if ONLY and not any(o in f.stem for o in ONLY):
        continue
    dest = f"/tmp/{f.name}"
    subprocess.run(["scp", "-q", *SSH_OPTS, str(f), f"n8n-server:{dest}"], check=True, timeout=120)
    subprocess.run(["ssh", *SSH_OPTS, "n8n-server", "python3", "-", dest, "1" if ACTIVATE else "0"],
                   input=REMOTE.encode(), check=True, timeout=900)
