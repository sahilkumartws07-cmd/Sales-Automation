from __future__ import annotations

from typing import Any

from sales_automation.models import Lead


def lead_to_payload(
    lead: Lead,
    *,
    title_default: str | None = None,
    include_source: bool = True,
    include_metadata: bool = True,
) -> dict[str, Any]:
    title = lead.title if lead.title is not None else title_default
    payload: dict[str, Any] = {
        "id": lead.id,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "email": lead.email,
        "title": title,
        "company_name": lead.company_name,
    }
    if include_source:
        payload["source"] = lead.source
    if include_metadata:
        payload["metadata"] = lead.lead_metadata
    return payload
