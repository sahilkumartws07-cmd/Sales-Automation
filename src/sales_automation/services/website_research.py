from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from time import monotonic
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests
from sqlalchemy.orm import Session
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from sales_automation.config import Settings, get_settings
from sales_automation.models import CompanyResearch, Lead
from sales_automation.repositories import (
    CompanyResearchRepository,
    LeadRepository,
    WorkflowLogRepository,
)
from sales_automation.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebsiteContent:
    url: str
    domain: str
    title: str | None
    meta_description: str | None
    content: str


class WebsiteResearchService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        openai_service: OpenAIService | None = None,
        http_client: requests.Session | None = None,
        ai_fallback_enabled: bool = True,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.openai_service = openai_service or OpenAIService(self.settings)
        self.http_client = http_client or requests.Session()
        self.ai_fallback_enabled = ai_fallback_enabled
        self.leads = LeadRepository(session)
        self.research = CompanyResearchRepository(session)
        self.logs = WorkflowLogRepository(session)

    def research_lead(
        self,
        lead_id: int,
        *,
        research_cache: dict[str, dict[str, Any]] | None = None,
    ) -> CompanyResearch:
        lead = self.session.get(Lead, lead_id, populate_existing=True)
        if lead is None:
            raise ValueError(f"Lead not found: {lead_id}")

        try:
            website_url = _website_url_for_lead(lead)
            if not website_url:
                self.logs.record(
                    lead_id=lead.id,
                    event_type="website_research.skipped",
                    status="skipped",
                    message="No website_url or company_domain available for lead.",
                    payload={"lead_id": lead.id},
                )
                stored = self.research.add(
                    CompanyResearch(
                        lead_id=lead.id,
                        company_domain=None,
                        website_url=None,
                        extracted_content=None,
                        summary="",
                        pain_points=[],
                        signals=[],
                        sources=[],
                        model=self.openai_service.settings.nvidia_model,
                    )
                )
                return stored

            cache_key = _research_cache_key(website_url)
            summary_payload = (research_cache or {}).get(cache_key)
            if summary_payload is None:
                try:
                    website = self.fetch_and_extract(website_url)
                    summary_payload = self.openai_service.summarize_website(
                        company_name=lead.company_name,
                        website_url=website.url,
                        content=website.content,
                    )
                    summary_payload["_source_type"] = "website"
                    summary_payload["_source_url"] = website.url
                    summary_payload["_company_domain"] = website.domain
                    summary_payload["_extracted_content"] = website.content
                except Exception as exc:
                    if self.ai_fallback_enabled:
                        summary_payload = self.openai_service.research_company(
                            company_name=lead.company_name,
                            lead_title=None,
                        )
                        summary_payload["_source_type"] = "ai_fallback"
                        summary_payload["_source_url"] = website_url
                        summary_payload["_company_domain"] = _domain_from_url(website_url)
                        summary_payload["_extracted_content"] = None
                    else:
                        logger.warning(
                            "website_research_ai_skipped",
                            extra={"lead_id": lead_id, "error": str(exc)},
                        )
                        summary_payload = _unavailable_research_payload(
                            company_name=lead.company_name,
                            website_url=website_url,
                            error=str(exc),
                        )
                if research_cache is not None:
                    research_cache[cache_key] = summary_payload

            stored = self.research.add(
                CompanyResearch(
                    lead_id=lead.id,
                    company_domain=summary_payload.get("_company_domain"),
                    website_url=website_url,
                    extracted_content=summary_payload.get("_extracted_content"),
                    summary=str(summary_payload.get("summary", "")),
                    pain_points=summary_payload.get("pain_points", []),
                    signals=summary_payload.get("signals", []),
                    sources=[
                        {
                            "type": summary_payload.get("_source_type", "ai_fallback"),
                            "url": summary_payload.get("_source_url", website_url),
                        }
                    ],
                    model=self.openai_service.settings.nvidia_model,
                )
            )
            self.logs.record(
                lead_id=lead.id,
                event_type="website_research.completed",
                status="completed",
                message="Website research completed.",
                payload={"website_url": website_url},
            )
            return stored
        except Exception as exc:
            logger.warning(
                "website_research_failed",
                extra={"lead_id": lead_id, "error": str(exc)},
            )
            raise

    def research_pending_leads(
        self,
        *,
        limit: int = 100,
        max_seconds: float | None = None,
    ) -> dict[str, int | bool]:
        processed = 0
        failed = 0
        timed_out = False
        started_at = monotonic()
        research_cache: dict[str, dict[str, Any]] = {}
        for lead in self.leads.list_by_status("new", limit=limit):
            if max_seconds is not None and monotonic() - started_at >= max_seconds:
                timed_out = True
                break

            lead_id = lead.id
            company_name = lead.company_name
            try:
                self.research_lead(lead_id, research_cache=research_cache)
                lead.status = "researched"
                processed += 1
                self.session.commit()
            except Exception as exc:
                self.session.rollback()
                log_lead_id = lead_id if self._lead_exists(lead_id) else None
                try:
                    self.logs.record(
                        lead_id=log_lead_id,
                        event_type="website_research.failed",
                        status="failed",
                        message=str(exc),
                        payload={"lead_id": lead_id, "company_name": company_name},
                    )
                    self.session.commit()
                except Exception:
                    self.session.rollback()
                    logger.exception("website_research_failure_log_failed", extra={"lead_id": lead_id})
                failed += 1
        return {"processed": processed, "failed": failed, "timed_out": timed_out}

    def _lead_exists(self, lead_id: int) -> bool:
        return self.session.get(Lead, lead_id, populate_existing=True) is not None

    def fetch_and_extract(self, website_url: str) -> WebsiteContent:
        url = _normalize_url(website_url)
        try:
            response = self._get_with_retries(url)
        except requests.exceptions.SSLError:
            fallback_url = _http_fallback_url(url)
            if fallback_url == url:
                raise
            response = self._get_with_retries(fallback_url)
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and content_type:
            raise ValueError(f"Unsupported content type: {content_type}")

        soup = BeautifulSoup(response.text, "html.parser")
        return WebsiteContent(
            url=response.url,
            domain=urlparse(response.url).netloc.lower(),
            title=_page_title(soup),
            meta_description=_meta_description(soup),
            content=extract_meaningful_content(soup, max_chars=self.settings.website_max_content_chars),
        )

    def _get_with_retries(self, url: str) -> requests.Response:
        retryer = Retrying(
            stop=stop_after_attempt(self.settings.http_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(requests.RequestException),
            reraise=True,
        )
        for attempt in retryer:
            with attempt:
                response = self.http_client.get(
                    url,
                    timeout=self.settings.http_timeout_seconds,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 sales-automation-research/0.1 "
                            "(compatible; website research)"
                        )
                    },
                )
                response.raise_for_status()
                return response
        raise RuntimeError("Retry loop exited without a response")


def extract_meaningful_content(soup: BeautifulSoup, *, max_chars: int) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "form", "nav", "footer", "header"]):
        tag.decompose()

    chunks: list[str] = []
    for selector in ("h1", "h2", "h3", "p", "li"):
        for element in soup.select(selector):
            text = _clean_text(element.get_text(" ", strip=True))
            if len(text) >= 30:
                chunks.append(text)

    deduped = list(dict.fromkeys(chunks))
    content = "\n".join(deduped)
    return content[:max_chars]


def _unavailable_research_payload(
    *,
    company_name: str,
    website_url: str,
    error: str,
) -> dict[str, Any]:
    return {
        "summary": (
            f"Website research for {company_name} could not be completed within the API "
            "time limit. Retry research later if deeper company context is needed."
        ),
        "pain_points": [],
        "signals": [],
        "_source_type": "unavailable",
        "_source_url": website_url,
        "_company_domain": _domain_from_url(website_url),
        "_extracted_content": None,
        "_error": error[:500],
    }


def _website_url_for_lead(lead: Lead) -> str:
    metadata = lead.lead_metadata or {}
    return str(metadata.get("website_url") or metadata.get("company_domain") or "")


def _research_cache_key(value: str) -> str:
    return _domain_from_url(value) or value.strip().lower()


def _domain_from_url(value: str) -> str:
    parsed = urlparse(_normalize_url(value))
    domain = parsed.netloc or parsed.path.split("/", maxsplit=1)[0]
    return domain.lower().removeprefix("www.")


def _normalize_url(value: str) -> str:
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        return f"https://{value}"
    return value


def _http_fallback_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https":
        return value
    return parsed._replace(scheme="http").geturl()


def _page_title(soup: BeautifulSoup) -> str | None:
    if soup.title and soup.title.string:
        return _clean_text(soup.title.string)
    return None


def _meta_description(soup: BeautifulSoup) -> str | None:
    tag = soup.find("meta", attrs={"name": "description"})
    if not tag:
        return None
    content = tag.get("content")
    return _clean_text(str(content)) if content else None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
