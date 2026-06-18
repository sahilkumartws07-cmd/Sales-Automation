from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from sales_automation.models import Lead
from sales_automation.repositories import LeadRepository, WorkflowLogRepository


@dataclass(frozen=True)
class LeadImportResult:
    created: int
    updated: int
    skipped: int
    errors: list[dict[str, Any]]


class LeadCSVImporter:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.leads = LeadRepository(session)
        self.logs = WorkflowLogRepository(session)

    def import_file(self, csv_path: str | Path, *, source: str = "csv") -> LeadImportResult:
        path = Path(csv_path)
        frame = pd.read_csv(path).fillna("")
        created = 0
        updated = 0
        skipped = 0
        errors: list[dict[str, Any]] = []

        self.logs.record(
            event_type="lead_import.started",
            status="started",
            message=f"Started CSV lead import from {path}.",
            payload={"path": str(path), "rows": int(len(frame))},
        )

        for index, row in frame.iterrows():
            try:
                payload = self._row_to_payload(row.to_dict(), source=source)
                if not payload["email"] or "@" not in payload["email"] or not payload["company_name"]:
                    skipped += 1
                    errors.append({"row": int(index), "error": "valid email and company_name are required"})
                    continue

                existing = self.leads.get_by_email(payload["email"])
                if existing:
                    _update_lead(existing, payload)
                    updated += 1
                else:
                    self.leads.add(Lead(**payload))
                    created += 1
            except Exception as exc:
                skipped += 1
                errors.append({"row": int(index), "error": str(exc)})

        status = "completed" if not errors else "completed_with_errors"
        self.logs.record(
            event_type="lead_import.completed",
            status=status,
            message="CSV lead import finished.",
            payload={"created": created, "updated": updated, "skipped": skipped, "errors": errors},
        )
        return LeadImportResult(created=created, updated=updated, skipped=skipped, errors=errors)

    def _row_to_payload(self, row: dict[str, Any], *, source: str) -> dict[str, Any]:
        normalized = {_normalize_column(key): _clean_value(value) for key, value in row.items()}
        email = _first(normalized, "email", "email_address", "work_email", "email_1", "email_2")
        first_name = _first(normalized, "first_name", "firstname", "given_name")
        last_name = _first(normalized, "last_name", "lastname", "family_name")
        company_name = _first(normalized, "company_name", "company", "account", "organization")
        website_url = _first(normalized, "website", "website_url", "company_website", "url")
        company_domain = _first(normalized, "domain", "company_domain")
        if not company_domain:
            company_domain = _domain_from_url(website_url) or _domain_from_email(email)

        metadata = {
            "website_url": website_url or None,
            "company_domain": company_domain or None,
            "import_source": source,
            "raw": normalized,
        }
        return {
            "first_name": first_name or "Unknown",
            "last_name": last_name or "Unknown",
            "email": email.lower(),
            "title": _first(normalized, "title", "job_title", "role") or None,
            "company_name": company_name or _company_from_domain(company_domain),
            "linkedin_url": _first(normalized, "linkedin", "linkedin_url", "profile_url") or None,
            "source": source,
            "status": _first(normalized, "status") or "new",
            "lead_metadata": metadata,
        }


def _update_lead(lead: Lead, payload: dict[str, Any]) -> None:
    for field in (
        "first_name",
        "last_name",
        "title",
        "company_name",
        "linkedin_url",
        "source",
        "status",
    ):
        setattr(lead, field, payload[field])
    lead.lead_metadata = {**(lead.lead_metadata or {}), **payload["lead_metadata"]}


def _normalize_column(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _clean_value(value: Any) -> str:
    return str(value).strip()


def _first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key, "")
        if value:
            return value
    return ""


def _domain_from_email(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", maxsplit=1)[-1].lower()


def _domain_from_url(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").split("/", maxsplit=1)[0].lower()


def _company_from_domain(domain: str) -> str:
    if not domain:
        return ""
    return domain.split(".", maxsplit=1)[0].replace("-", " ").title()
