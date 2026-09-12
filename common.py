"""Общее для обоих генераторов: сборщик узлов и настройки из private.py.

`--public` собирает воркфлоу с заглушками из private_example.py в workflows/ —
это то, что лежит в репозитории. Боевая сборка идёт в n8n/ (в .gitignore).
"""
import json
import sys
import uuid
from pathlib import Path

PUBLIC = "--public" in sys.argv
if PUBLIC:
    import private_example as P
else:
    try:
        import private as P
    except ImportError:
        sys.exit("Нет private.py — скопируй private_example.py в private.py и заполни.")

OUT_DIR = Path(__file__).parent / ("workflows" if PUBLIC else "n8n")

CRED_OPENAI = {"openAiApi": {"id": P.CRED_OPENAI_ID, "name": "OpenAi account"}}
CRED_TG = {"telegramApi": {"id": P.CRED_TG_ID, "name": "NikitaJobs Bot"}}
CRED_SMTP = {"smtp": {"id": P.CRED_SMTP_ID, "name": "Gmail SMTP (Nikita)"}}
TABLE_SEEN, TABLE_APPLIED, TABLE_EVENTS = P.TABLE_SEEN, P.TABLE_APPLIED, P.TABLE_EVENTS
CHAT_ID, PLZ, CV_PATH, MAIL_FROM = P.CHAT_ID, P.PLZ, P.CV_PATH, P.MAIL_FROM
SEARCH_WEBHOOK_PATH = P.SEARCH_WEBHOOK_PATH
SEARCH_WEBHOOK = "http://127.0.0.1:5678/webhook/" + SEARCH_WEBHOOK_PATH
PROFILE, SIGNATURE = P.PROFILE, P.SIGNATURE

# Токен бота в репозиторий не попадает: deploy.py подставляет его на сервере
# из /root/nikitajobs-bot-token.txt. Telegram-узел n8n не умеет динамические
# inline-кнопки, поэтому сообщения с кнопками шлём HTTP-запросом к Bot API.
TG_API = "https://api.telegram.org/bot__TG_TOKEN__/"

# n8n держит соединения с api.telegram.org в пуле keep-alive, а через несколько минут
# простоя они молча умирают: первый запрос прогона висел до таймаута (замер 2026-09-11:
# 30 000 мс против 70 мс у второго). `Connection: close` не даёт сокету попасть в пул.
NO_KEEPALIVE = {"parameters": [{"name": "Connection", "value": "close"}]}




def js_str(s):
    """Строка Python → JS-литерал (для вставки в jsCode)."""
    return json.dumps(s, ensure_ascii=False)


class Workflow:
    def __init__(self, name, ns):
        self.name = name
        self.ns = uuid.UUID(ns)
        self.nodes, self.connections = [], {}

    def uid(self, key):
        return str(uuid.uuid5(self.ns, key))

    def node(self, name, type_, version, params, pos, **extra):
        n = {"id": self.uid(name), "name": name, "type": type_, "typeVersion": version,
             "position": pos, "parameters": params}
        n.update(extra)
        self.nodes.append(n)
        return name

    def code(self, name, js, pos, mode="runOnceForAllItems", **extra):
        params = {"jsCode": js.strip() + "\n"}
        if mode != "runOnceForAllItems":
            params["mode"] = mode
        return self.node(name, "n8n-nodes-base.code", 2, params, pos, **extra)

    def tg(self, name, pos, batching=None, **extra):
        """HTTP-узел Bot API: вход — {method, body}."""
        options = {"timeout": 25000}  # Telegram изредка отвечает дольше 8 с — на 8000 терялись сообщения
        if batching:
            options["batching"] = {"batch": batching}
        return self.node(name, "n8n-nodes-base.httpRequest", 4.2, {
            "method": "POST",
            "url": "=" + TG_API + "{{ $json.method }}",
            "sendHeaders": True,
            "headerParameters": NO_KEEPALIVE,
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.body) }}",
            "options": options,
        }, pos, retryOnFail=True, maxTries=3, waitBetweenTries=1000, onError="continueRegularOutput", **extra)

    def link(self, src, dst, out=0):
        outs = self.connections.setdefault(src, {"main": []})["main"]
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": dst, "type": "main", "index": 0})

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        wf = {"name": self.name, "nodes": self.nodes, "connections": self.connections,
              "settings": {"executionOrder": "v1", "timezone": "Europe/Berlin"}}
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2))
        print(f"OK: {path} ({len(self.nodes)} узлов)")
