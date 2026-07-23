"""Minimal Vercel-compatible Gmail IMAP API. One request = one IMAP connection."""
from __future__ import annotations

import email
import imaplib
import os
import hmac
import secrets
from email.header import decode_header

from flask import Flask, jsonify, request

app = Flask(__name__)


def generate(address: str, kind: str) -> str:
    """Gmail dot/plus alias generator; kept here to make the Vercel function self-contained."""
    local, separator, domain = address.strip().lower().partition("@")
    if not separator or domain != "gmail.com":
        raise ValueError("GMAIL_ADDRESS must be a @gmail.com address")
    local = local.replace(".", "")
    if len(local) < 2:
        raise ValueError("Invalid Gmail address")
    if kind == "dot":
        result = local[0]
        for character in local[1:]:
            result += ("." if secrets.choice((False, True)) else "") + character
        return f"{result}@{domain}"
    if kind in {"dotplus", "plus"}:
        return f"{local}+{secrets.token_urlsafe(5).lower()}@{domain}"
    if kind == "mixed":
        return generate(address, secrets.choice(("dot", "plus")))
    raise ValueError("type must be dot, dotplus, plus, or mixed")


def setting(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"Missing Vercel environment variable: {name}")
    return value


def authorise() -> None:
    # The App Password is also the API secret so the inbox is never public.
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").replace(" ", "")
    expected = setting("GMAIL_APP_PASSWORD").replace(" ", "")
    if not hmac.compare_digest(supplied, expected):
        raise PermissionError("Use Authorization: Bearer <your Gmail App Password>")


def mailbox() -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
    client.login(setting("GMAIL_ADDRESS"), setting("GMAIL_APP_PASSWORD").replace(" ", ""))
    client.select("INBOX")
    return client


def decoded(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for text, charset in decode_header(value):
        parts.append(text.decode(charset or "utf-8", errors="replace") if isinstance(text, bytes) else text)
    return "".join(parts)


def body(message: email.message.Message) -> str:
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
            data = part.get_payload(decode=True)
            if data:
                return data.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def message_data(client: imaplib.IMAP4_SSL, uid: bytes) -> dict:
    status, payload = client.uid("fetch", uid, "(RFC822)")
    if status != "OK" or not payload or not isinstance(payload[0], tuple):
        raise RuntimeError("Could not read the requested message")
    message = email.message_from_bytes(payload[0][1])
    return {"to": decoded(message.get("To")), "uid": uid.decode(), "from": decoded(message.get("From")),
            "date": decoded(message.get("Date")), "subject": decoded(message.get("Subject")), "body": body(message)}


def find_messages(recipient: str, text: str | None = None) -> list[dict]:
    client = mailbox()
    try:
        # Gmail's IMAP search understands recipient and full-text search.
        criteria = f'(TO "{recipient}")' if not text else f'(TO "{recipient}" TEXT "{text}")'
        status, result = client.uid("search", None, criteria)
        if status != "OK":
            raise RuntimeError("Gmail search failed")
        uids = result[0].split()[-100:]
        return [message_data(client, uid) for uid in reversed(uids)]
    finally:
        try:
            client.logout()
        except Exception:
            pass


@app.get("/")
@app.get("/api/")
def home():
    return jsonify(status=True, message="Temporary Gmail API is running")


@app.get("/generate/<kind>")
@app.get("/api/generate/<kind>")
def alias(kind: str):
    try:
        authorise()
        address = generate(setting("GMAIL_ADDRESS"), kind)
        return jsonify(status=True, message="Success", data={"status": True, "email": address, "mailbox": "/read/" + address})
    except PermissionError as error:
        return jsonify(status=False, message=str(error)), 401
    except (RuntimeError, ValueError) as error:
        return jsonify(status=False, message=str(error)), 400


@app.get("/read/<path:address>")
@app.get("/api/read/<path:address>")
def read(address: str):
    try:
        authorise()
        messages = find_messages(address)
        return jsonify(status=bool(messages), message="Messages appear" if messages else "No messages appear", email=address,
                       **({"data": messages} if messages else {}))
    except PermissionError as error:
        return jsonify(status=False, message=str(error)), 401
    except Exception as error:
        return jsonify(status=False, message=str(error), email=address), 500


@app.get("/readby/<path:address>/<path:text>")
@app.get("/api/readby/<path:address>/<path:text>")
def read_by(address: str, text: str):
    try:
        authorise()
        messages = find_messages(address, text)
        return jsonify(status=bool(messages), message="Messages appear" if messages else "No messages appear", email=address,
                       **({"data": messages} if messages else {}))
    except PermissionError as error:
        return jsonify(status=False, message=str(error)), 401
    except Exception as error:
        return jsonify(status=False, message=str(error), email=address), 500


@app.get("/delete/<uid>")
@app.get("/api/delete/<uid>")
def delete(uid: str):
    try:
        authorise()
        client = mailbox()
        try:
            status, _ = client.uid("store", uid, "+X-GM-LABELS", "\\Trash")
            return jsonify(status == "OK")
        finally:
            client.logout()
    except PermissionError as error:
        return jsonify(status=False, message=str(error)), 401
    except Exception:
        return jsonify(False)


@app.errorhandler(404)
def missing(_: Exception):
    return jsonify(status=False, message="Route not found"), 404
