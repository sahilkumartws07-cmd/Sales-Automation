from __future__ import annotations

from typing import Any

from fastapi import Body
from sales_automation.db.session import SessionLocal
from sales_automation.services.email_approval import EmailApprovalService


def create_app() -> Any:
    try:
        from fastapi import FastAPI
        from pydantic import BaseModel
    except ImportError as exc:
        raise RuntimeError("Install FastAPI to run the approval server") from exc

    class DecisionRequest(BaseModel):
        approved_by: str = "sales"
        notes: str | None = None

    class EditRequest(BaseModel):
        subject: str | None = None
        body: str | None = None

    app = FastAPI(title="Sales Automation Approval")

    @app.get("/drafts/pending")
    def pending_drafts(limit: int = 100) -> list[dict[str, Any]]:
        with SessionLocal() as session:
            drafts = EmailApprovalService(session).list_pending(limit=limit)
            return [
                {
                    "id": draft.id,
                    "lead_id": draft.lead_id,
                    "subject": draft.subject,
                    "body": draft.body,
                    "status": draft.status,
                }
                for draft in drafts
            ]

    @app.post("/drafts/{draft_id}/approve")
    def approve_draft(draft_id: int, decision: dict[str, Any] = Body(...)) -> dict[str, Any]:
        with SessionLocal() as session:
            draft = EmailApprovalService(session).approve(
                draft_id,
                approved_by=decision.get("approved_by", "sales"),
                notes=decision.get("notes"),
            )
            session.commit()
            return {"draft_id": draft.id, "status": draft.status}

    @app.post("/drafts/{draft_id}/reject")
    def reject_draft(draft_id: int, decision: dict[str, Any] = Body(...)) -> dict[str, Any]:
        with SessionLocal() as session:
            draft = EmailApprovalService(session).reject(
                draft_id,
                approved_by=decision.get("approved_by", "sales"),
                notes=decision.get("notes"),
            )
            session.commit()
            return {"draft_id": draft.id, "status": draft.status}

    @app.post("/drafts/{draft_id}/edit")
    def edit_draft(draft_id: int, request: dict[str, Any] = Body(...)) -> dict[str, Any]:
        with SessionLocal() as session:
            draft = EmailApprovalService(session).edit(
                draft_id,
                subject=request.get("subject"),
                body=request.get("body"),
            )
            session.commit()
            return {"draft_id": draft.id, "status": draft.status}

    @app.post("/drafts/{draft_id}/send")
    def send_draft(draft_id: int) -> dict[str, Any]:
        with SessionLocal() as session:
            draft = EmailApprovalService(session).send_approved(draft_id)
            session.commit()
            return {"draft_id": draft.id, "status": draft.status}

    return app


app = create_app()
