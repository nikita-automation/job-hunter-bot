#!/usr/bin/env python3
"""Собирает n8n-воркфлоу «Job Hunter: Suche» (n8n/ или, с --public, workflows/).

Воркфлоу правится здесь, а не руками в n8n: генератор даёт узлам стабильные UUID
и держит весь JS-код рядом с остальным проектом.
"""
import json
import uuid

from common import (CHAT_ID, CRED_OPENAI, NO_KEEPALIVE, OUT_DIR, PLZ, PROFILE, SEARCH_WEBHOOK_PATH,
                    TABLE_SEEN, TG_API)

OUT = OUT_DIR / "job-hunter-search.json"
NS = uuid.UUID("6f1c2b7e-3d4a-4c1e-9a55-0b8d2f6e7a10")

nodes, connections = [], {}


def node(name, type_, version, params, pos, **extra):
    n = {
        "id": str(uuid.uuid5(NS, name)),
        "name": name,
        "type": type_,
        "typeVersion": version,
        "position": pos,
        "parameters": params,
    }
    n.update(extra)
    nodes.append(n)
    return name


def link(src, dst, out=0):
    outs = connections.setdefault(src, {"main": []})["main"]
    while len(outs) <= out:
        outs.append([])
    outs[out].append({"node": dst, "type": "main", "index": 0})


def code(name, js, pos, mode="runOnceForAllItems", **extra):
    params = {"jsCode": js.strip() + "\n"}
    if mode != "runOnceForAllItems":
        params["mode"] = mode
    return node(name, "n8n-nodes-base.code", 2, params, pos, **extra)


# --- Триггеры ---------------------------------------------------------------

node("Täglich 07:30", "n8n-nodes-base.scheduleTrigger", 1.2,
     {"rule": {"interval": [{"field": "cronExpression", "expression": "30 7 * * *"}]}}, [0, 0])
node("Manuell", "n8n-nodes-base.manualTrigger", 1, {}, [0, 200])
# Ручной запуск снаружи: `n8n execute` из CLI не видит модуль Data Tables,
# поэтому прогон по требованию идёт через этот вебхук (только с сервера, 127.0.0.1).
node("Jetzt suchen (Webhook)", "n8n-nodes-base.webhook", 2,
     {"httpMethod": "POST", "path": SEARCH_WEBHOOK_PATH, "responseMode": "onReceived", "options": {}},
     [0, 400], webhookId=str(uuid.uuid5(NS, "jobhunter-webhook")))

# --- Конфиг и запросы ---------------------------------------------------------

CONFIG_JS = r"""
// Единственное место с настройками поиска.
// Разовый прогон: POST на вебхук с телом {"days": 30, "max_eval": 90, "ignore_seen": true}.
// ignore_seen — прислать заново и уже виденные вакансии (записи в таблице не удаляются);
// seen_since: "2026-09-11T17:39:00+02:00" — «виденными» считать только оценённые после этого момента.
const ov = $input.first().json.body ?? {};
const cfg = {
  plz: '__PLZ__',
  umkreis_km: 40,
  homeoffice_min: 80,    // «удалёнка» = хоумофис от 80% времени
  days: Number(ov.days) || 2,  // вакансии, опубликованные за последние N дней
  ignore_seen: ov.ignore_seen === true,
  seen_since: ov.seen_since ? String(ov.seen_since) : '',
  max_eval: Number(ov.max_eval) || 40,  // потолок оценок ИИ за один прогон
  min_score: 6,          // ниже — в Telegram не присылаем, только в таблицу
  model: 'gpt-4o',
  chat_id: '__CHAT_ID__',
};

// Подобраны прогоном по живому API за 30 дней (2026-09-11). Выкинуты те, что
// приносили в основном мусор: Make.com, Zapier, Quereinsteiger IT, Schnittstellen,
// Integration/Automation Engineer (промышленность и senior), Chatbot.
const keywords = [
  'n8n', 'Prozessautomatisierung', 'KI Automatisierung', 'Workflow Automatisierung',
  'Low-Code', 'No-Code', 'RPA', 'Prozessdigitalisierung', 'Digitalisierung Prozesse',
  'Digitalisierungsmanager', 'KI-Manager', 'KI Prozesse', 'Power Automate',
  'Python Automatisierung', 'Prompt Engineer', 'Business Process Automation',
];

const base = 'https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs';
const qs = (o) => Object.entries(o).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&');

const out = [];
for (const was of keywords) {
  // angebotsart=1 — только работа (без стажировок, Ausbildung и самозанятости)
  const common = { was, angebotsart: 1, veroeffentlichtseit: cfg.days, size: 50, page: 1 };
  out.push({ json: { cfg, was, mode: 'local',
    url: base + '?' + qs({ ...common, wo: cfg.plz, umkreis: cfg.umkreis_km }) } });
  out.push({ json: { cfg, was, mode: 'remote',
    url: base + '?' + qs({ ...common, homeoffice: 'prozentual_' + cfg.homeoffice_min }) } });
}
return out;
""".replace("__CHAT_ID__", CHAT_ID).replace("__PLZ__", PLZ)
code("Config & Suchen", CONFIG_JS, [220, 100])

node("BA Suche", "n8n-nodes-base.httpRequest", 4.2, {
    "url": "={{ $json.url }}",
    "sendHeaders": True,
    "headerParameters": {"parameters": [
        {"name": "X-API-Key", "value": "jobboerse-jobsuche"},
        {"name": "User-Agent", "value": "Mozilla/5.0 (NikitaJobs)"},
    ]},
    "options": {"timeout": 20000, "batching": {"batch": {"batchSize": 5, "batchInterval": 500}}},
}, [440, 100], retryOnFail=True, maxTries=3, waitBetweenTries=2000, onError="continueRegularOutput")

COLLECT_JS = r"""
// Склеиваем ответы всех запросов, убираем повторы и явно не наши профессии.
const queries = $('Config & Suchen').all();
const cfg = queries[0].json.cfg;

// Отсев по заголовку: промышленная автоматизация, учёба, руководящие роли.
const EXCLUDE = /(elektron|elektrik|mechatron|instandhalt|\bsps\b|schlosser|monteur|servicetechn|techniker|embedded|hardware|werkstudent|praktik|ausbildung|dual|abschlussarbeit|thesis|minijob|fahrer|lager|pflege|erzieher|verkäufer|recruiter|personalberater|senior|lead\b|head of|leiter|principal|director|chief)/i;

const byRef = new Map();
let raw = 0, excluded = 0;
$input.all().forEach((it, i) => {
  const mode = queries[i]?.json.mode ?? 'local';
  for (const j of it.json.ergebnisliste ?? []) {
    raw++;
    const refnr = j.referenznummer;
    if (!refnr) continue;
    const title = j.stellenangebotsTitel ?? '';
    if (EXCLUDE.test(title)) { excluded++; continue; }
    const prev = byRef.get(refnr);
    if (prev) { if (mode === 'remote') prev.mode = 'remote'; continue; }
    byRef.set(refnr, {
      refnr, title, mode,
      firma: j.firma ?? '',
      ort: (j.stellenlokationen ?? [])[0]?.adresse?.ort ?? '',
      published: j.datumErsteVeroeffentlichung ?? '',
    });
  }
});

const jobs = [...byRef.values()];
return [{ json: { cfg, jobs, stats: { raw, excluded, unique: jobs.length } } }];
"""
code("Sammeln & Vorfiltern", COLLECT_JS, [660, 100])

node("Bekannte Stellen", "n8n-nodes-base.dataTable", 1.1, {
    "operation": "get",
    "dataTableId": {"__rl": True, "mode": "id", "value": TABLE_SEEN},
    "returnAll": True,
    "options": {},
}, [880, 100], alwaysOutputData=True, executeOnce=True)

NEW_JS = r"""
// Оставляем только вакансии, которых ещё нет в таблице job_seen.
const { cfg, jobs, stats } = $('Sammeln & Vorfiltern').first().json;
const since = cfg.seen_since ? new Date(cfg.seen_since).getTime() : 0;
const seen = new Set(cfg.ignore_seen ? [] : $input.all()
  .filter((it) => !since || new Date(it.json.found_at).getTime() >= since)
  .map((it) => it.json.refnr).filter(Boolean));
const fresh = jobs.filter((j) => !seen.has(j.refnr));

// Удалёнку показываем первой, потом — самое свежее.
fresh.sort((a, b) => (a.mode === b.mode ? b.published.localeCompare(a.published) : a.mode === 'remote' ? -1 : 1));
const picked = fresh.slice(0, cfg.max_eval);
const summary = { ...stats, fresh: fresh.length, evaluated: picked.length };

if (!picked.length) return [{ json: { none: true, cfg, summary } }];

const b64 = (s) => (typeof btoa === 'function' ? btoa(s) : Buffer.from(s).toString('base64'));
return picked.map((j) => ({ json: { ...j, refnr_b64: b64(j.refnr), cfg, summary } }));
"""
code("Nur neue", NEW_JS, [1100, 100])

node("Gibt es neue?", "n8n-nodes-base.if", 2.2, {
    "conditions": {
        "options": {"caseSensitive": True, "version": 2, "typeValidation": "loose"},
        "combinator": "and",
        "conditions": [{"id": "hasnew", "operator": {"type": "boolean", "operation": "notEquals"},
                        "leftValue": "={{ $json.none === true }}", "rightValue": True}],
    },
    "looseTypeValidation": True,
    "options": {},
}, [1320, 100])

code("Keine neuen", r"""
const { cfg, summary } = $input.first().json;
return [{ json: { method: 'sendMessage', body: { chat_id: cfg.chat_id,
  text: `🔎 Сегодня новых вакансий нет.\nПросмотрено: ${summary.raw}, после фильтра: ${summary.unique}.` } } }];
""", [1540, 300])

node("BA Details", "n8n-nodes-base.httpRequest", 4.2, {
    "url": "=https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/{{ $json.refnr_b64 }}",
    "sendHeaders": True,
    "headerParameters": {"parameters": [
        {"name": "X-API-Key", "value": "jobboerse-jobsuche"},
        {"name": "User-Agent", "value": "Mozilla/5.0 (NikitaJobs)"},
    ]},
    "options": {"timeout": 20000, "batching": {"batch": {"batchSize": 5, "batchInterval": 500}}},
}, [1540, 0], retryOnFail=True, maxTries=3, waitBetweenTries=2000, onError="continueRegularOutput")


PROMPT_JS = r"""
// Готовим запрос к модели: профиль кандидата + полный текст вакансии.
const job = $('Nur neue').item.json;
const d = $json ?? {};
const text = String(d.stellenangebotsBeschreibung ?? '').slice(0, 5000);
const url = d.externeURL || `https://www.arbeitsagentur.de/jobsuche/jobdetail/${job.refnr}`;
const salary = d.gehaltsspanneVon ? `${d.gehaltsspanneVon}–${d.gehaltsspanneBis ?? '?'} € (${d.verguetungsangabe ?? ''})` : '';
const email = (text.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i) ?? [null])[0];

const profile = `__PROFILE__`;

const system = `Du bewertest Stellenanzeigen für genau einen Kandidaten. Die Frage ist: Lohnt sich eine Bewerbung — hat er eine realistische Chance auf ein Vorstellungsgespräch? Nicht: erfüllt er jede Anforderung. Deutsche Anzeigen sind Wunschlisten; Formulierungen wie "idealerweise", "wünschenswert", "oder vergleichbar" sind keine Ausschlussgründe.
Ehrlich bleiben: hohe Punktzahl nur, wenn der Kern der Arbeit Automatisierung, Workflows, Integrationen, Digitalisierung von Prozessen oder KI-Einsatz ist.
Skala: 9-10 = passt fast perfekt; 7-8 = gute Chance, kleine Lücken; 5-6 = möglich, Bewerbung lohnt sich mit gutem Anschreiben; 0-4 = passt nicht (anderes Fachgebiet wie Buchhaltung/Vertrieb/Technik, Industrieautomatisierung/SPS, harte Pflicht zu mehrjähriger Berufserfahrung oder Informatikstudium, verhandlungssicheres Englisch als Arbeitssprache).
Antworte NUR mit JSON:
{"score": 0-10, "fit_ru": "1-2 Sätze auf RUSSISCH: warum passt es oder nicht", "pros": ["bis zu 3 kurze Punkte auf Russisch"], "cons": ["bis zu 3 kurze Punkte auf Russisch"], "english_required": true|false, "remote": "vollständig|teilweise|nein|unklar"}`;

const user = `KANDIDAT:\n${profile}\n\nSTELLE:\nTitel: ${job.title}\nFirma: ${job.firma}\nOrt: ${job.ort}\n${salary ? 'Gehalt: ' + salary + '\n' : ''}Arbeitnehmerüberlassung: ${d.istArbeitnehmerUeberlassung ? 'ja' : 'nein'}\n\n${text || '(keine Beschreibung verfügbar – bewerte nur nach Titel, vorsichtig)'}`;

return {
  json: {
    job: { ...job, url, salary, email, zeitarbeit: !!d.istArbeitnehmerUeberlassung, has_text: !!text },
    openai_request: {
      model: job.cfg.model,
      temperature: 0.2,
      max_tokens: 500,
      response_format: { type: 'json_object' },
      messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
    },
  },
};
""".replace("__PROFILE__", PROFILE.replace("`", "'"))
code("Prompt bauen", PROMPT_JS, [1760, 0], mode="runOnceForEachItem")

node("KI-Bewertung", "n8n-nodes-base.httpRequest", 4.2, {
    "method": "POST",
    "url": "https://api.openai.com/v1/chat/completions",
    "authentication": "predefinedCredentialType",
    "nodeCredentialType": "openAiApi",
    "sendBody": True,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify($json.openai_request) }}",
    "options": {"timeout": 40000, "batching": {"batch": {"batchSize": 1, "batchInterval": 10000}}},  # лимит OpenAI 30k TPM: ~3.5k токенов × 6/мин
}, [1980, 0], credentials=CRED_OPENAI, retryOnFail=True, maxTries=3, waitBetweenTries=5000,
   onError="continueRegularOutput")

EVAL_JS = r"""
// Разбираем ответ модели. Если модель упала или прислала мусор — score = -1,
// вакансия всё равно попадает в таблицу, чтобы не оценивать её каждый день заново.
const job = $('Prompt bauen').item.json.job;
let r = {};
try { r = JSON.parse($json.choices?.[0]?.message?.content ?? '{}'); } catch (e) { r = {}; }
const score = Number.isFinite(Number(r.score)) ? Math.max(0, Math.min(10, Math.round(Number(r.score)))) : -1;
return {
  json: {
    ...job,
    score,
    fit_ru: String(r.fit_ru ?? (score < 0 ? 'Оценка не удалась' : '')),
    pros: Array.isArray(r.pros) ? r.pros.slice(0, 3).map(String) : [],
    cons: Array.isArray(r.cons) ? r.cons.slice(0, 3).map(String) : [],
    english_required: r.english_required === true,
    remote: String(r.remote ?? 'unklar'),
  },
};
"""
code("Auswerten", EVAL_JS, [2200, 0], mode="runOnceForEachItem")

node("Merken", "n8n-nodes-base.dataTable", 1.1, {
    "operation": "insert",
    "dataTableId": {"__rl": True, "mode": "id", "value": TABLE_SEEN},
    "columns": {"mappingMode": "defineBelow", "value": {
        "refnr": "={{ $json.refnr }}",
        "title": "={{ $json.title }}",
        "firma": "={{ $json.firma }}",
        "ort": "={{ $json.ort }}",
        "mode": "={{ $json.mode }}",
        "score": "={{ $json.score }}",
        "status": "={{ $json.score >= $json.cfg.min_score ? 'sent' : 'skipped' }}",
        "url": "={{ $json.url }}",
        "found_at": "={{ $now.toISO() }}",
        "fit_ru": "={{ $json.fit_ru }}",
    }},
    "options": {},
}, [2640, 200])

# Неудачные оценки (score = -1) не запоминаем — завтра вакансия будет оценена снова.
node("Nur bewertete", "n8n-nodes-base.filter", 2.2, {
    "conditions": {
        "options": {"caseSensitive": True, "version": 2, "typeValidation": "loose"},
        "combinator": "and",
        "conditions": [{"id": "scored", "operator": {"type": "number", "operation": "gte"},
                        "leftValue": "={{ $json.score }}", "rightValue": 0}],
    },
    "looseTypeValidation": True,
    "options": {},
}, [2420, 200])

CARDS_JS = r"""
// Сводка первым сообщением (с коротким списком «возможно» на 5/10),
// затем карточки подходящих вакансий с кнопками — от лучших к худшим.
const all = $input.all().map((it) => it.json);
const cfg = all[0].cfg;
const s = all[0].summary;
const esc = (t) => String(t ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const good = all.filter((j) => j.score >= cfg.min_score).sort((a, b) => b.score - a.score);
const maybe = all.filter((j) => j.score === cfg.min_score - 1);
const failed = all.filter((j) => j.score < 0).length;

const head = [
  `🔎 <b>Вакансии на ${$now.setZone('Europe/Berlin').toFormat('dd.MM')}</b>`,
  `Найдено ${s.raw}, после фильтра ${s.unique}, новых ${s.fresh}, оценено ${s.evaluated}.`,
  good.length ? `Подходящих (≥${cfg.min_score}/10): <b>${good.length}</b> 👇` : `Подходящих (≥${cfg.min_score}/10) сегодня нет.`,
  failed ? `⚠️ Не удалось оценить: ${failed} — попробую завтра снова.` : '',
  s.fresh > s.evaluated ? `ℹ️ Ещё ${s.fresh - s.evaluated} новых отложено на завтра (лимит ${cfg.max_eval}).` : '',
  maybe.length ? `\n🤔 <b>Возможно (${cfg.min_score - 1}/10):</b>\n` + maybe.slice(0, 10).map((j) =>
    `• <a href="${esc(j.url)}">${esc(j.title)}</a> — ${esc(j.firma)}${j.mode === 'remote' ? ' · 🏠' : ''}`).join('\n') : '',
].filter(Boolean).join('\n');

const dot = (n) => (n >= 8 ? '🟢' : n >= 7 ? '🟡' : '🟠');
const place = (j) => (j.mode === 'remote' || j.remote === 'vollständig' ? '🏠 удалёнка' : j.remote === 'teilweise' ? '🏠 частично удалённо' : '📍 на месте');

const msg = (text, reply_markup) => ({ json: { method: 'sendMessage', body: {
  chat_id: cfg.chat_id, text: text.slice(0, 4000), parse_mode: 'HTML',
  disable_web_page_preview: true, ...(reply_markup ? { reply_markup } : {}) } } });

const cards = good.map((j) => msg([
  `${dot(j.score)} <b>${j.score}/10 · ${esc(j.title)}</b>`,
  `🏢 ${esc(j.firma)} · ${esc(j.ort)} · ${place(j)}`,
  j.salary ? `💶 ${esc(j.salary)}` : '',
  '',
  `💬 ${esc(j.fit_ru)}`,
  ...j.pros.map((p) => `✅ ${esc(p)}`),
  ...j.cons.map((c) => `⚠️ ${esc(c)}`),
  j.english_required ? '🇬🇧 нужен английский' : '',
  j.zeitarbeit ? '🔁 Zeitarbeit (через кадровое агентство)' : '',
  j.email ? '📧 в тексте есть e-mail для отклика' : '',
].join('\n').replace(/\n{3,}/g, '\n\n'), { inline_keyboard: [
  [{ text: '✍️ Написать Anschreiben', callback_data: 'w:' + j.refnr }],
  [{ text: '🔗 Вакансия', url: j.url }, { text: '❌ Не интересно', callback_data: 'x:' + j.refnr }],
] }));

return [msg(head), ...cards];
"""
code("Karten bauen", CARDS_JS, [2420, -100])

node("An Telegram", "n8n-nodes-base.httpRequest", 4.2, {
    "method": "POST",
    "url": "=" + TG_API + "{{ $json.method }}",
    "sendHeaders": True,
    "headerParameters": NO_KEEPALIVE,
    "sendBody": True,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify($json.body) }}",
    "options": {"timeout": 25000, "batching": {"batch": {"batchSize": 1, "batchInterval": 1100}}},  # Telegram: ≤1 сообщ./с в чат
}, [2640, -100], retryOnFail=True, maxTries=4, waitBetweenTries=3000, onError="continueRegularOutput")

# --- Связи ------------------------------------------------------------------

link("Täglich 07:30", "Config & Suchen")
link("Manuell", "Config & Suchen")
link("Jetzt suchen (Webhook)", "Config & Suchen")
link("Config & Suchen", "BA Suche")
link("BA Suche", "Sammeln & Vorfiltern")
link("Sammeln & Vorfiltern", "Bekannte Stellen")
link("Bekannte Stellen", "Nur neue")
link("Nur neue", "Gibt es neue?")
link("Gibt es neue?", "BA Details", 0)
link("Gibt es neue?", "Keine neuen", 1)
link("Keine neuen", "An Telegram")
link("BA Details", "Prompt bauen")
link("Prompt bauen", "KI-Bewertung")
link("KI-Bewertung", "Auswerten")
link("Auswerten", "Nur bewertete")
link("Nur bewertete", "Merken")
link("Auswerten", "Karten bauen")
link("Karten bauen", "An Telegram")

workflow = {
    "name": "Job Hunter: Suche",
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v1", "timezone": "Europe/Berlin"},
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(workflow, ensure_ascii=False, indent=2))
print(f"OK: {OUT} ({len(nodes)} узлов)")
