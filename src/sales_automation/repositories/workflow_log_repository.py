from __future__ import annotations

from sales_automation.models import WorkflowLog
from sales_automation.repositories.base import BaseRepository


class WorkflowLogRepository(BaseRepository[WorkflowLog]):
    model = WorkflowLog

    def record(
        self,
        *,
        event_type: str,
        status: str,
        message: str,
        lead_id: int | None = None,
        payload: dict | None = None,
    ) -> WorkflowLog:
        log = WorkflowLog(
            lead_id=lead_id,
            event_type=event_type,
            status=status,
            message=message,
            payload=payload or {},
        )
        return self.add(log)

