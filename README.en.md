# Career Scout

**Turn job listings into a shortlist grounded in your resumes.**

[Русский](https://github.com/timsmykov/career-scout/blob/main/README.md) · [Getting started](https://github.com/timsmykov/career-scout/blob/main/docs/quickstart.md) ·
[Architecture](https://github.com/timsmykov/career-scout/blob/main/docs/architecture.md) · [0.1 audit](https://github.com/timsmykov/career-scout/blob/main/docs/product-audit-0.1.md)

Career Scout collects complete vacancies from four Russian job platforms, stores
source records in SQLite and matches them against full resumes and candidate
preferences. Its JSON CLI lets a person or an agent configure a personal search,
resume interrupted work and read recommendations with supporting evidence.

**0.1.0 alpha · MIT · Linux · Python 3.12.** The configurable implementation is on main; this is not a stable 1.x release.

## The workflow

1. Supply resumes, goals, preferences and search queries.
2. Collect full records with pagination, finite budgets and durable checkpoints.
3. Apply explicit factual constraints, preserving unknown conditions.
4. Route to one or two resumes through an external Decisions connector. A single
   resume needs no routing call when automatic exclusion is disabled.
5. Evaluate independent vacancies through a configured subscription or API provider.
6. Validate result identities, evidence references and preference coverage.
7. Return PASS recommendations; include UNCERTAIN only when explicitly requested.

Each evaluation batch contains one resume and up to 20 vacancies, with up to four
concurrent slots. Collection and queue management do not use an LLM. Applications
are submitted manually; an imported application history suppresses completed ones.

## Run it

```sh
git clone https://github.com/timsmykov/career-scout.git
cd career-scout
uv sync --locked
uv run --no-sync career-scout version
uv run --no-sync career-scout schema
```

Follow the [first-run tutorial](https://github.com/timsmykov/career-scout/blob/main/docs/quickstart.md), currently in Russian. Use your
own platform credentials and check the relevant data-use permissions. Choose an API provider or authenticate Codex/Claude Code on the host; native
CLIs and their logins are not bundled in Docker.
Multi-resume routing requires a separately configured compatible Decisions
connector, which is not distributed with this package yet.

HH, SuperJob and Trudvsem use official APIs. Habr uses frontend JSON and HTML,
with no confirmed public search API contract. Old full descriptions are retained
rather than refreshed automatically; check the original before applying.

## Configure your own search

No candidate profile is bundled: supply your own full resumes, preferences,
queries, geography, credentials and workspace. Profession and experience are not
fixed enums in the personal workflow. The public TOML has no domain or experience
ceiling, requires explicit queries and leaves SuperJob storage permission off.
Start with one resume to avoid the external routing dependency.
[Personalization guide](docs/personalization.md).

## Evidence and limitations

The public-alpha validation recorded 489 passing offline tests, package builds, Docker persistence
and an installed-package workflow with explicit HTTP/Codex simulators. Separate
bounded live probes checked search and full details on all four sources. These
checks establish execution, not broad semantic accuracy. Automatic probability-based
Decisions exclusion is disabled by default until quality is calibrated.

[Detailed audit](https://github.com/timsmykov/career-scout/blob/main/docs/product-audit-0.1.md) · [Documentation](https://github.com/timsmykov/career-scout/blob/main/docs/README.md) ·
[Contributing](https://github.com/timsmykov/career-scout/blob/main/CONTRIBUTING.md) · [Security](https://github.com/timsmykov/career-scout/blob/main/SECURITY.md) · [MIT license](https://github.com/timsmykov/career-scout/blob/main/LICENSE)

## Agent skill and model providers

Install the bundled skill with `career-scout skill install --agent codex` or
`--agent claude`. It preserves existing custom skills and ships in the wheel.
See [agent installation](docs/agent-skill.md) and [provider setup](docs/model-providers.md).
Evaluators support Codex/ChatGPT and Claude Code native subscriptions, plus OpenAI,
Anthropic, Gemini, OpenRouter, DeepSeek, Mistral, xAI, Groq, Together and Fireworks APIs.
API keys stay in the environment or private env file; only variable names enter
configuration. Offline protocol tests are distinct from live model validation.
