# Career Scout

**Turn job listings into a shortlist grounded in your resumes.**

[Русский](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/README.md) · [Getting started](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/quickstart.md) ·
[Architecture](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/architecture.md) · [0.1 audit](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/product-audit-0.1.md)

Career Scout collects complete vacancies from four Russian job platforms, stores
source records in SQLite and matches them against full resumes and candidate
preferences. Its JSON CLI lets a person or an agent configure a personal search,
resume interrupted work and read recommendations with supporting evidence.

**0.1.0 alpha · MIT · Linux · Python 3.12.** The implementation is on
`feat/evaluation-workers`; this is not a stable 1.x release.

## The workflow

1. Supply resumes, goals, preferences and search queries.
2. Collect full records with pagination, finite budgets and durable checkpoints.
3. Apply explicit factual constraints, preserving unknown conditions.
4. Route to one or two resumes through an external Decisions connector. A single
   resume needs no routing call when automatic exclusion is disabled.
5. Evaluate independent vacancies through the user's Codex subscription.
6. Validate result identities, evidence references and preference coverage.
7. Return PASS recommendations; include UNCERTAIN only when explicitly requested.

Each Codex batch contains one resume and up to 20 vacancies, with up to four
concurrent slots. Collection and queue management do not use an LLM. Applications
are submitted manually; an imported application history suppresses completed ones.

## Run it

```sh
git clone --branch feat/evaluation-workers https://github.com/timsmykov/career-scout.git
cd career-scout
uv sync --locked
uv run --no-sync career-scout version
uv run --no-sync career-scout schema
```

Follow the [first-run tutorial](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/quickstart.md), currently in Russian. Use your
own platform credentials and check the relevant data-use permissions. Install and
authenticate Codex on the host; neither Codex nor its login is bundled in Docker.
Multi-resume routing requires a separately configured compatible Decisions
connector, which is not distributed with this package yet.

HH, SuperJob and Trudvsem use official APIs. Habr uses frontend JSON and HTML,
with no confirmed public search API contract. Old full descriptions are retained
rather than refreshed automatically; check the original before applying.

## Evidence and limitations

The 0.1 audit recorded 484 passing offline tests, package builds, Docker persistence
and an installed-package workflow with explicit HTTP/Codex simulators. Separate
bounded live probes checked search and full details on all four sources. These
checks establish execution, not broad semantic accuracy. Automatic probability-based
Decisions exclusion is disabled by default until quality is calibrated.

[Detailed audit](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/product-audit-0.1.md) · [Documentation](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/docs/README.md) ·
[Contributing](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/CONTRIBUTING.md) · [Security](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/SECURITY.md) · [MIT license](https://github.com/timsmykov/career-scout/blob/feat/evaluation-workers/LICENSE)
