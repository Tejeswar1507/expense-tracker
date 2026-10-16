"""Gmail (read alerts, send emails) and Drive (statements folder, Sheet copy) using ONE Google sign-in of the admin.

Scopes: gmail.readonly, gmail.send, drive.file (only files this app creates). The refresh token comes
from scripts/get_google_token.py, run once on your laptop.
"""
from __future__ import annotations

import base64
import io
import re
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

from core.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
]


def credentials():
    from google.oauth2.credentials import Credentials

    cfg = settings()
    if not (cfg.google_client_id and cfg.google_client_secret and cfg.google_refresh_token):
        raise RuntimeError("Google access is not set up yet (GOOGLE_CLIENT_ID / SECRET / REFRESH_TOKEN). See SETUP.md step 3.")
    return Credentials(None, refresh_token=cfg.google_refresh_token, token_uri="https://oauth2.googleapis.com/token",
                       client_id=cfg.google_client_id, client_secret=cfg.google_client_secret, scopes=SCOPES)


def _service(name: str, version: str):
    from googleapiclient.discovery import build

    return build(name, version, credentials=credentials(), cache_discovery=False)


# ------------------------------------------------------------------ Gmail


def list_alert_ids(label: str, newer_than_days: int = 30) -> list[str]:
    svc = _service("gmail", "v1")
    q = f'label:"{label}" newer_than:{newer_than_days}d'
    ids, token = [], None
    while True:
        res = svc.users().messages().list(userId="me", q=q, pageToken=token, maxResults=200).execute()
        ids += [m["id"] for m in res.get("messages", [])]
        token = res.get("nextPageToken")
        if not token:
            return ids


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _body(payload: dict) -> str:
    plain, html = [], []

    def walk(part):
        mt = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mt == "text/plain":
            plain.append(_decode(data))
        elif data and mt == "text/html":
            html.append(_decode(data))
        for p in part.get("parts", []) or []:
            walk(p)

    walk(payload)
    return "\n".join(plain) if plain else "\n".join(html)


def get_message(msg_id: str) -> dict:
    svc = _service("gmail", "v1")
    m = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
    try:
        received = parsedate_to_datetime(headers.get("date"))
    except Exception:
        received = None
    to = headers.get("to", "")
    return {
        "id": msg_id,
        "subject": headers.get("subject", ""),
        "sender": headers.get("from", ""),
        "to": to,
        "x_forwarded_for": headers.get("x-forwarded-for", ""),
        "received": received,
        "body": _body(m["payload"]),
    }


def send_email(to: list[str], subject: str, html: str) -> None:
    svc = _service("gmail", "v1")
    msg = MIMEText(html, "html", "utf-8")
    msg["to"] = ", ".join(to)
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()


# ------------------------------------------------------------------ Drive


def _folder(svc, name: str, parent: str | None) -> str:
    q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent:
        q += f" and '{parent}' in parents"
    found = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
    if found:
        return found[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        meta["parents"] = [parent]
    return svc.files().create(body=meta, fields="id").execute()["id"]


def save_statement(file_name: str, data: bytes, year: int) -> str:
    """Saves the original statement under 'Expense Tracker/Statements/<year>/' in your Drive."""
    from googleapiclient.http import MediaIoBaseUpload

    svc = _service("drive", "v3")
    root = _folder(svc, "Expense Tracker", None)
    st = _folder(svc, "Statements", root)
    yr = _folder(svc, str(year), st)
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/octet-stream", resumable=False)
    return svc.files().create(body={"name": file_name, "parents": [yr]}, media_body=media, fields="id").execute()["id"]


def write_sheet_copy(csv_bytes: bytes, file_id: str | None) -> str:
    """Creates or overwrites the read-only Google Sheet copy of all transactions."""
    from googleapiclient.http import MediaIoBaseUpload

    svc = _service("drive", "v3")
    media = MediaIoBaseUpload(io.BytesIO(csv_bytes), mimetype="text/csv", resumable=False)
    if file_id:
        try:
            svc.files().update(fileId=file_id, media_body=media).execute()
            return file_id
        except Exception:
            pass
    root = _folder(svc, "Expense Tracker", None)
    meta = {"name": "Expense Tracker — all transactions (read-only copy)",
            "mimeType": "application/vnd.google-apps.spreadsheet", "parents": [root]}
    return svc.files().create(body=meta, media_body=media, fields="id").execute()["id"]


def share_read_only(file_id: str, email: str) -> None:
    svc = _service("drive", "v3")
    svc.permissions().create(fileId=file_id, sendNotificationEmail=False,
                             body={"type": "user", "role": "reader", "emailAddress": email}).execute()


def email_address_in(header: str) -> str | None:
    m = re.search(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", header or "")
    return m.group(0).lower() if m else None
