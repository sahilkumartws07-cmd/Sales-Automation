# Sales Automation

Production-ready Python 3.11+ project for lead generation workflows using PostgreSQL, SQLAlchemy ORM, Alembic migrations, python-dotenv, structured JSON logging, and OpenAI.

## Features

- SQLAlchemy 2.x ORM models for leads, research, scoring, email drafts, approvals, replies, and workflow logs
- Alembic migration setup with an initial schema migration
- Repository pattern for persistence boundaries
- OpenAI client service for research summaries, lead scoring, and email drafting
- CSV lead ingestion with pandas
- Website research using requests, BeautifulSoup, retries, and OpenAI summaries
- Environment-driven configuration via `python-dotenv`
- Structured JSON logging using the standard logging stack
- Database initialization scripts for local PostgreSQL

## Project Structure

```text
.
├── alembic/
│   ├── env.py
│   └── versions/
├── scripts/
│   ├── init_db.sql
│   └── init_db.sh
├── src/sales_automation/
│   ├── config.py
│   ├── db/
│   ├── logging_config.py
│   ├── models/
│   ├── repositories/
│   └── services/
├── alembic.ini
├── pyproject.toml
└── .env.example
```

## Quickstart

1. Create a virtual environment and install dependencies:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

2. Create local environment config:

```bash
cp .env.example .env
```

3. Create the PostgreSQL role and database:

```bash
./scripts/init_db.sh
```

4. Run migrations:

```bash
alembic upgrade head
```

5. Verify database connectivity:

```bash
sales-automation db-check
```

## Import Leads From CSV

The importer accepts common column variants such as `email`, `work_email`, `first_name`, `last_name`, `company`, `company_name`, `title`, `website`, `website_url`, `company_website`, `domain`, and `linkedin_url`.

```bash
sales-automation import-leads sample_leads.csv --source outbound-list
```

To import leads and immediately research company websites:

```bash
sales-automation import-leads sample_leads.csv --source outbound-list --research --research-limit 50
```

To research pending leads later:

```bash
sales-automation research-websites --limit 100
```

Website research uses `requests` with bounded retries, parses HTML with BeautifulSoup, extracts meaningful headings/body content, generates a company summary with OpenAI, stores it in `company_research`, and records workflow events in `workflow_logs`.

## Score Leads With AI

After website/company research exists in PostgreSQL, score unscored leads with OpenAI:

```bash
sales-automation score-leads --limit 100
```

The AI scorer reads the latest `company_research` record for each unscored lead, sends the company summary, signals, and pain points to OpenAI using a strict structured JSON schema, saves a 1-10 score in `lead_scores.score`, stores `HOT`, `WARM`, or `COLD` in `lead_scores.grade`, writes the score reason to `lead_scores.rationale`, and records workflow events in `workflow_logs`.

## NVIDIA AI Usage

Set `NVIDIA_API_KEY` and optionally `NVIDIA_MODEL` / `NVIDIA_BASE_URL` in `.env`. The AI integration uses NVIDIA NIM through an OpenAI-compatible client API and returns typed dictionaries suitable for storing in the ORM models.

## Development

Run linting:

```bash
ruff check .
```

Create a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```


python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env


./scripts/init_db.sh
alembic upgrade head
sales-automation db-check


sales-automation import-leads leads.csv --source linkedin --research --research-limit 50


sales-automation score-leads --limit 100
sales-automation generate-emails --categories HOT WARM



sales-automation approval-server

for id in 2 3 4 5 6 7 8 9; do
  curl -X POST http://127.0.0.1:8000/drafts/$id/approve \
    -H "Content-Type: application/json" \
    -d '{"approved_by": "you"}'
done


for id in 2 3 4 5 6 7 8 9; do
  sales-automation send-approved $id
done



for id in 1 2 3 4 5 6 7 8 9 10 11 12 13 14; do
  sales-automation log-sheet $id
done




source .venv/bin/activate && python3 -c "
from sales_automation.services.slack_notification import SlackNotificationService
from sales_automation.models import Lead, LeadScore
lead = Lead(id=1, first_name='John', last_name='Smith', company_name='TechCorp')
score = LeadScore(id=1, score=5, grade='HOT', rationale='Test notification')
slack = SlackNotificationService()
slack.notify_hot_lead(lead=lead, score=score)
"


grep -E 'GMAIL|SLACK|GOOGLE_SHEETS' .env | grep -v 'None\|Sheet1'

uvicorn sales_automation.api.main:app --host 0.0.0.0 --port 8000
