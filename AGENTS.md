# Working on Career Scout

Career Scout is a configurable personal vacancy-collection and resume-review tool
for the Russian job market. International search requires different sources and a
separate workflow and is outside the current product scope.
Read README.md and docs/README.md, then the relevant contract before changing code.

## Scope and data

- The primary interface is career-scout (agent_cli), with arbitrary full resumes,
  goals, preferences and explicit fact_rules. No profession, city, pay floor or
  experience ceiling belongs in library defaults.
- Legacy collector/analysis commands remain for compatibility. Their four-role
  example configurations are optional demonstrations, not candidate defaults.
- Keep resumes, source credentials, platform data, model receipts, application
  history and SQLite backups in ignored private directories. Never commit them.
- Public source credentials are empty examples. Each installation must explicitly
  configure its own access and data-storage permissions, including SuperJob.
- Never modify a user's deployed data, authentication, containers, provider settings
  or schedule as part of a code/documentation change.

## Architecture invariants

- Collection, pagination, deduplication, retry, normalization, queue admission,
  budgets and persistence are code. Model reasoning evaluates semantic fit.
- Each worker sees one full resume and up to20independent complete vacancies;
  at most4concurrent slots. Do not transfer experience between resumes/cards.
- New context-1 results are PASS/UNCERTAIN/REJECT, questions=[]. Unknown is not
  rejection. Invalid/unknown external delivery is not permission for blind retries.
- Multi-resume routing uses an explicitly configured external Decisions connector;
  one resume skips routing when automatic exclusion is disabled.
- Source 401/403/429/schema drift remain observable errors. Do not bypass access
  restrictions or turn an error into an empty successful market result.
- Collector SQLite v5 migrations preserve old payloads/content hashes with backup.
  Evaluation reads collector snapshots and writes a separate context-version DB.
- Preserve completed evaluations, source identities, immutable inputs and request-id
  receipts. Resume completes the old run; prepare selects the next unadmitted input.
- Evaluators use an explicit subscription CLI or API provider. Preserve native
  logins; store API environment references only, never key values in inputs,
  databases, images or repositories. No automatic applications.

## Development

Python3.12, uv.lock, src-layout, httpx/Pydantic, local SQLite. Generic context-1
queues use Python coordination; legacy first-PASS retains LangGraph.
Source/module map: src/career_scout/README.md. Workflow: CONTRIBUTING.md.

Required offline checks:

```sh
uv run --no-sync python scripts/check_repository.py
uv run --no-sync python scripts/check_public_distribution.py
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest -m "not live"
uv build
```

For packaging/storage/main-flow changes also build Docker and run
scripts/check_container.py and scripts/check_product.py. They use isolated
synthetic data, never production volumes or model subscriptions. Live acceptance
requires its own finite budget and is distinct from offline tests.

Update docs/schemas/examples with changed contracts. Do not claim broad semantic
accuracy from schema checks or a small synthetic control set.
