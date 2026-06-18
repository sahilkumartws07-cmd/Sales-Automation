from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AuthService": ("sales_automation.services.auth", "AuthService"),
    "AuthServiceError": ("sales_automation.services.auth", "AuthServiceError"),
    "LeadCSVImporter": ("sales_automation.services.csv_importer", "LeadCSVImporter"),
    "LeadImportResult": ("sales_automation.services.csv_importer", "LeadImportResult"),
    "ColdEmailGenerationService": (
        "sales_automation.services.email_generation",
        "ColdEmailGenerationService",
    ),
    "EmailGenerationResult": (
        "sales_automation.services.email_generation",
        "EmailGenerationResult",
    ),
    "ApprovalLink": ("sales_automation.services.email_approval", "ApprovalLink"),
    "EmailApprovalService": ("sales_automation.services.email_approval", "EmailApprovalService"),
    "GmailReplyMessage": ("sales_automation.services.gmail_service", "GmailReplyMessage"),
    "GmailService": ("sales_automation.services.gmail_service", "GmailService"),
    "GoogleSheetsLoggingService": (
        "sales_automation.services.google_sheets_logging",
        "GoogleSheetsLoggingService",
    ),
    "AILeadScoringService": ("sales_automation.services.lead_scoring", "AILeadScoringService"),
    "LeadScoringResult": ("sales_automation.services.lead_scoring", "LeadScoringResult"),
    "NvidiaAIUtility": ("sales_automation.services.nvidia_ai_utils", "NvidiaAIUtility"),
    "OpenAIService": ("sales_automation.services.openai_service", "OpenAIService"),
    "ReplyClassificationResult": (
        "sales_automation.services.reply_classification",
        "ReplyClassificationResult",
    ),
    "ReplyClassificationService": (
        "sales_automation.services.reply_classification",
        "ReplyClassificationService",
    ),
    "SalesWorkflowService": ("sales_automation.services.sales_workflow", "SalesWorkflowService"),
    "SlackNotificationService": (
        "sales_automation.services.slack_notification",
        "SlackNotificationService",
    ),
    "WebsiteContent": ("sales_automation.services.website_research", "WebsiteContent"),
    "WebsiteResearchService": (
        "sales_automation.services.website_research",
        "WebsiteResearchService",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
