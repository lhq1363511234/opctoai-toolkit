#!/usr/bin/env python3
"""Toolkit unified mail console: SMTP send + multi-provider receive."""
from __future__ import annotations

import json
import os
import re
import secrets
import smtplib
import ssl
import string
import threading
import time
import uuid
from datetime import datetime, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
ENV_PATH = BASE_DIR / ".env"


def load_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


ENV = load_env(ENV_PATH)
for k, v in ENV.items():
    os.environ.setdefault(k, v)

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_SECURE = os.getenv("SMTP_SECURE", "true").lower() in {"1", "true", "yes"}
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Mail Console")
BIND_HOST = os.getenv("BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.getenv("BIND_PORT", "18430"))
LOG_PATH = Path(os.getenv("LOG_PATH", "/var/lib/smtp-console/send.log.jsonl"))
MAILBOX_STORE = Path(os.getenv("MAILBOX_STORE", "/var/lib/mail-console/mailboxes.json"))
MAX_RECIPIENTS = int(os.getenv("MAX_RECIPIENTS", "20"))
TELE_OPC_BASE = os.getenv("TELE_OPC_BASE", "").rstrip("/")
TELE_OPC_DEV_TOKEN = os.getenv("TELE_OPC_DEV_TOKEN", "")
OPC_MAIL_BASE = os.getenv("OPC_MAIL_BASE", "").rstrip("/")
OPC_MAIL_DOMAINS = [x.strip() for x in os.getenv("OPC_MAIL_DOMAINS", "").split(",") if x.strip()]
DUCKMAIL_BASE = os.getenv("DUCKMAIL_BASE", "https://api.duckmail.sbs").rstrip("/")
MAILTM_BASE = os.getenv("MAILTM_BASE", "https://api.mail.tm").rstrip("/")
TEMPMAIL_BASE = os.getenv("TEMPMAIL_BASE", "https://api.internal.temp-mail.io/api/v3").rstrip("/")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STORE_LOCK = threading.Lock()

app = FastAPI(title="Toolkit Mail Console", version="2.0.0")
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")


class SendBody(BaseModel):
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str = ""
    text: str = ""
    html: str = ""
    from_name: str | None = None


class CreateMailboxBody(BaseModel):
    provider: str = "custom_mail"
    domain: str | None = None
    name: str | None = None
    label: str | None = None


class ImportMailboxBody(BaseModel):
    provider: str
    address: str
    token: str = ""
    password: str = ""
    label: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask(s: str) -> str:
    if not s:
        return ""
    if len(s) <= 6:
        return "***"
    return s[:3] + "***" + s[-3:]


def gen_name(n: int = 10) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(n))


def gen_password(n: int = 14) -> str:
    chars = string.ascii_letters + string.digits
    return "Aa1!" + "".join(secrets.choice(chars) for _ in range(max(8, n - 4)))


def http_json(
    method: str,
    url: str,
    *,
    data: Any = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 20,
) -> Any:
    hdrs = {
        "Accept": "application/json",
        "User-Agent": "toolkit-mail-console/2.0",
    }
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = UrlRequest(url, body, hdrs)
    try:
        req.get_method = lambda m=method.upper(): m  # py3.8 compatible
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {"raw": raw.decode("utf-8", errors="ignore")}
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {e.code} {url}: {detail[:300]}") from e
    except URLError as e:
        raise RuntimeError(f"network error {url}: {e}") from e


def normalize_emails(values: list[str], *, field: str = "email", required: bool = False) -> list[str]:
    out: list[str] = []
    invalid: list[str] = []
    for raw in values:
        if not raw:
            continue
        for part in re.split(r"[,;\s]+", str(raw).strip()):
            part = part.strip()
            if not part:
                continue
            if not EMAIL_RE.match(part):
                invalid.append(part)
                continue
            if part not in out:
                out.append(part)
    if invalid:
        bad = ", ".join(invalid[:5])
        raise ValueError(f"{field} 含无效邮箱: {bad}。请只填标准邮箱，例如 name@example.com")
    if required and not out:
        raise ValueError(f"{field} 不能为空")
    return out


def append_log(entry: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_logs(limit: int = 50) -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    items: list[dict[str, Any]] = []
    for line in lines[-max(1, min(limit, 200)) :]:
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(items))


def load_mailboxes() -> list[dict[str, Any]]:
    with STORE_LOCK:
        if not MAILBOX_STORE.exists():
            return []
        try:
            data = json.loads(MAILBOX_STORE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data["items"]
        except Exception:
            return []
    return []


def save_mailboxes(items: list[dict[str, Any]]) -> None:
    with STORE_LOCK:
        MAILBOX_STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = MAILBOX_STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(MAILBOX_STORE)


def upsert_mailbox(item: dict[str, Any]) -> dict[str, Any]:
    items = load_mailboxes()
    found = False
    for i, old in enumerate(items):
        if old.get("id") == item.get("id") or (
            old.get("provider") == item.get("provider") and old.get("address") == item.get("address")
        ):
            merged = {**old, **item, "updated_at": now_iso()}
            items[i] = merged
            item = merged
            found = True
            break
    if not found:
        item.setdefault("created_at", now_iso())
        item["updated_at"] = now_iso()
        items.insert(0, item)
    # keep recent 200
    save_mailboxes(items[:200])
    return item


def get_mailbox(mailbox_id: str) -> Optional[dict[str, Any]]:
    for item in load_mailboxes():
        if item.get("id") == mailbox_id or item.get("address") == mailbox_id:
            return item
    return None


def delete_mailbox(mailbox_id: str) -> bool:
    items = load_mailboxes()
    new_items = [x for x in items if x.get("id") != mailbox_id and x.get("address") != mailbox_id]
    if len(new_items) == len(items):
        return False
    save_mailboxes(new_items)
    return True




def strip_email_headers(text: str) -> str:
    """If provider returns raw RFC822-ish payload, keep only body after headers."""
    raw = text or ""
    if not raw:
        return ""
    if re.search(
        r"(?im)^(received|return-path|arc-|dkim-|authentication-results|mime-version|content-type|message-id|from:|to:|subject:|date:)",
        raw,
    ):
        parts = re.split(r"\r?\n\r?\n", raw, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            raw = parts[1]
    # quoted-printable soft line breaks and common entities
    raw = re.sub(r"=\r?\n", "", raw)
    raw = raw.replace("=3D", "=").replace("=20", " ")
    # if html, strip tags for code extraction
    if "<html" in raw.lower() or "<body" in raw.lower() or "<div" in raw.lower():
        raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _normalize_code(code: str) -> str:
    return (code or "").strip()


def _is_plausible_code(code: str) -> bool:
    c = _normalize_code(code)
    if not c:
        return False
    # dashed OTP like ABC-DEF / Q1W-E2R
    if re.fullmatch(r"[A-Za-z0-9]{3}-[A-Za-z0-9]{3}", c):
        bad = {"arc-sea", "rsa-sha", "are-ema", "ent-dat", "ent-fro", "ac4-iad"}
        return c.lower() not in bad
    # pure digits 4-8
    if re.fullmatch(r"\d{4,8}", c):
        if c.startswith("20") and len(c) == 4:
            return False
        if c in {"0000", "1111", "1234", "9999"}:
            return False
        return True
    # alnum 4-8 only if contains a digit (avoid words like Continue/Your)
    if re.fullmatch(r"[A-Za-z0-9]{4,8}", c):
        return any(ch.isdigit() for ch in c) and not c.isalpha()
    return False


def extract_verification_code(text: str, subject: str = "") -> Optional[str]:
    """Extract OTP / verification codes, avoiding raw email header false positives."""
    subject = subject or ""
    body = strip_email_headers(text or "")

    # 1) subject dashed code (xAI / SpaceXAI style)
    for src in (subject,):
        m = re.search(r"\b([A-Za-z0-9]{3}-[A-Za-z0-9]{3})\b", src)
        if m and _is_plausible_code(m.group(1)):
            return m.group(1).upper()

    # 2) labeled patterns on body first, then subject+body
    labeled = [
        r"verification\s+code[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"confirmation\s+code[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"your\s+(?:xai\s+)?(?:verification\s+)?code\s+is[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"your\s+(?:xai\s+)?(?:verification\s+)?code[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"launch\s+code[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"one[-\s]?time(?:\s+pass(?:word|code))?[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"otp[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"验证码[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"code\s+is[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"enter(?:ing)?\s+the\s+code\s+below[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
        r"code\s+below[:\s#\-]*([A-Za-z0-9]{3}-[A-Za-z0-9]{3}|\d{4,8})",
    ]
    for src in (body, f"{subject}\n{body}"):
        for pattern in labeled:
            m = re.search(pattern, src, re.IGNORECASE)
            if not m:
                continue
            code = _normalize_code(m.group(1))
            if _is_plausible_code(code):
                return code.upper() if "-" in code else code

    # 3) dashed code in body only
    m = re.search(r"\b([A-Za-z0-9]{3}-[A-Za-z0-9]{3})\b", body)
    if m and _is_plausible_code(m.group(1)):
        return m.group(1).upper()

    # 4) pure numeric OTP in body only
    for m in re.finditer(r"(?<![#\w])(\d{4,8})(?![\w])", body):
        code = m.group(1)
        if _is_plausible_code(code):
            return code
    return None


def strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def list_payload(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("hydra:member", "member", "items", "messages", "data", "mails", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
            if isinstance(val, dict):
                nested = val.get("messages") or val.get("items")
                if isinstance(nested, list):
                    return [x for x in nested if isinstance(x, dict)]
    return []


def normalize_message(raw: dict[str, Any], provider: str) -> dict[str, Any]:
    from_val = raw.get("from") or raw.get("fromAddr") or raw.get("sender") or raw.get("mail_from") or ""
    if isinstance(from_val, dict):
        from_addr = from_val.get("address") or from_val.get("email") or ""
        from_name = from_val.get("name") or ""
        from_disp = f"{from_name} <{from_addr}>".strip() if from_name else from_addr
    else:
        from_disp = str(from_val)
        from_addr = from_disp
        from_name = ""

    subject = str(raw.get("subject") or raw.get("title") or "(no subject)")
    text = (
        raw.get("text")
        or raw.get("textBody")
        or raw.get("body_text")
        or raw.get("intro")
        or raw.get("preview")
        or ""
    )
    html = raw.get("html") or raw.get("htmlBody") or raw.get("body_html") or raw.get("body") or ""
    if isinstance(html, list):
        html = "\n".join(str(x) for x in html)
    if not text and html:
        text = strip_html(str(html))
    # custom mail / cloudflare-email style providers may return raw RFC822 with headers prepended
    text = strip_email_headers(str(text or ""))
    if html:
        html_text = strip_email_headers(strip_html(str(html)))
        if html_text and (not text or len(html_text) > len(text)):
            # keep original html, but ensure code extraction sees cleaned html text via text
            if not text:
                text = html_text
    created = (
        raw.get("createdAt")
        or raw.get("created_at")
        or raw.get("date")
        or raw.get("receivedAt")
        or raw.get("time")
        or ""
    )
    msg_id = str(raw.get("id") or raw.get("messageId") or raw.get("@id") or raw.get("msgid") or uuid.uuid4())
    code = extract_verification_code(str(text), subject)
    return {
        "id": msg_id,
        "provider": provider,
        "from": from_disp,
        "from_address": from_addr,
        "subject": subject,
        "intro": (str(text)[:180] if text else ""),
        "text": str(text or ""),
        "html": str(html or ""),
        "created_at": created,
        "code": code,
        "raw": raw,
    }


def provider_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": "custom_mail",
            "name": "Custom Mail API",
            "type": "receive",
            "create": True,
            "note": "Self-hosted temporary mailbox API (configure OPC_MAIL_BASE)",
            "base": OPC_MAIL_BASE,
        },
        {
            "id": "mailtm",
            "name": "Mail.tm",
            "type": "receive",
            "create": True,
            "note": "Public temporary mailbox provider",
            "base": MAILTM_BASE,
        },
        {
            "id": "duckmail",
            "name": "DuckMail",
            "type": "receive",
            "create": True,
            "note": "Mail.tm-compatible temporary mailbox API",
            "base": DUCKMAIL_BASE,
        },
        {
            "id": "tempmailio",
            "name": "TempMail.io",
            "type": "receive",
            "create": True,
            "note": "Public temporary mailbox provider",
            "base": TEMPMAIL_BASE,
        },
        {
            "id": "smtp_send",
            "name": "SMTP Sender",
            "type": "send",
            "create": False,
            "note": "Direct SMTP sending via configured credentials",
            "base": f"{SMTP_HOST}:{SMTP_PORT}" if SMTP_HOST else "",
        },
        {
            "id": "remote_send",
            "name": "Remote Send API",
            "type": "send",
            "create": False,
            "note": "Optional remote mail-sending service (configure TELE_OPC_BASE)",
            "base": TELE_OPC_BASE,
        },
    ]


def smtp_status() -> dict[str, Any]:
    t0 = time.time()
    if not SMTP_HOST:
        return {"ok": False, "latency_ms": 0, "message": "SMTP_HOST is not configured"}
    try:
        if SMTP_SECURE:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=12) as s:
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASSWORD)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=12) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASSWORD)
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "message": "SMTP 登录成功"}
    except Exception as e:
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000), "message": str(e)}


def send_mail(
    *,
    to_list: list[str],
    cc_list: list[str],
    subject: str,
    text: str,
    html: str,
    from_name: str | None,
) -> dict[str, Any]:
    if not SMTP_HOST:
        raise ValueError("SMTP_HOST is not configured")
    if not to_list:
        raise ValueError("to is required")
    if len(to_list) + len(cc_list) > MAX_RECIPIENTS:
        raise ValueError(f"too many recipients (max {MAX_RECIPIENTS})")
    if not subject.strip():
        raise ValueError("subject is required")
    if not text.strip() and not html.strip():
        raise ValueError("text or html is required")

    msg = MIMEMultipart("alternative")
    display_name = (from_name or SMTP_FROM_NAME or "").strip() or "Mail Console"
    msg["From"] = formataddr((str(Header(display_name, "utf-8")), SMTP_FROM))
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = str(Header(subject.strip(), "utf-8"))
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=SMTP_FROM.split("@")[-1] if "@" in SMTP_FROM else "localhost")

    body_text = text.strip() or re.sub(r"<[^>]+>", " ", html)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if html.strip():
        msg.attach(MIMEText(html, "html", "utf-8"))

    recipients = to_list + cc_list
    t0 = time.time()
    if SMTP_SECURE:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30) as s:
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(SMTP_FROM, recipients, msg.as_string())
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(SMTP_FROM, recipients, msg.as_string())

    return {
        "message_id": msg["Message-ID"],
        "latency_ms": int((time.time() - t0) * 1000),
        "accepted": recipients,
    }


# -------------------- providers --------------------

def opctoai_create(name: str = "", domain: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    if domain:
        payload["domain"] = domain
    data = http_json("POST", f"{OPC_MAIL_BASE}/accounts", data=payload or {})
    address = data.get("address") or data.get("email") or data.get("id")
    if not address:
        raise RuntimeError(f"custom mail create failed: {data}")
    password = data.get("password") or ""
    token = address  # mail-api2 accepts token=email
    return {
        "provider": "custom_mail",
        "address": address,
        "password": password,
        "token": token,
        "meta": {"api_base": OPC_MAIL_BASE, "mailbox_url": f"{OPC_MAIL_BASE}/messages?token={quote(address)}"},
    }


def opctoai_messages(box: dict[str, Any]) -> list[dict[str, Any]]:
    address = box.get("address") or ""
    token = box.get("token") or address
    data = http_json("GET", f"{OPC_MAIL_BASE}/messages?token={quote(token)}")
    msgs = list_payload(data)
    # optional detail enrichment if body missing
    out = []
    for m in msgs:
        nm = normalize_message(m, "opctoai")
        if not nm["text"] and not nm["html"] and m.get("id"):
            try:
                detail = http_json("GET", f"{OPC_MAIL_BASE}/messages/{quote(str(m['id']))}?token={quote(token)}")
                if isinstance(detail, dict):
                    nm = normalize_message({**m, **detail}, "opctoai")
            except Exception:
                pass
        out.append(nm)
    return out


def mailtm_like_domains(base: str) -> list[str]:
    data = http_json("GET", f"{base}/domains")
    domains = []
    for item in list_payload(data):
        d = item.get("domain")
        if not d:
            continue
        if item.get("isPrivate") is True:
            continue
        if item.get("isActive") is False:
            continue
        if item.get("isVerified") is False:
            continue
        domains.append(str(d))
    return domains


def mailtm_like_create(provider: str, base: str, name: str = "", domain: str = "") -> dict[str, Any]:
    domains = mailtm_like_domains(base)
    if not domains:
        raise RuntimeError(f"{provider} has no domains")
    use_domain = domain if domain in domains else domains[0]
    local = name or gen_name(10)
    address = f"{local}@{use_domain}"
    password = gen_password()
    http_json("POST", f"{base}/accounts", data={"address": address, "password": password})
    tok = http_json("POST", f"{base}/token", data={"address": address, "password": password})
    token = tok.get("token") if isinstance(tok, dict) else ""
    if not token:
        raise RuntimeError(f"{provider} token failed: {tok}")
    return {
        "provider": provider,
        "address": address,
        "password": password,
        "token": token,
        "meta": {"api_base": base, "domain": use_domain},
    }


def mailtm_like_messages(provider: str, base: str, box: dict[str, Any]) -> list[dict[str, Any]]:
    token = box.get("token") or ""
    if not token and box.get("address") and box.get("password"):
        tok = http_json(
            "POST",
            f"{base}/token",
            data={"address": box["address"], "password": box["password"]},
        )
        token = tok.get("token") if isinstance(tok, dict) else ""
        if token:
            box["token"] = token
            upsert_mailbox(box)
    if not token:
        raise RuntimeError(f"{provider} missing token")
    data = http_json("GET", f"{base}/messages", headers={"Authorization": f"Bearer {token}"})
    out = []
    for m in list_payload(data):
        nm = normalize_message(m, provider)
        # fetch detail for body/code
        mid = m.get("id")
        if mid and (not nm["text"] or len(nm["text"]) < 20 or not nm["code"]):
            try:
                detail = http_json(
                    "GET",
                    f"{base}/messages/{quote(str(mid))}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if isinstance(detail, dict):
                    text = detail.get("text") or ""
                    html = detail.get("html") or ""
                    if isinstance(html, list):
                        html = "\n".join(str(x) for x in html)
                    nm = normalize_message({**m, **detail, "text": text, "html": html}, provider)
            except Exception:
                pass
        out.append(nm)
    return out


def tempmailio_domains() -> list[str]:
    data = http_json("GET", f"{TEMPMAIL_BASE}/domains")
    out = []
    if isinstance(data, dict):
        for item in data.get("domains") or []:
            if isinstance(item, dict) and item.get("name"):
                out.append(str(item["name"]))
            elif isinstance(item, str):
                out.append(item)
    return out


def tempmailio_create(name: str = "", domain: str = "") -> dict[str, Any]:
    domains = tempmailio_domains()
    if not domains:
        raise RuntimeError("tempmailio has no domains")
    use_domain = domain if domain in domains else domains[0]
    local = name or gen_name(10)
    data = http_json(
        "POST",
        f"{TEMPMAIL_BASE}/email/new",
        data={"name": local, "domain": use_domain},
    )
    address = data.get("email")
    token = data.get("token") or ""
    if not address:
        raise RuntimeError(f"tempmailio create failed: {data}")
    return {
        "provider": "tempmailio",
        "address": address,
        "password": "",
        "token": token,
        "meta": {"api_base": TEMPMAIL_BASE, "domain": use_domain},
    }


def tempmailio_messages(box: dict[str, Any]) -> list[dict[str, Any]]:
    address = box.get("address")
    if not address:
        raise RuntimeError("tempmailio missing address")
    # temp-mail.io rejects percent-encoded @ in path; keep raw email path
    data = http_json("GET", f"{TEMPMAIL_BASE}/email/{address}/messages")
    msgs = data if isinstance(data, list) else list_payload(data)
    out = []
    for m in msgs:
        # temp-mail.io fields often: from, subject, body_text/body_html, created_at
        raw = {
            "id": m.get("id") or m.get("_id") or uuid.uuid4().hex,
            "from": m.get("from") or m.get("from_mail") or m.get("sender"),
            "subject": m.get("subject") or "",
            "text": m.get("body_text") or m.get("body") or m.get("text") or "",
            "html": m.get("body_html") or m.get("html") or "",
            "createdAt": m.get("created_at") or m.get("date") or "",
        }
        out.append(normalize_message(raw, "tempmailio"))
    return out


def provider_health(provider_id: str) -> dict[str, Any]:
    t0 = time.time()
    try:
        if provider_id in {"custom_mail", "opctoai"}:
            data = http_json("GET", f"{OPC_MAIL_BASE}/")
            ok = bool(data.get("ok")) if isinstance(data, dict) else True
            return {"id": provider_id, "ok": ok, "latency_ms": int((time.time() - t0) * 1000), "detail": data}
        if provider_id == "mailtm":
            domains = mailtm_like_domains(MAILTM_BASE)
            return {"id": provider_id, "ok": bool(domains), "latency_ms": int((time.time() - t0) * 1000), "domains": domains[:5]}
        if provider_id == "duckmail":
            domains = mailtm_like_domains(DUCKMAIL_BASE)
            return {"id": provider_id, "ok": bool(domains), "latency_ms": int((time.time() - t0) * 1000), "domains": domains[:5]}
        if provider_id == "tempmailio":
            domains = tempmailio_domains()
            return {"id": provider_id, "ok": bool(domains), "latency_ms": int((time.time() - t0) * 1000), "domains": domains[:5]}
        if provider_id in {"smtp_send", "feishu_smtp"}:
            st = smtp_status()
            return {"id": provider_id, "ok": st["ok"], "latency_ms": st["latency_ms"], "message": st["message"]}
        if provider_id in {"remote_send", "teleopc"}:
            data = http_json(
                "GET",
                f"{TELE_OPC_BASE}/api/web/mail/smtp-status",
                headers={"x-tele-opc-dev-token": TELE_OPC_DEV_TOKEN},
            )
            ok = bool(data.get("ok")) if isinstance(data, dict) else False
            return {"id": provider_id, "ok": ok, "latency_ms": int((time.time() - t0) * 1000), "detail": data}
        return {"id": provider_id, "ok": False, "message": "unknown provider"}
    except Exception as e:
        return {"id": provider_id, "ok": False, "latency_ms": int((time.time() - t0) * 1000), "message": str(e)}


def create_mailbox(provider: str, name: str = "", domain: str = "", label: str = "") -> dict[str, Any]:
    provider = (provider or "").strip().lower()
    if provider in {"custom_mail", "opctoai"}:
        created = opctoai_create(name=name, domain=domain)
        provider = "custom_mail"
    elif provider == "mailtm":
        created = mailtm_like_create("mailtm", MAILTM_BASE, name=name, domain=domain)
    elif provider == "duckmail":
        created = mailtm_like_create("duckmail", DUCKMAIL_BASE, name=name, domain=domain)
    elif provider in {"tempmailio", "tempmail"}:
        created = tempmailio_create(name=name, domain=domain)
        provider = "tempmailio"
    else:
        raise ValueError(f"unsupported provider: {provider}")

    item = {
        "id": f"mb_{uuid.uuid4().hex[:12]}",
        "provider": provider,
        "address": created["address"],
        "password": created.get("password") or "",
        "token": created.get("token") or "",
        "label": label or "",
        "meta": created.get("meta") or {},
        "last_code": "",
        "last_checked_at": "",
    }
    return upsert_mailbox(item)


def fetch_messages(box: dict[str, Any]) -> list[dict[str, Any]]:
    provider = (box.get("provider") or "").lower()
    if provider in {"custom_mail", "opctoai"}:
        return opctoai_messages(box)
    if provider == "mailtm":
        return mailtm_like_messages("mailtm", MAILTM_BASE, box)
    if provider == "duckmail":
        return mailtm_like_messages("duckmail", DUCKMAIL_BASE, box)
    if provider in {"tempmailio", "tempmail"}:
        return tempmailio_messages(box)
    raise ValueError(f"unsupported provider: {provider}")


# -------------------- routes --------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "toolkit-mail-console",
        "version": "2.0.0",
        "smtp": {
            "host": SMTP_HOST,
            "port": SMTP_PORT,
            "secure": SMTP_SECURE,
            "from": SMTP_FROM,
            "user": mask(SMTP_USER),
        },
        "providers": [p["id"] for p in provider_catalog()],
        "time": now_iso(),
    }


@app.get("/api/status")
async def status() -> dict[str, Any]:
    st = smtp_status()
    return {
        "ok": st["ok"],
        "smtp": {
            "host": SMTP_HOST,
            "port": SMTP_PORT,
            "secure": SMTP_SECURE,
            "from": SMTP_FROM,
            "from_name": SMTP_FROM_NAME,
            "user": mask(SMTP_USER),
            "provider": "smtp",
            "note": "Configured SMTP sender",
        },
        "check": st,
        "providers": provider_catalog(),
        "mailboxes": len(load_mailboxes()),
        "recent": read_logs(10),
    }


@app.get("/api/providers")
async def api_providers(check: bool = False) -> dict[str, Any]:
    items = provider_catalog()
    if check:
        healths = {h["id"]: h for h in (provider_health(p["id"]) for p in items)}
        for p in items:
            p["health"] = healths.get(p["id"])
    return {"ok": True, "items": items}


@app.get("/api/providers/{provider_id}/domains")
async def api_provider_domains(provider_id: str) -> dict[str, Any]:
    provider_id = provider_id.lower()
    try:
        if provider_id in {"custom_mail", "opctoai"}:
            domains = OPC_MAIL_DOMAINS or ([u.split("//",1)[-1].split("/",1)[0] for u in [OPC_MAIL_BASE] if u])
        elif provider_id == "mailtm":
            domains = mailtm_like_domains(MAILTM_BASE)
        elif provider_id == "duckmail":
            domains = mailtm_like_domains(DUCKMAIL_BASE)
        elif provider_id in {"tempmailio", "tempmail"}:
            domains = tempmailio_domains()
        else:
            return {"ok": False, "error": "provider has no domains"}
        return {"ok": True, "provider": provider_id, "domains": domains}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/test")
async def test_login() -> dict[str, Any]:
    st = smtp_status()
    append_log(
        {
            "id": str(uuid.uuid4()),
            "ts": now_iso(),
            "action": "test_login",
            "ok": st["ok"],
            "message": st["message"],
            "latency_ms": st["latency_ms"],
        }
    )
    return st


@app.post("/api/send")
async def api_send(body: SendBody, request: Request) -> JSONResponse:
    req_id = str(uuid.uuid4())
    try:
        to_list = normalize_emails(body.to, field="收件人", required=True)
        # 抄送可选：空就忽略；填了无效值要明确提示，避免误把测试数字当邮箱
        cc_list = normalize_emails(body.cc, field="抄送", required=False) if any(str(x).strip() for x in (body.cc or [])) else []
        result = send_mail(
            to_list=to_list,
            cc_list=cc_list,
            subject=body.subject,
            text=body.text,
            html=body.html,
            from_name=body.from_name,
        )
        entry = {
            "id": req_id,
            "ts": now_iso(),
            "action": "send",
            "ok": True,
            "to": to_list,
            "cc": cc_list,
            "subject": body.subject.strip(),
            "message_id": result["message_id"],
            "latency_ms": result["latency_ms"],
            "ip": request.client.host if request.client else None,
        }
        append_log(entry)
        return JSONResponse({"ok": True, **entry})
    except Exception as e:
        entry = {
            "id": req_id,
            "ts": now_iso(),
            "action": "send",
            "ok": False,
            "to": body.to,
            "cc": body.cc,
            "subject": body.subject,
            "error": str(e),
            "ip": request.client.host if request.client else None,
        }
        append_log(entry)
        return JSONResponse({"ok": False, "error": str(e), "id": req_id}, status_code=400)


@app.get("/api/logs")
async def api_logs(limit: int = 50) -> dict[str, Any]:
    return {"ok": True, "items": read_logs(limit)}


@app.get("/api/teleopc/status")
async def teleopc_status() -> dict[str, Any]:
    try:
        body = http_json(
            "GET",
            f"{TELE_OPC_BASE}/api/web/mail/smtp-status",
            headers={"x-tele-opc-dev-token": TELE_OPC_DEV_TOKEN},
        )
        return {"ok": True, "teleopc": body, "base": TELE_OPC_BASE}
    except Exception as e:
        return {"ok": False, "error": str(e), "base": TELE_OPC_BASE}


@app.post("/api/teleopc/send")
async def teleopc_send(body: SendBody, request: Request) -> JSONResponse:
    req_id = str(uuid.uuid4())
    try:
        to_list = normalize_emails(body.to, field="收件人", required=True)
        cc_list = normalize_emails(body.cc, field="抄送", required=False) if any(str(x).strip() for x in (body.cc or [])) else []
        if not (body.subject or "").strip():
            raise ValueError("主题不能为空")
        if not (body.text or "").strip() and not (body.html or "").strip():
            raise ValueError("正文不能为空")
        payload: dict[str, Any] = {
            "to": to_list,
            "subject": body.subject.strip(),
            "text": body.text or "",
        }
        if cc_list:
            payload["cc"] = cc_list
        if body.html:
            payload["html"] = body.html
        result = http_json(
            "POST",
            f"{TELE_OPC_BASE}/api/web/mail/send",
            data=payload,
            headers={"x-tele-opc-dev-token": TELE_OPC_DEV_TOKEN},
        )
        entry = {
            "id": req_id,
            "ts": now_iso(),
            "action": "teleopc_send",
            "ok": bool(result.get("ok")) if isinstance(result, dict) else True,
            "to": body.to,
            "subject": body.subject,
            "result": result,
            "ip": request.client.host if request.client else None,
        }
        append_log(entry)
        if isinstance(result, dict):
            return JSONResponse({"ok": bool(result.get("ok")), **result, "via": "tele-opc"})
        return JSONResponse({"ok": True, "result": result, "via": "tele-opc"})
    except Exception as e:
        entry = {
            "id": req_id,
            "ts": now_iso(),
            "action": "teleopc_send",
            "ok": False,
            "to": body.to,
            "subject": body.subject,
            "error": str(e),
            "ip": request.client.host if request.client else None,
        }
        append_log(entry)
        return JSONResponse({"ok": False, "error": str(e), "via": "tele-opc"}, status_code=400)


@app.get("/api/mailboxes")
async def api_mailboxes() -> dict[str, Any]:
    items = load_mailboxes()
    safe = []
    for x in items:
        safe.append(
            {
                "id": x.get("id"),
                "provider": x.get("provider"),
                "address": x.get("address"),
                "label": x.get("label") or "",
                "has_token": bool(x.get("token")),
                "has_password": bool(x.get("password")),
                "meta": x.get("meta") or {},
                "last_code": x.get("last_code") or "",
                "last_checked_at": x.get("last_checked_at") or "",
                "created_at": x.get("created_at"),
                "updated_at": x.get("updated_at"),
            }
        )
    return {"ok": True, "items": safe}


@app.post("/api/mailboxes")
async def api_create_mailbox(body: CreateMailboxBody) -> JSONResponse:
    try:
        item = create_mailbox(
            provider=body.provider,
            name=(body.name or "").strip(),
            domain=(body.domain or "").strip(),
            label=(body.label or "").strip(),
        )
        append_log(
            {
                "id": str(uuid.uuid4()),
                "ts": now_iso(),
                "action": "create_mailbox",
                "ok": True,
                "provider": item.get("provider"),
                "address": item.get("address"),
            }
        )
        return JSONResponse(
            {
                "ok": True,
                "mailbox": {
                    "id": item["id"],
                    "provider": item["provider"],
                    "address": item["address"],
                    "password": item.get("password") or "",
                    "token": item.get("token") or "",
                    "label": item.get("label") or "",
                    "meta": item.get("meta") or {},
                },
            }
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/mailboxes/import")
async def api_import_mailbox(body: ImportMailboxBody) -> JSONResponse:
    try:
        address = body.address.strip()
        if not EMAIL_RE.match(address):
            raise ValueError("invalid address")
        item = {
            "id": f"mb_{uuid.uuid4().hex[:12]}",
            "provider": body.provider.strip().lower(),
            "address": address,
            "password": body.password or "",
            "token": body.token or (address if body.provider.strip().lower() in {"custom_mail", "opctoai"} else ""),
            "label": body.label or "",
            "meta": body.meta or {},
            "last_code": "",
            "last_checked_at": "",
        }
        item = upsert_mailbox(item)
        return JSONResponse({"ok": True, "mailbox": item})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.delete("/api/mailboxes/{mailbox_id}")
async def api_delete_mailbox(mailbox_id: str) -> dict[str, Any]:
    ok = delete_mailbox(mailbox_id)
    return {"ok": ok}


@app.get("/api/mailboxes/{mailbox_id}/messages")
async def api_mailbox_messages(mailbox_id: str) -> JSONResponse:
    box = get_mailbox(mailbox_id)
    if not box:
        return JSONResponse({"ok": False, "error": "mailbox not found"}, status_code=404)
    try:
        messages = fetch_messages(box)
        codes = [m.get("code") for m in messages if m.get("code")]
        last_code = codes[0] if codes else ""
        box["last_code"] = last_code
        box["last_checked_at"] = now_iso()
        upsert_mailbox(box)
        return JSONResponse(
            {
                "ok": True,
                "mailbox": {
                    "id": box.get("id"),
                    "provider": box.get("provider"),
                    "address": box.get("address"),
                    "last_code": last_code,
                },
                "total": len(messages),
                "codes": codes,
                "messages": [
                    {
                        "id": m.get("id"),
                        "from": m.get("from"),
                        "subject": m.get("subject"),
                        "intro": m.get("intro"),
                        "text": m.get("text"),
                        "html": m.get("html"),
                        "created_at": m.get("created_at"),
                        "code": m.get("code"),
                    }
                    for m in messages
                ],
            }
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/mailboxes/{mailbox_id}/refresh")
async def api_mailbox_refresh(mailbox_id: str) -> JSONResponse:
    return await api_mailbox_messages(mailbox_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=BIND_HOST, port=BIND_PORT, reload=False)
