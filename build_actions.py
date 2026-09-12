#!/usr/bin/env python3
"""Собирает n8n-воркфлоу «Job Hunter: Aktionen» (n8n/ или, с --public, workflows/).

Всё, что Никита делает в боте @NikitaJobs_bot:
  ✍️ w:<refnr>  написать Anschreiben         🔄 r:<refnr>  другой вариант
  ❌ x:<refnr>  не интересно                  ✅ a:<refnr>  «я откликнулся» (через портал)
  📤 s:<refnr>  отправить → подтверждение → S:<refnr> реальная отправка (c:<refnr> отмена)
  ответ (reply) на письмо → переписать с учётом пожелания
  /bewerbungen — журнал откликов, /suche — поиск прямо сейчас

Письмо уходит ТОЛЬКО после двух нажатий Никиты (📤 и «Да, отправить»).
"""
from common import (CHAT_ID, CRED_OPENAI, CRED_SMTP, CRED_TG, CV_PATH, MAIL_FROM, OUT_DIR, PROFILE,
                    SEARCH_WEBHOOK, SIGNATURE, TABLE_APPLIED, TABLE_EVENTS, TABLE_SEEN, Workflow, js_str)

OUT = OUT_DIR / "job-hunter-actions.json"
wf = Workflow("Job Hunter: Aktionen", "0d7a4e2c-8b1f-4f3a-a9c6-5e2b7d1c9f44")

BA_HEADERS = {"parameters": [
    {"name": "X-API-Key", "value": "jobboerse-jobsuche"},
    {"name": "User-Agent", "value": "Mozilla/5.0 (NikitaJobs)"},
]}

# Общие JS-помощники, вставляются в Code-узлы.
HELPERS = r"""
const R = $('Route').first().json;
const esc = (t) => String(t ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const send = (text, extra = {}) => ({ json: { method: 'sendMessage', body: {
  chat_id: R.chat_id, text: String(text).slice(0, 4000), parse_mode: 'HTML', disable_web_page_preview: true, ...extra } } });
const setKb = (inline_keyboard) => ({ json: { method: 'editMessageReplyMarkup', body: {
  chat_id: R.chat_id, message_id: R.msg_id, reply_markup: { inline_keyboard } } } });
const letterKb = (refnr, email, url) => [
  ...(email ? [[{ text: '📤 Отправить на ' + email, callback_data: 's:' + refnr }]] : []),
  [{ text: '🔄 Другой вариант', callback_data: 'r:' + refnr }, { text: '✅ Я откликнулся', callback_data: 'a:' + refnr }],
  ...(url ? [[{ text: '🔗 Вакансия', url }]] : []),
];
"""


def jsnode(name, body, pos, **kw):
    return wf.code(name, HELPERS + body, pos, **kw)


def table_get(name, table, key_expr, pos, return_all=False, key="refnr"):
    params = {"operation": "get", "dataTableId": {"__rl": True, "mode": "id", "value": table}, "options": {}}
    if key_expr:
        params.update({"filters": {"conditions": [{"keyName": key, "condition": "eq", "keyValue": key_expr}]},
                       "matchType": "allConditions"})
    params.update({"returnAll": True} if return_all else {"returnAll": False, "limit": 1})
    return wf.node(name, "n8n-nodes-base.dataTable", 1.1, params, pos, alwaysOutputData=True, executeOnce=True)


# --- Вход и маршрутизация -----------------------------------------------------

wf.node("Telegram", "n8n-nodes-base.telegramTrigger", 1.2,
        {"updates": ["message", "callback_query"], "additionalFields": {}},
        [0, 400], credentials=CRED_TG, webhookId=wf.uid("tg-webhook"))

ROUTE_JS = r"""
// Входящий апдейт Telegram → одно действие. Чужие чаты игнорируем.
const OWNER = '__CHAT_ID__';
const u = $input.first().json;
const b64 = (s) => (typeof btoa === 'function' ? btoa(s) : Buffer.from(s).toString('base64'));
const urlOf = (m) => ((m?.reply_markup?.inline_keyboard ?? []).flat().find((b) => b.url) ?? {}).url ?? '';
let r = { action: 'ignore' };

if (u.callback_query) {
  const cq = u.callback_query;
  const data = String(cq.data ?? '');
  const i = data.indexOf(':');
  const k = i > 0 ? data.slice(0, i) : data;
  const map = { w: 'write', r: 'write', x: 'reject', s: 'confirm_send', S: 'send', c: 'cancel_send', a: 'applied' };
  const tips = { write: '✍️ Пишу письмо, ~20 секунд…', reject: 'Скрыл', confirm_send: 'Проверь адрес и подтверди',
                 send: '📤 Отправляю…', cancel_send: 'Отменено', applied: '✅ Записал в отклики' };
  r = { action: map[k] ?? 'noop', rewrite: k === 'r', refnr: i > 0 ? data.slice(i + 1) : '',
        chat_id: String(cq.message?.chat?.id ?? ''), msg_id: cq.message?.message_id,
        msg_text: cq.message?.text ?? '', url: urlOf(cq.message), cb_id: cq.id, cb_text: tips[map[k]] ?? '' };
} else if (u.message) {
  const m = u.message;
  const text = String(m.text ?? '').trim();
  const rep = m.reply_to_message;
  const ref = ((rep?.text ?? '').match(/#ref\s+(\S+)/) ?? [])[1];
  const base = { chat_id: String(m.chat?.id ?? '') };
  if (ref && text) r = { ...base, action: 'write', refnr: ref, instruction: text, msg_text: rep.text, url: urlOf(rep) };
  else if (/^\/bewerbungen/i.test(text)) r = { ...base, action: 'list' };
  else if (/^\/suche/i.test(text)) r = { ...base, action: 'search' };
  else r = { ...base, action: 'help' };
}

if (r.chat_id !== OWNER) r = { action: 'ignore' };
// Telegram повторяет апдейт, если воркфлоу отвечает слишком долго — иначе письмо уходит дважды.
r.event_key = String(u.update_id ?? '') + ':' + String(u.callback_query?.id ?? u.message?.message_id ?? '');
if (r.refnr) r.refnr_b64 = b64(r.refnr);

// Данные письма берём из текста уже присланного сообщения с Anschreiben.
const t = r.msg_text ?? '';
r.to = (t.match(/Кому:\s*(\S+@\S+)/) ?? [])[1] ?? '';
r.subject = ((t.match(/Betreff:\s*(.+)/) ?? [])[1] ?? '').trim();
const parts = t.split('———');
r.letter = parts.length >= 3 ? parts[1].trim() : '';
const head = t.split('\n')[0] ?? '';
const hm = head.match(/Anschreiben · (.+) — (.+)$/);
r.head_title = ((hm ?? head.match(/\d+\/10 · (.+)$/) ?? [])[1] ?? '').trim();
r.head_firma = (hm?.[2] ?? '').trim();
return [{ json: r }];
""".replace("__CHAT_ID__", CHAT_ID)
wf.code("Route", ROUTE_JS, [220, 400])

wf.code("Quittung", r"""
// Ответ на нажатие кнопки, иначе у Никиты крутятся «часики».
const r = $input.first().json;
if (!r.cb_id) return [];
return [{ json: { method: 'answerCallbackQuery', body: { callback_query_id: r.cb_id, text: r.cb_text } } }];
""", [440, 160])
wf.tg("Callback beantworten", [660, 160])

table_get("Ereignis prüfen", TABLE_EVENTS, "={{ $('Route').first().json.event_key }}", [440, 560], key="event_key")

jsnode("Nur einmal", r"""
// Повтор того же апдейта (Telegram шлёт его снова, если ответа долго нет) — дальше не пускаем.
const seen = $input.all().some((it) => it.json.event_key === R.event_key);
if (seen || R.action === 'ignore') return [];
return [{ json: { event_key: R.event_key, ts: $now.toISO() } }];
""", [660, 560])

wf.node("Ereignis merken", "n8n-nodes-base.dataTable", 1.1, {
    "operation": "insert", "dataTableId": {"__rl": True, "mode": "id", "value": TABLE_EVENTS},
    "columns": {"mappingMode": "defineBelow", "value": {
        "event_key": "={{ $json.event_key }}", "ts": "={{ $json.ts }}"}},
    "options": {},
}, [880, 560])

ACTIONS = ["write", "reject", "confirm_send", "send", "cancel_send", "applied", "list", "search", "help"]
wf.node("Aktion", "n8n-nodes-base.switch", 3.2, {"rules": {"values": [
    {"conditions": {"options": {"caseSensitive": True, "version": 2, "typeValidation": "strict"},
                    "combinator": "and",
                    "conditions": [{"id": a, "operator": {"type": "string", "operation": "equals"},
                                    "leftValue": "={{ $('Route').first().json.action }}", "rightValue": a}]},
     "renameOutput": True, "outputKey": a} for a in ACTIONS]}, "options": {}}, [440, 400])

# Один общий выход в Bot API для всех веток.
wf.tg("Telegram API", [2860, 400])

# --- ✍️ Anschreiben -------------------------------------------------------------

wf.node("Stelle (Details)", "n8n-nodes-base.httpRequest", 4.2, {
    # $json здесь — запись о событии из дедупликации, поэтому идентификатор берём у Route.
    "url": "=https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/{{ $('Route').first().json.refnr_b64 }}",
    "sendHeaders": True, "headerParameters": BA_HEADERS, "options": {"timeout": 20000},
}, [660, -200], retryOnFail=True, maxTries=4, waitBetweenTries=4000, onError="continueRegularOutput")

LETTER_SYSTEM = """Du schreibst das Anschreiben für eine E-Mail-Bewerbung auf Deutsch — in der Stimme des Kandidaten, nicht in der eines Sprachmodells.

MASSSTAB: Ein Personaler soll nicht auf den Gedanken kommen, dass eine KI das geschrieben hat. Lieber schlicht und konkret als rund und werbend.

VERBOTEN
- Marketingsprache der Anzeige zurückspiegeln ("die Digitalisierung aktiv mitgestalten", "innovatives Umfeld", "dynamisches Team").
- Floskeln: "hiermit bewerbe ich mich", "mit großem Interesse", "teamfähig und motiviert", "reizt mich besonders", "Vielen Dank für die Berücksichtigung", "Ich freue mich darauf, meine Kenntnisse einzubringen".
- Leersätze, die nur eine Erfahrung bewerten ("Diese Erfahrung hat mir gezeigt, wie wichtig …"). Jeder Satz muss eine neue Information tragen.
- Adjektiv-Inflation ("äußerst spannend", "erheblich", "maßgeschneidert", "umfangreich") und Nominalstil ("die Durchführung der Optimierung"). Nimm Verben.
- Behauptungen ohne Beleg ("ich arbeite strukturiert"). Stattdessen etwas nennen, woraus das folgt.
- Die Gastronomie-Vergangenheit des Kandidaten erwähnen (ausdrücklicher Wunsch).

PFLICHT
- Höchstens 200 Wörter, drei Absätze. Einer der Absätze ist höchstens zwei Zeilen lang.
- Ungleichmäßiger Rhythmus: mindestens ein Satz unter acht Wörtern, kein Absatz aus lauter gleich langen Sätzen.
- Mindestens zwei konkrete Angaben aus dem Profil: Zahl, Werkzeug, Zeitraum oder Ergebnis (z. B. "~70 Belege pro Tag", "seit Juni 2026", "n8n auf eigenem VPS").
- Genau ein ehrlicher Satz zu einer Lücke, wenn die Anzeige etwas verlangt, was im Profil fehlt — sachlich, ohne Entschuldigung, mit dem, was stattdessen da ist ("Mit der Power Platform habe ich nicht gearbeitet; dieselben Abläufe habe ich in n8n gebaut.").
- Schluss: ein konkreter nächster Schritt ("Ab sofort verfügbar, ein Gespräch geht auch kurzfristig."), keine Dankesformel.
- Nur Fakten aus dem Profil. Nichts erfinden: keine Jahre, Tools, Abschlüsse, Kunden oder Zahlen, die dort nicht stehen. Englisch nie besser darstellen als A2.

ERLAUBTE PERSÖNLICHE DETAILS — höchstens eines pro Brief, nur wenn es zur Stelle passt:
- Die tägliche Videoreihe auf Deutsch über Automatisierung, seit über 70 Tagen ohne Lücke.
- Die eigene Video-Pipeline, die er nach einer Kostenrechnung wieder abgeschaltet hat: der manuelle Weg war rund achtmal günstiger. (Zeigt, dass er rechnet, statt Technik um ihrer selbst willen zu bauen.)
- Deutsch von null auf C1 (Zertifikat März 2026, 95 %).
- Bei technischen Stellen: dass diese Bewerbung aus seinem eigenen Job-Bot kommt, der Stellen sucht, bewertet und Anschreiben vorbereitet.

WEITERE REGELN
- Einstieg: ein konkreter Bezug auf die Aufgabe oder das Unternehmen — keine Begrüßungsfloskel über die Branche.
- Mitte: das Projekt aus dem Profil, das der Stelle am nächsten kommt, mit Ergebnis in Zahlen; danach die Brücke zur Aufgabe.
- Anrede: Nur wenn im Anzeigentext oder im Hinweis des Kandidaten eine Ansprechperson MIT Namen und Anrede steht, "Sehr geehrte Frau …" bzw. "Sehr geehrter Herr …". Namen NIEMALS aus einer E-Mail-Adresse ableiten und Geschlecht nie raten — im Zweifel "Sehr geehrte Damen und Herren,".
- Verlangt die Anzeige einen Eintrittstermin: "ab sofort".
- Verlangt die Anzeige eine Gehaltsvorstellung: nenne selbst eine konkrete Zahl — kein Platzhalter, keine Rückfrage. Nennt die Anzeige eine Gehaltsspanne, nimm das untere Drittel; nennt sie keine, nimm 48.000 € brutto pro Jahr (Junior/Quereinstieg in NRW). Ein kurzer Satz als Verhandlungsbasis. Fragt die Anzeige nicht danach, schreibe nichts zum Gehalt.
- KEINE Grußformel und keine Signatur am Ende — die werden automatisch angehängt.

TONBEISPIEL (nur Ton und Rhythmus, Inhalte NICHT übernehmen):
"Sehr geehrte Damen und Herren,
Ihre Anzeige nennt Workflows, Formulare und Dashboards als Alltag. Genau das baue ich seit Juni 2026 freiberuflich: Für einen Lieferdienst in Kyjiw läuft ein System, das täglich rund 70 Buchhaltungsbelege erzeugt, verschickt und in ein Tagesregister schreibt — vorher mehrere Stunden Excel pro Tag.
Als Werkzeug nutze ich n8n auf einem eigenen Linux-Server, dazu Python für die Teile, die über Klicken hinausgehen. Projekterfahrung im klassischen Sinn habe ich nicht; ich habe bisher jedes Projekt selbst geschnitten, abgestimmt und dokumentiert.
Ab sofort verfügbar, ein Gespräch geht auch kurzfristig."

Antworte NUR mit JSON:
{"betreff": "Bewerbung als <Stellentitel> – Mykyta Rozumnyi", "anschreiben": "Text, Absätze mit \\n\\n getrennt", "email": "passendste Bewerbungsadresse aus den gefundenen E-Mail-Adressen oder null", "hinweis_ru": "1–2 Sätze auf Russisch, Nikita mit \"ты\" ansprechen: was er vor dem Absenden prüfen sollte — nur Konkretes aus DIESER Anzeige; gibt es nichts Konkretes, leerer String"}"""

jsnode("Brief-Prompt", r"""
// Промпт для письма: профиль + вакансия (+ пожелание Никиты при переписывании).
const d = $input.first().json ?? {};
const text = String(d.stellenangebotsBeschreibung ?? '').slice(0, 6000);
const title = d.stellenangebotsTitel || R.head_title || '(Titel unbekannt)';
// API der BA antwortet manchmal mit 403 — dann schreibt das Modell blind, ohne Anzeigentext.
const blind = !text;
const firma = d.firma ?? '';
const ort = (d.stellenlokationen ?? [])[0]?.adresse?.ort ?? '';
const url = R.url || d.externeURL || `https://www.arbeitsagentur.de/jobsuche/jobdetail/${R.refnr}`;
const salary = d.gehaltsspanneVon ? `${d.gehaltsspanneVon}–${d.gehaltsspanneBis ?? '?'} € pro Jahr` : '';
const emails = [...new Set((text.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) ?? []).map((e) => e.toLowerCase()))];

// Адрес для отклика часто скрыт за капчей Arbeitsagentur. Тогда Никита отвечает на письмо
// строкой с e-mail: берём его как получателя, а не как пожелание по тексту.
const forced = ((R.instruction ?? '').match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i) ?? [''])[0].toLowerCase();
const onlyMail = forced && (R.instruction ?? '').replace(forced, '').replace(/[\s,.:;«»"']/gi, '').length === 0;
if (forced) emails.unshift(forced);

let user = `KANDIDAT:\n${__PROFILE__}\n\nSTELLE:\nTitel: ${title}\nFirma: ${firma}\nOrt: ${ort}\n` +
  `Gehaltsangabe der Anzeige: ${salary || 'keine'}\n` +
  `Gefundene E-Mail-Adressen: ${emails.join(', ') || 'keine'}\n\n` +
  (text || '(Beschreibung nicht verfügbar — allgemeiner schreiben, nur Titel und Firma verwenden)');
if (R.instruction && !onlyMail) user += `\n\nBISHERIGES ANSCHREIBEN:\n${R.letter}\n\nÄNDERUNGSWUNSCH DES KANDIDATEN (evtl. auf Russisch): ${R.instruction}\nÜberarbeite das Anschreiben genau so. Alle Regeln gelten weiter.`;
else if ((R.rewrite || onlyMail) && R.letter) user += `\n\nBISHERIGES ANSCHREIBEN:\n${R.letter}\n\nSchreibe eine spürbar andere Variante (anderer Einstieg, andere Projektauswahl oder Gewichtung).`;

return [{ json: {
  job: { refnr: R.refnr, title, firma, url, emails: [...new Set(emails)], forced, blind },
  openai_request: { model: 'gpt-4o', temperature: 0.6, max_tokens: 1200, response_format: { type: 'json_object' },
    messages: [{ role: 'system', content: __SYSTEM__ }, { role: 'user', content: user }] },
} }];
""".replace("__PROFILE__", js_str(PROFILE)).replace("__SYSTEM__", js_str(LETTER_SYSTEM)), [880, -200])

wf.node("OpenAI Brief", "n8n-nodes-base.httpRequest", 4.2, {
    "method": "POST", "url": "https://api.openai.com/v1/chat/completions",
    "authentication": "predefinedCredentialType", "nodeCredentialType": "openAiApi",
    "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.openai_request) }}",
    "options": {"timeout": 60000},
}, [1100, -200], credentials=CRED_OPENAI, retryOnFail=True, maxTries=3, waitBetweenTries=5000,
   onError="continueRegularOutput")

jsnode("Brief-Nachricht", r"""
// Письмо в Telegram. Формат строк «Кому:», «Betreff:», «#ref» и разделители ——— читает Route
// при отправке, поэтому менять его только вместе с Route.
const job = $('Brief-Prompt').first().json.job;
let o = {};
try { o = JSON.parse($input.first().json.choices?.[0]?.message?.content ?? '{}'); } catch (e) { o = {}; }
if (!o.anschreiben) return [send('❌ Не получилось написать письмо (ошибка OpenAI). Нажми кнопку ещё раз через минуту.')];

// Модель отдаёт адрес не всегда; если в тексте вакансии он ровно один — берём его.
const picked = job.emails.includes(String(o.email ?? '').toLowerCase()) ? String(o.email).toLowerCase() : '';
const email = job.forced || picked || (job.emails.length === 1 ? job.emails[0] : '');
const subject = String(o.betreff || `Bewerbung als ${job.title} – Mykyta Rozumnyi`).replace(/\n/g, ' ');
// Модель иногда всё равно дописывает прощание — срезаем, подпись ставим сами.
const body = String(o.anschreiben).trim()
  .replace(/\n+\s*(mit freundlichen grüßen|viele grüße|beste grüße|freundliche grüße)[\s\S]*$/i, '').trim();
const letter = body + '\n\n' + __SIGNATURE__;
const text = [
  job.blind ? '⚠️ <b>Текст вакансии не загрузился</b> (API Arbeitsagentur ответил ошибкой) — письмо общее. Лучше нажми 🔄 через пару минут.' : '',
  `✉️ <b>Anschreiben</b> · ${esc(job.title)} — ${esc(job.firma)}`,
  email ? `📧 Кому: ${esc(email)}` : '📮 E-mail в вакансии нет — откликайся через сайт/портал, текст скопируй отсюда.',
  `Betreff: ${esc(subject)}`,
  `#ref ${esc(job.refnr)}`,
  '———', esc(letter), '———',
  o.hinweis_ru ? `💡 ${esc(o.hinweis_ru)}` : '',
  email ? '✏️ Чтобы поправить — ответь на это сообщение: «короче», «сделай акцент на Python»…'
        : '✏️ Ответь на это сообщение: текстом — поправлю письмо; e-mail — добавлю кнопку отправки (можно с именем: «huzun@firma.de, Frau Hülya Uzun»).',
].filter(Boolean).join('\n');
return [send(text, { reply_markup: { inline_keyboard: letterKb(job.refnr, email, job.url) } })];
""".replace("__SIGNATURE__", js_str(SIGNATURE)), [1320, -200])

# --- ❌ Не интересно -------------------------------------------------------------

wf.node("Als abgelehnt", "n8n-nodes-base.dataTable", 1.1, {
    "operation": "update", "dataTableId": {"__rl": True, "mode": "id", "value": TABLE_SEEN},
    "filters": {"conditions": [{"keyName": "refnr", "condition": "eq", "keyValue": "={{ $('Route').first().json.refnr }}"}]},
    "matchType": "allConditions",
    "columns": {"mappingMode": "defineBelow", "value": {"status": "rejected"}},
    "options": {},
}, [660, 0], alwaysOutputData=True, executeOnce=True, onError="continueRegularOutput")
jsnode("Abgelehnt-Tasten", r"""
return [setKb([[{ text: '❌ Не интересно', callback_data: 'n:' }]])];
""", [880, 0], executeOnce=True)

# --- 📤 Подтверждение / отмена -----------------------------------------------------

jsnode("Bestätigen-Tasten", r"""
if (!R.to) return [send('В этом письме нет адреса для отправки — откликайся через сайт вакансии.')];
return [setKb([
  [{ text: '✅ Да, отправить на ' + R.to, callback_data: 'S:' + R.refnr }],
  [{ text: '↩️ Отмена', callback_data: 'c:' + R.refnr }],
  ...(R.url ? [[{ text: '🔗 Вакансия', url: R.url }]] : []),
])];
""", [660, 200])

jsnode("Abbrechen-Tasten", r"""
return [setKb(letterKb(R.refnr, R.to, R.url))];
""", [660, 600])

# --- 📤 Отправка письма -----------------------------------------------------------

table_get("Schon gesendet?", TABLE_APPLIED, "={{ $('Route').first().json.refnr }}", [660, 400])

# Сразу убираем кнопки, чтобы второе нажатие не успело отправить письмо ещё раз.
jsnode("Senden-Tasten", r"""
return [setKb([[{ text: '⏳ Отправляю…', callback_data: 'n:' }]])];
""", [660, 300])

jsnode("Mail vorbereiten", r"""
// Последние проверки перед отправкой: адрес, тема, текст и защита от двойного нажатия.
const dup = $input.all().some((it) => it.json.refnr === R.refnr && it.json.channel === 'email');
const problem = dup ? 'Письмо по этой вакансии уже отправлено — второй раз не шлю.'
  : !/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(R.to) ? 'Не нашёл корректный адрес в письме.'
  : !R.subject ? 'Не нашёл тему письма (строку Betreff).'
  : R.letter.length < 300 ? 'Текст письма слишком короткий или не найден.' : '';
return [{ json: { ok: !problem, problem, to: R.to, subject: R.subject, letter: R.letter } }];
""", [880, 400])

wf.node("Mail ok?", "n8n-nodes-base.if", 2.2, {
    "conditions": {"options": {"caseSensitive": True, "version": 2, "typeValidation": "loose"},
                   "combinator": "and",
                   "conditions": [{"id": "ok", "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                                   "leftValue": "={{ $json.ok }}", "rightValue": ""}]},
    "looseTypeValidation": True, "options": {},
}, [1100, 400])

jsnode("Mail-Problem", r"""
return [send('⛔ ' + esc($input.first().json.problem))];
""", [1320, 560])

wf.node("Lebenslauf laden", "n8n-nodes-base.readWriteFile", 1.1, {
    "operation": "read", "fileSelector": CV_PATH, "options": {"dataPropertyName": "data"},
}, [1320, 360])

wf.node("E-Mail senden", "n8n-nodes-base.emailSend", 2.1, {
    "fromEmail": MAIL_FROM,
    "toEmail": "={{ $('Mail vorbereiten').first().json.to }}",
    "subject": "={{ $('Mail vorbereiten').first().json.subject }}",
    "emailFormat": "text",
    "text": "={{ $('Mail vorbereiten').first().json.letter }}",
    "options": {"attachments": "data", "appendAttribution": False},
}, [1540, 360], credentials=CRED_SMTP, onError="continueErrorOutput")

jsnode("Mail-Fehler", r"""
const err = $input.first().json.error;
return [send('❌ Письмо НЕ отправлено: ' + esc(err?.message ?? JSON.stringify(err ?? {})).slice(0, 500))];
""", [1760, 560])

# --- ✅ Запись отклика (после письма или «Я откликнулся») -----------------------------

table_get("Stelle laden", TABLE_SEEN, "={{ $('Route').first().json.refnr }}", [1760, 300])

jsnode("Bewerbung-Daten", r"""
const row = $input.all().find((it) => it.json.refnr === R.refnr)?.json ?? {};
const viaMail = R.action === 'send';
return [{ json: {
  refnr: R.refnr,
  title: row.title || R.head_title,
  firma: row.firma || R.head_firma || '',
  url: row.url || R.url,
  channel: viaMail ? 'email' : 'portal',
  recipient: viaMail ? R.to : '',
  subject: viaMail ? R.subject : '',
  applied_at: $now.setZone('Europe/Berlin').toISO(),
} }];
""", [1980, 300])

wf.node("Bewerbung merken", "n8n-nodes-base.dataTable", 1.1, {
    "operation": "insert", "dataTableId": {"__rl": True, "mode": "id", "value": TABLE_APPLIED},
    "columns": {"mappingMode": "defineBelow", "value": {
        c: "={{ $json.%s }}" % c for c in
        ("refnr", "title", "firma", "url", "channel", "recipient", "subject", "applied_at")}},
    "options": {},
}, [2200, 300])

wf.node("Als beworben", "n8n-nodes-base.dataTable", 1.1, {
    "operation": "update", "dataTableId": {"__rl": True, "mode": "id", "value": TABLE_SEEN},
    "filters": {"conditions": [{"keyName": "refnr", "condition": "eq", "keyValue": "={{ $('Route').first().json.refnr }}"}]},
    "matchType": "allConditions",
    "columns": {"mappingMode": "defineBelow", "value": {"status": "applied"}},
    "options": {},
}, [2420, 300], alwaysOutputData=True, executeOnce=True, onError="continueRegularOutput")

jsnode("Beworben-Tasten", r"""
const when = $now.setZone('Europe/Berlin').toFormat('dd.MM HH:mm');
const label = R.action === 'send' ? `✅ Отправлено на ${R.to} · ${when}` : `✅ Откликнулся · ${when}`;
return [setKb([[{ text: label, callback_data: 'n:' }], ...(R.url ? [[{ text: '🔗 Вакансия', url: R.url }]] : [])])];
""", [2640, 300], executeOnce=True)

# --- /bewerbungen, /suche, помощь ------------------------------------------------------

table_get("Alle Bewerbungen", TABLE_APPLIED, None, [660, 800], return_all=True)
jsnode("Bewerbungen-Liste", r"""
// Журнал откликов — пригодится для отчёта о поиске работы (Eigenbemühungen) в Arbeitsagentur.
const rows = $input.all().map((it) => it.json).filter((r) => r.refnr)
  .sort((a, b) => String(b.applied_at).localeCompare(String(a.applied_at)));
if (!rows.length) return [send('📋 Откликов пока нет.')];
const fmt = (iso) => (iso ? DateTime.fromISO(iso).setZone('Europe/Berlin').toFormat('dd.MM.yy') : '—');
const lines = rows.slice(0, 40).map((r) =>
  `${fmt(r.applied_at)} · <a href="${esc(r.url)}">${esc(r.title)}</a> — ${esc(r.firma)} · ${r.channel === 'email' ? '📧 ' + esc(r.recipient) : '🌐 портал'}`);
return [send(`📋 <b>Мои отклики: ${rows.length}</b>\n\n${lines.join('\n')}` + (rows.length > 40 ? `\n…и ещё ${rows.length - 40}` : ''))];
""", [880, 800])

wf.node("Suche starten", "n8n-nodes-base.httpRequest", 4.2, {
    "method": "POST", "url": SEARCH_WEBHOOK, "options": {"timeout": 10000},
}, [660, 1000], onError="continueRegularOutput")
jsnode("Suche-Antwort", r"""
const ok = !$input.first().json.error;
return [send(ok ? '🔎 Запустил поиск. Новые вакансии придут через несколько минут.' : '❌ Не смог запустить поиск.')];
""", [880, 1000])

jsnode("Hilfe", r"""
return [send([
  '🤖 <b>Бот поиска работы</b>',
  'Каждое утро в 07:30 присылаю новые вакансии с оценкой под твоё резюме.',
  '',
  'Под вакансией: ✍️ написать Anschreiben · ❌ не интересно',
  'Под письмом: 📤 отправить (попрошу подтвердить) · 🔄 другой вариант · ✅ я откликнулся',
  'Ответь на письмо текстом — перепишу с учётом пожелания.',
  '',
  '/suche — искать прямо сейчас',
  '/bewerbungen — список откликов (для отчёта в Arbeitsagentur)',
].join('\n'))];
""", [660, 1200])

# --- Связи ------------------------------------------------------------------------------

wf.link("Telegram", "Route")
wf.link("Route", "Quittung")
wf.link("Quittung", "Callback beantworten")
wf.link("Route", "Ereignis prüfen")
wf.link("Ereignis prüfen", "Nur einmal")
wf.link("Nur einmal", "Ereignis merken")
wf.link("Ereignis merken", "Aktion")
out = {a: i for i, a in enumerate(ACTIONS)}
wf.link("Aktion", "Stelle (Details)", out["write"])
wf.link("Stelle (Details)", "Brief-Prompt")
wf.link("Brief-Prompt", "OpenAI Brief")
wf.link("OpenAI Brief", "Brief-Nachricht")
wf.link("Brief-Nachricht", "Telegram API")
wf.link("Aktion", "Als abgelehnt", out["reject"])
wf.link("Als abgelehnt", "Abgelehnt-Tasten")
wf.link("Abgelehnt-Tasten", "Telegram API")
wf.link("Aktion", "Bestätigen-Tasten", out["confirm_send"])
wf.link("Bestätigen-Tasten", "Telegram API")
wf.link("Aktion", "Senden-Tasten", out["send"])
wf.link("Senden-Tasten", "Telegram API")
wf.link("Aktion", "Schon gesendet?", out["send"])
wf.link("Schon gesendet?", "Mail vorbereiten")
wf.link("Mail vorbereiten", "Mail ok?")
wf.link("Mail ok?", "Lebenslauf laden", 0)
wf.link("Mail ok?", "Mail-Problem", 1)
wf.link("Mail-Problem", "Telegram API")
wf.link("Lebenslauf laden", "E-Mail senden")
wf.link("E-Mail senden", "Stelle laden", 0)
wf.link("E-Mail senden", "Mail-Fehler", 1)
wf.link("Mail-Fehler", "Telegram API")
wf.link("Aktion", "Abbrechen-Tasten", out["cancel_send"])
wf.link("Abbrechen-Tasten", "Telegram API")
wf.link("Aktion", "Stelle laden", out["applied"])
wf.link("Stelle laden", "Bewerbung-Daten")
wf.link("Bewerbung-Daten", "Bewerbung merken")
wf.link("Bewerbung merken", "Als beworben")
wf.link("Als beworben", "Beworben-Tasten")
wf.link("Beworben-Tasten", "Telegram API")
wf.link("Aktion", "Alle Bewerbungen", out["list"])
wf.link("Alle Bewerbungen", "Bewerbungen-Liste")
wf.link("Bewerbungen-Liste", "Telegram API")
wf.link("Aktion", "Suche starten", out["search"])
wf.link("Suche starten", "Suche-Antwort")
wf.link("Suche-Antwort", "Telegram API")
wf.link("Aktion", "Hilfe", out["help"])
wf.link("Hilfe", "Telegram API")

wf.save(OUT)
