from __future__ import annotations

import argparse
import logging

from sqlalchemy import text

from sales_automation.config import get_settings
from sales_automation.db.session import SessionLocal
from sales_automation.logging_config import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(prog="sales-automation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("db-check", help="Verify database connectivity")

    import_parser = subparsers.add_parser("import-leads", help="Import leads from a CSV file")
    import_parser.add_argument("csv_path")
    import_parser.add_argument("--source", default="csv")
    import_parser.add_argument(
        "--research", action="store_true", help="Run website research after import"
    )
    import_parser.add_argument("--research-limit", type=int, default=100)

    research_parser = subparsers.add_parser(
        "research-websites", help="Research pending lead websites"
    )
    research_parser.add_argument("--limit", type=int, default=100)

    score_parser = subparsers.add_parser("score-leads", help="Score researched leads with OpenAI")
    score_parser.add_argument("--limit", type=int, default=100)

    email_parser = subparsers.add_parser(
        "generate-emails", help="Generate cold emails for HOT and WARM leads"
    )
    email_parser.add_argument(
        "--categories",
        nargs="+",
        default=["HOT", "WARM"],
        help="Lead categories to process (default: HOT WARM)",
    )
    email_parser.add_argument("--limit", type=int, default=100)
    email_parser.add_argument("--notify-slack", action="store_true")

    replies_parser = subparsers.add_parser(
        "classify-replies", help="Read and classify Gmail replies"
    )
    replies_parser.add_argument("--query", default="is:unread")
    replies_parser.add_argument("--limit", type=int, default=100)
    replies_parser.add_argument("--notify-slack", action="store_true")

    send_parser = subparsers.add_parser("send-approved", help="Send an approved email draft")
    send_parser.add_argument("draft_id", type=int)

    sheets_parser = subparsers.add_parser(
        "log-sheet", help="Append a lead workflow row to Google Sheets"
    )
    sheets_parser.add_argument("lead_id", type=int)

    approval_parser = subparsers.add_parser(
        "approval-server", help="Run the email approval FastAPI server"
    )
    approval_parser.add_argument("--host", default="127.0.0.1")
    approval_parser.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()

    if args.command == "db-check":
        db_check()
    elif args.command == "import-leads":
        import_leads(
            args.csv_path, source=args.source, research=args.research, limit=args.research_limit
        )
    elif args.command == "research-websites":
        research_websites(limit=args.limit)
    elif args.command == "score-leads":
        score_leads(limit=args.limit)
    elif args.command == "generate-emails":
        generate_emails(
            categories=args.categories,
            limit=args.limit,
            notify_slack=args.notify_slack,
        )
    elif args.command == "classify-replies":
        classify_replies(query=args.query, limit=args.limit, notify_slack=args.notify_slack)
    elif args.command == "send-approved":
        send_approved(draft_id=args.draft_id)
    elif args.command == "log-sheet":
        log_sheet(lead_id=args.lead_id)
    elif args.command == "approval-server":
        run_approval_server(host=args.host, port=args.port)


def db_check() -> None:
    with SessionLocal() as session:
        value = session.scalar(text("select 1"))
    logger.info("database_check_completed", extra={"result": value})


def import_leads(csv_path: str, *, source: str, research: bool, limit: int) -> None:
    from sales_automation.services.csv_importer import LeadCSVImporter

    with SessionLocal() as session:
        result = LeadCSVImporter(session).import_file(csv_path, source=source)
        session.commit()
        logger.info(
            "lead_import_completed",
            extra={
                "created_count": result.created,
                "updated_count": result.updated,
                "skipped_count": result.skipped,
                "error_count": len(result.errors),
            },
        )
        if research:
            from sales_automation.services.website_research import WebsiteResearchService

            research_result = WebsiteResearchService(session).research_pending_leads(limit=limit)
            logger.info("website_research_batch_completed", extra=research_result)


def research_websites(*, limit: int) -> None:
    from sales_automation.services.website_research import WebsiteResearchService

    with SessionLocal() as session:
        result = WebsiteResearchService(session).research_pending_leads(limit=limit)
        logger.info("website_research_batch_completed", extra=result)


def score_leads(*, limit: int) -> None:
    from sales_automation.services.lead_scoring import AILeadScoringService

    with SessionLocal() as session:
        result = AILeadScoringService(session).score_unscored_leads(limit=limit)
        logger.info(
            "lead_scoring_batch_completed",
            extra={
                "status": result.status,
                "message": result.message,
                "scored": result.scored,
                "skipped": result.skipped,
                "failed": result.failed,
            },
        )


def generate_emails(*, categories: list[str], limit: int, notify_slack: bool = False) -> None:
    from sales_automation.services.email_generation import ColdEmailGenerationService

    with SessionLocal() as session:
        result = ColdEmailGenerationService(session).generate_emails_for_qualified_leads(
            categories=categories,
            limit=limit,
            notify_slack=notify_slack,
        )
        logger.info(
            "email_generation_batch_completed",
            extra={
                "generated": result.generated,
                "skipped": result.skipped,
                "failed": result.failed,
                "error_count": len(result.errors),
            },
        )


def classify_replies(*, query: str, limit: int, notify_slack: bool = False) -> None:
    from sales_automation.services.reply_classification import ReplyClassificationService

    with SessionLocal() as session:
        result = ReplyClassificationService(session).classify_gmail_replies(
            query=query,
            limit=limit,
            notify_slack=notify_slack,
        )
        logger.info(
            "reply_classification_batch_completed",
            extra={
                "classified": result.classified,
                "skipped": result.skipped,
                "failed": result.failed,
                "error_count": len(result.errors),
            },
        )


def send_approved(*, draft_id: int) -> None:
    from sales_automation.services.email_approval import EmailApprovalService

    with SessionLocal() as session:
        draft = EmailApprovalService(session).send_approved(draft_id)
        session.commit()
        logger.info("approved_email_sent", extra={"draft_id": draft.id, "status": draft.status})


def log_sheet(*, lead_id: int) -> None:
    from sales_automation.services.google_sheets_logging import GoogleSheetsLoggingService

    with SessionLocal() as session:
        row = GoogleSheetsLoggingService(session).append_lead_row(lead_id)
        session.commit()
        logger.info("google_sheet_row_appended", extra={"lead_id": lead_id, "column_count": len(row)})


def run_approval_server(*, host: str, port: int) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Install uvicorn to run the approval server") from exc
    uvicorn.run("sales_automation.services.approval_app:app", host=host, port=port)


if __name__ == "__main__":
    main()
