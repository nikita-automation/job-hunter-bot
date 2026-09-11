"""Template for private.py — copy it to private.py and fill in your own data."""
CHAT_ID = "000000000"             # your Telegram chat id
PLZ = "10115"                     # postcode to search around
CRED_OPENAI_ID = "<n8n credential id: OpenAI>"
CRED_TG_ID = "<n8n credential id: Telegram bot>"
CRED_SMTP_ID = "<n8n credential id: SMTP>"
TABLE_SEEN = "<n8n data table id: job_seen>"
TABLE_APPLIED = "<n8n data table id: bewerbungen>"
SEARCH_WEBHOOK_PATH = "jobhunter-run-<random>"
MAIL_FROM = "Max Mustermann <max@example.com>"
CV_PATH = "/root/.n8n-files/Lebenslauf.pdf"

# The model scores jobs and writes cover letters from these facts only.
PROFILE = """
Kandidat: Max Mustermann, wohnt in 10115 Berlin. Verfügbar: Vollzeit, ab sofort.
Werdegang: Quereinsteiger, seit 2026 freiberuflich in der Workflow-Automatisierung.
Sprachen: Deutsch C1, Englisch B1.
Projekte:
- Beispielprojekt: n8n-Workflow, der Rechnungen automatisch erstellt und versendet.
Werkzeuge: n8n, Python, REST APIs, Webhooks.
Nicht vorhanden: Informatikstudium.
Sucht: Junior-Stelle in der Automatisierung, vor Ort oder remote.
""".strip()

SIGNATURE = """Mit freundlichen Grüßen
Max Mustermann
max@example.com"""
