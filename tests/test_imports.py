from sales_automation.models import (
    CompanyResearch,
    EmailApproval,
    EmailDraft,
    EmailReply,
    EmailSentMessage,
    Lead,
    LeadScore,
    WorkflowLog,
)


def test_models_import() -> None:
    assert Lead.__tablename__ == "leads"
    assert CompanyResearch.__tablename__ == "company_research"
    assert LeadScore.__tablename__ == "lead_scores"
    assert EmailDraft.__tablename__ == "email_drafts"
    assert EmailApproval.__tablename__ == "email_approvals"
    assert EmailReply.__tablename__ == "email_replies"
    assert EmailSentMessage.__tablename__ == "email_sent_messages"
    assert WorkflowLog.__tablename__ == "workflow_logs"
