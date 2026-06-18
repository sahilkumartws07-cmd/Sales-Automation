from __future__ import annotations

import base64
from dataclasses import dataclass
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from email.message import EmailMessage
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from sales_automation.config import Settings, get_settings
from sales_automation.models import EmailDraft, Lead

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


class GmailAuthenticationError(RuntimeError):
    """Raised when Gmail OAuth credentials cannot be refreshed or re-created."""


@dataclass(frozen=True)
class GmailReplyMessage:
    message_id: str
    thread_id: str | None
    rfc_message_id: str | None
    from_email: str
    subject: str | None
    body: str


class GmailService:
    def __init__(self, *, settings: Settings | None = None, service: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._service = service

    def list_unread_replies(
        self,
        *,
        query: str = "is:unread",
        limit: int = 100,
    ) -> list[GmailReplyMessage]:
        service = self._gmail()
        response = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        messages = response.get("messages", [])
        replies: list[GmailReplyMessage] = []
        for item in messages:
            message = (
                service.users()
                .messages()
                .get(userId="me", id=item["id"], format="full")
                .execute()
            )
            replies.append(_reply_from_gmail_message(message))
        return replies

    def mark_read(self, message_id: str) -> None:
        self._gmail().users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()

    def send_draft(self, *, lead: Lead, draft: EmailDraft) -> str:
        sender = self.settings.gmail_sender_email
        if not sender:
            raise ValueError("GMAIL_SENDER_EMAIL is not configured")

        message = build_outreach_message(
            lead=lead,
            draft=draft,
            sender_email=sender,
            sender_name=self.settings.outreach_sender_name,
            reply_to_email=self.settings.outreach_reply_to_email,
            unsubscribe_url=self.settings.outreach_unsubscribe_url,
            postal_address=self.settings.outreach_postal_address,
        )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = self._gmail().users().messages().send(userId="me", body={"raw": raw}).execute()
        return str(sent["id"])

    def send_reply(
        self,
        *,
        to_email: str,
        subject: str | None,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> str:
        sender = self.settings.gmail_sender_email
        if not sender:
            raise ValueError("GMAIL_SENDER_EMAIL is not configured")
        if not body.strip():
            raise ValueError("Reply body cannot be empty")

        message = build_reply_message(
            to_email=to_email,
            body=body,
            sender_email=sender,
            sender_name=self.settings.outreach_sender_name,
            subject=subject,
            in_reply_to=in_reply_to,
        )
        request_body: dict[str, str] = {
            "raw": base64.urlsafe_b64encode(message.as_bytes()).decode()
        }
        if thread_id:
            request_body["threadId"] = thread_id
        sent = self._gmail().users().messages().send(userId="me", body=request_body).execute()
        return str(sent["id"])

    def _gmail(self) -> Any:
        if self._service is None:
            self._service = _build_gmail_service(self.settings)
        return self._service


def _reply_from_gmail_message(message: dict[str, Any]) -> GmailReplyMessage:
    headers = {
        header["name"].lower(): header["value"]
        for header in message["payload"].get("headers", [])
    }
    return GmailReplyMessage(
        message_id=str(message["id"]),
        thread_id=message.get("threadId"),
        rfc_message_id=headers.get("message-id"),
        from_email=headers.get("from", ""),
        subject=headers.get("subject"),
        body=_message_body(message.get("payload", {})),
    )


def _message_body(payload: dict[str, Any]) -> str:
    plain_body = _find_message_part(payload, "text/plain")
    if plain_body is not None:
        return clean_reply_body(plain_body)

    html_body = _find_message_part(payload, "text/html")
    if html_body is not None:
        return clean_reply_body(_html_to_text(html_body))
    return ""


def _find_message_part(payload: dict[str, Any], mime_type: str) -> str | None:
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return _decode_body(payload["body"]["data"])

    payload_mime_type = payload.get("mimeType")
    if (
        payload.get("body", {}).get("data")
        and not payload.get("parts")
        and (payload_mime_type is None or payload_mime_type == mime_type)
    ):
        return _decode_body(payload["body"]["data"])

    for part in payload.get("parts", []):
        body = _find_message_part(part, mime_type)
        if body is not None:
            return body
    return None


def _decode_body(value: str) -> str:
    return base64.urlsafe_b64decode(value.encode()).decode(errors="replace")


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for element in soup(["blockquote", "style", "script"]):
        element.decompose()
    return soup.get_text("\n")


def clean_reply_body(value: str) -> str:
    """Return the sender's visible reply without quoted thread history."""
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned: list[str] = []
    previous_blank = False

    for line in lines:
        stripped = line.strip()
        if _is_quoted_reply_boundary(stripped):
            break
        if stripped.startswith(">"):
            continue
        if not stripped:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue
        cleaned.append(line.rstrip())
        previous_blank = False

    return "\n".join(cleaned).strip()


def _is_quoted_reply_boundary(line: str) -> bool:
    if not line:
        return False
    if line == "--":
        return True
    if line.lower().startswith(("-----original message-----", "---------- forwarded message")):
        return True
    lower = line.lower()
    if lower.startswith("on ") and " wrote" in lower:
        return True
    if lower.startswith("on ") and " at " in lower and "<" in line and ">" in line:
        return True
    if re.match(r"^on .+ wrote:$", line, flags=re.IGNORECASE):
        return True
    if re.match(r"^from:\s.+", line, flags=re.IGNORECASE):
        return True
    return False


def build_outreach_message(
    *,
    lead: Lead,
    draft: EmailDraft,
    sender_email: str,
    sender_name: str | None = None,
    reply_to_email: str | None = None,
    unsubscribe_url: str | None = None,
    postal_address: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["To"] = lead.email
    message["From"] = _format_sender(sender_email=sender_email, sender_name=sender_name)
    message["Subject"] = draft.subject.strip()
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=_email_domain(sender_email))
    message["X-Auto-Response-Suppress"] = "OOF, AutoReply"
    if reply_to_email:
        message["Reply-To"] = reply_to_email
    if unsubscribe_url:
        message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    message.set_content(
        _append_deliverability_footer(
            draft.body,
            unsubscribe_url=unsubscribe_url,
            postal_address=postal_address,
        )
    )
    return message


def build_reply_message(
    *,
    to_email: str,
    body: str,
    sender_email: str,
    sender_name: str | None = None,
    subject: str | None = None,
    in_reply_to: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["To"] = to_email
    message["From"] = _format_sender(sender_email=sender_email, sender_name=sender_name)
    message["Subject"] = _reply_subject(subject)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=_email_domain(sender_email))
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body.strip())
    return message


def _reply_subject(subject: str | None) -> str:
    if not subject or not subject.strip():
        return "Re:"
    stripped = subject.strip()
    if stripped.lower().startswith("re:"):
        return stripped
    return f"Re: {stripped}"


def _format_sender(*, sender_email: str, sender_name: str | None) -> str:
    if sender_name:
        return formataddr((sender_name, sender_email))
    return sender_email


def _email_domain(email_address: str) -> str | None:
    parsed = parseaddr(email_address)[1]
    if "@" not in parsed:
        return None
    return parsed.rsplit("@", 1)[1] or None


def _append_deliverability_footer(
    body: str,
    *,
    unsubscribe_url: str | None,
    postal_address: str | None,
) -> str:
    footer_lines: list[str] = []
    if unsubscribe_url:
        footer_lines.append(f"Unsubscribe: {unsubscribe_url}")
    if postal_address:
        footer_lines.append(postal_address)
    if not footer_lines:
        return body.strip()
    return f"{body.strip()}\n\n-- \n" + "\n".join(footer_lines)


def _build_gmail_service(settings: Settings) -> Any:
    if not settings.gmail_credentials_file or not settings.gmail_token_file:
        raise ValueError("GMAIL_CREDENTIALS_FILE and GMAIL_TOKEN_FILE must be configured")
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Install Google API dependencies to enable Gmail integration") from exc

    token_path = Path(settings.gmail_token_file)
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                if "invalid_grant" not in str(exc):
                    raise GmailAuthenticationError(
                        "Gmail token refresh failed. Re-authorize Gmail and try again."
                    ) from exc
                token_path.unlink(missing_ok=True)
                credentials = None
        else:
            credentials = None

        if credentials is None:
            flow = InstalledAppFlow.from_client_secrets_file(
                settings.gmail_credentials_file,
                GMAIL_SCOPES,
            )
            credentials = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=credentials)
