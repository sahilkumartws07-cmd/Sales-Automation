import base64
from datetime import UTC, datetime
import sys
from types import SimpleNamespace

from sales_automation.models import EmailDraft, EmailReply, Lead
from sales_automation.config import Settings
from sales_automation.services.csv_importer import LeadCSVImporter
from sales_automation.services.gmail_service import (
    GmailService,
    _build_gmail_service,
    _reply_from_gmail_message,
    build_reply_message,
    build_outreach_message,
    clean_reply_body,
)
from sales_automation.services.google_sheets_logging import workflow_log_row


def test_reply_from_gmail_message_extracts_headers_and_plain_body() -> None:
    message = {
        "id": "msg-1",
        "threadId": "thread-1",
        "payload": {
            "headers": [
                {"name": "From", "value": "Buyer <buyer@example.com>"},
                {"name": "Subject", "value": "Re: quick question"},
                {"name": "Message-ID", "value": "<message-1@example.com>"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": "SSdtIGludGVyZXN0ZWQ="},
                }
            ],
        },
    }

    reply = _reply_from_gmail_message(message)

    assert reply.message_id == "msg-1"
    assert reply.thread_id == "thread-1"
    assert reply.rfc_message_id == "<message-1@example.com>"
    assert reply.from_email == "Buyer <buyer@example.com>"
    assert reply.subject == "Re: quick question"
    assert reply.body == "I'm interested"


def test_reply_from_gmail_message_strips_quoted_thread_history() -> None:
    raw_body = (
        "Yes, I would be interested.\n\n"
        "On Tue, Jun 16, 2026 at 6:13 PM Sahil Kumar <buyer@example.com> wrote:\n\n"
        "> Yes, I would be interested.\n"
        ">\n"
        "> On Tue, Jun 16, 2026 at 6:12 PM <sender@example.com> wrote:\n"
        ">> Hi, I came across Aether AI.\n"
        ">>\n"
        ">> Schedule a call\n"
    )
    encoded_body = base64.urlsafe_b64encode(raw_body.encode()).decode()
    message = {
        "id": "msg-1",
        "payload": {
            "headers": [],
            "parts": [{"mimeType": "text/plain", "body": {"data": encoded_body}}],
        },
    }

    reply = _reply_from_gmail_message(message)

    assert reply.body == "Yes, I would be interested."


def test_clean_reply_body_removes_original_message_headers() -> None:
    body = "Interested\n\nFrom: sender@example.com\nSent: Tuesday\nTo: buyer@example.com"

    assert clean_reply_body(body) == "Interested"


def test_clean_reply_body_removes_wrapped_gmail_quote_marker() -> None:
    body = (
        "hii this is testing\n\n"
        "On Wed, Jun 17, 2026 at 12:42 PM Sahil Kumar <sahil@example.com>\n"
        "wrote:\n"
        "> old message"
    )

    assert clean_reply_body(body) == "hii this is testing"


def test_build_outreach_message_adds_deliverability_headers_and_footer() -> None:
    lead = Lead(
        id=1,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        company_name="Example Co",
    )
    draft = EmailDraft(
        id=2,
        lead_id=1,
        subject="Relevant question",
        body="Hi Ada,\n\nNoticed Example Co is growing its sales team.",
        status="approved",
    )

    message = build_outreach_message(
        lead=lead,
        draft=draft,
        sender_email="sahil@example-sales.com",
        sender_name="Sahil Kumar",
        reply_to_email="reply@example-sales.com",
        unsubscribe_url="https://example-sales.com/unsubscribe/ada",
        postal_address="123 Market Street, San Francisco, CA",
    )

    assert message["To"] == "ada@example.com"
    assert message["From"] == "Sahil Kumar <sahil@example-sales.com>"
    assert message["Reply-To"] == "reply@example-sales.com"
    assert message["Date"]
    assert message["Message-ID"].endswith("@example-sales.com>")
    assert message["List-Unsubscribe"] == "<https://example-sales.com/unsubscribe/ada>"
    assert message["X-Auto-Response-Suppress"] == "OOF, AutoReply"
    assert "Unsubscribe: https://example-sales.com/unsubscribe/ada" in message.get_content()
    assert "123 Market Street, San Francisco, CA" in message.get_content()


def test_build_reply_message_adds_threading_headers() -> None:
    message = build_reply_message(
        to_email="buyer@example.com",
        body="Happy to discuss. Does tomorrow work?",
        sender_email="sahil@example-sales.com",
        sender_name="Sahil Kumar",
        subject="Aether AI Growth",
        in_reply_to="<message-1@example.com>",
    )

    assert message["To"] == "buyer@example.com"
    assert message["From"] == "Sahil Kumar <sahil@example-sales.com>"
    assert message["Subject"] == "Re: Aether AI Growth"
    assert message["In-Reply-To"] == "<message-1@example.com>"
    assert message["References"] == "<message-1@example.com>"
    assert message.get_content().strip() == "Happy to discuss. Does tomorrow work?"


def test_gmail_service_send_reply_uses_thread_id() -> None:
    class FakeSendRequest:
        def __init__(self, response: dict[str, str]) -> None:
            self.response = response

        def execute(self) -> dict[str, str]:
            return self.response

    class FakeMessages:
        def __init__(self) -> None:
            self.send_body: dict[str, str] | None = None

        def send(self, *, userId: str, body: dict[str, str]) -> FakeSendRequest:
            assert userId == "me"
            self.send_body = body
            return FakeSendRequest({"id": "sent-123"})

    class FakeUsers:
        def __init__(self, messages: FakeMessages) -> None:
            self._messages = messages

        def messages(self) -> FakeMessages:
            return self._messages

    class FakeGmailApi:
        def __init__(self) -> None:
            self.messages_resource = FakeMessages()

        def users(self) -> FakeUsers:
            return FakeUsers(self.messages_resource)

    fake_api = FakeGmailApi()
    service = GmailService(
        service=fake_api,
        settings=Settings(
            DATABASE_URL="sqlite://",
            GMAIL_SENDER_EMAIL="sahil@example-sales.com",
        ),
    )

    message_id = service.send_reply(
        to_email="buyer@example.com",
        subject="Re: Aether AI Growth",
        body="Thanks, sending a calendar link now.",
        thread_id="thread-123",
        in_reply_to="<message-1@example.com>",
    )

    assert message_id == "sent-123"
    assert fake_api.messages_resource.send_body is not None
    assert fake_api.messages_resource.send_body["threadId"] == "thread-123"
    assert fake_api.messages_resource.send_body["raw"]


def test_workflow_log_row_matches_documented_columns() -> None:
    lead = Lead(
        id=1,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        title="VP Sales",
        company_name="Example Co",
        lead_metadata={},
    )
    draft = EmailDraft(id=2, lead_id=1, subject="Subject", body="Body", status="approved")
    reply = EmailReply(
        id=3,
        lead_id=1,
        from_email="ada@example.com",
        subject="Re: Subject",
        body="Interested",
        sentiment="INTERESTED",
        received_at=datetime.now(UTC),
    )

    row = workflow_log_row(
        lead=lead,
        company_summary="Builds sales software.",
        score=8,
        score_reason="Strong ICP fit.",
        email_draft=draft,
        approval_status=draft.status,
        reply=reply,
    )

    assert row[1:] == [
        "Example Co",
        "ada@example.com",
        "VP Sales",
        8,
        "Strong ICP fit.",
        "Builds sales software.",
        "Subject",
        "Body",
        "approved",
        "INTERESTED",
    ]


def test_workflow_log_row_uses_effective_reply_sentiment() -> None:
    lead = Lead(
        id=1,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        title="VP Sales",
        company_name="Example Co",
        lead_metadata={},
    )
    reply = EmailReply(
        id=3,
        lead_id=1,
        from_email="ada@example.com",
        subject="Re: Subject",
        body="Thanks, but we are not interested.",
        sentiment="INTERESTED",
        received_at=datetime.now(UTC),
    )

    row = workflow_log_row(
        lead=lead,
        company_summary=None,
        score=None,
        score_reason=None,
        email_draft=None,
        approval_status=None,
        reply=reply,
    )

    assert row[-1] == "NOT_INTERESTED"


def test_csv_importer_accepts_numbered_email_columns() -> None:
    importer = LeadCSVImporter(session=object())  # type: ignore[arg-type]

    payload = importer._row_to_payload(
        {
            "First Name": "Ada",
            "Last Name": "Lovelace",
            "Company": "Example Co",
            "Email 1": "ada@example.com",
            "Website": "https://example.com",
        },
        source="csv",
    )

    assert payload["email"] == "ada@example.com"
    assert payload["company_name"] == "Example Co"


def test_gmail_service_reauthorizes_when_refresh_token_is_revoked(monkeypatch, tmp_path) -> None:
    from google.auth.exceptions import RefreshError

    token_path = tmp_path / "token.json"
    token_path.write_text('{"token": "stale"}', encoding="utf-8")
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")

    class StaleCredentials:
        valid = False
        expired = True
        refresh_token = "refresh-token"

        @classmethod
        def from_authorized_user_file(
            cls,
            filename: str,
            scopes: list[str],
        ) -> "StaleCredentials":
            assert filename == str(token_path)
            assert scopes
            return cls()

        def refresh(self, request: object) -> None:
            raise RefreshError("invalid_grant: Token has been expired or revoked.")

    class FreshCredentials:
        def to_json(self) -> str:
            return '{"token": "fresh"}'

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, filename: str, scopes: list[str]) -> "FakeFlow":
            assert filename == str(credentials_path)
            assert scopes
            return cls()

        def run_local_server(self, *, port: int) -> FreshCredentials:
            assert port == 0
            return FreshCredentials()

    def fake_build(api: str, version: str, *, credentials: FreshCredentials) -> str:
        assert api == "gmail"
        assert version == "v1"
        assert isinstance(credentials, FreshCredentials)
        return "gmail-service"

    monkeypatch.setitem(
        sys.modules,
        "google.oauth2.credentials",
        SimpleNamespace(Credentials=StaleCredentials),
    )
    monkeypatch.setitem(
        sys.modules,
        "google_auth_oauthlib.flow",
        SimpleNamespace(InstalledAppFlow=FakeFlow),
    )
    monkeypatch.setitem(
        sys.modules,
        "googleapiclient.discovery",
        SimpleNamespace(build=fake_build),
    )

    service = _build_gmail_service(
        Settings(
            DATABASE_URL="sqlite://",
            GMAIL_CREDENTIALS_FILE=str(credentials_path),
            GMAIL_TOKEN_FILE=str(token_path),
        )
    )

    assert service == "gmail-service"
    assert token_path.read_text(encoding="utf-8") == '{"token": "fresh"}'
