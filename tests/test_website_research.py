from bs4 import BeautifulSoup
import pytest
import requests

from sales_automation.config import Settings
from sales_automation.services.website_research import WebsiteResearchService, extract_meaningful_content


def test_extract_meaningful_content_removes_navigation_and_scripts() -> None:
    soup = BeautifulSoup(
        """
        <html>
          <head><script>alert("x")</script></head>
          <body>
            <nav>This should not be included in extracted research content.</nav>
            <h1>Industrial workflow automation for revenue teams</h1>
            <p>Acme helps sales teams qualify accounts, understand buying signals, and
            draft personalized outreach from verified company context.</p>
            <footer>This footer should disappear from extracted content.</footer>
          </body>
        </html>
        """,
        "html.parser",
    )

    content = extract_meaningful_content(soup, max_chars=500)

    assert "Industrial workflow automation" in content
    assert "qualify accounts" in content
    assert "This should not be included" not in content
    assert "alert" not in content


def test_fetch_and_extract_falls_back_to_http_when_https_ssl_fails() -> None:
    class FakeHTTPClient:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str, **_: object) -> requests.Response:
            self.urls.append(url)
            if url.startswith("https://"):
                raise requests.exceptions.SSLError("tlsv1 alert internal error")

            response = requests.Response()
            response.status_code = 200
            response.url = url
            response.headers["content-type"] = "text/html"
            response._content = (
                b"<html><head><title>Quantum Data</title></head>"
                b"<body><h1>Reliable analytics workflow automation for revenue teams</h1></body>"
                b"</html>"
            )
            return response

    settings = Settings(
        DATABASE_URL="postgresql+psycopg://sales:sales@localhost:5432/sales_automation",
        HTTP_MAX_RETRIES=1,
    )
    service = WebsiteResearchService(
        session=object(),  # type: ignore[arg-type]
        settings=settings,
        openai_service=object(),  # type: ignore[arg-type]
        http_client=FakeHTTPClient(),  # type: ignore[arg-type]
    )

    website = service.fetch_and_extract("quantumdata.net")

    assert website.url == "http://quantumdata.net"
    assert website.domain == "quantumdata.net"
    assert website.title == "Quantum Data"
    assert "analytics workflow automation" in website.content


def test_fetch_and_extract_reraises_ssl_error_for_explicit_http_url() -> None:
    class FakeHTTPClient:
        def get(self, url: str, **_: object) -> requests.Response:
            raise requests.exceptions.SSLError("unexpected ssl error")

    settings = Settings(
        DATABASE_URL="postgresql+psycopg://sales:sales@localhost:5432/sales_automation",
        HTTP_MAX_RETRIES=1,
    )
    service = WebsiteResearchService(
        session=object(),  # type: ignore[arg-type]
        settings=settings,
        openai_service=object(),  # type: ignore[arg-type]
        http_client=FakeHTTPClient(),  # type: ignore[arg-type]
    )

    with pytest.raises(requests.exceptions.SSLError):
        service.fetch_and_extract("http://quantumdata.net")
