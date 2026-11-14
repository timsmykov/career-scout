# OpenBot Career Assistant integration

The collector and evaluator remain the owners of the vacancy catalogue, immutable originals, Decisions routing and Codex batch evaluations. The OpenBot assistant is an on-demand client for selection, resumes, application preparation, employer correspondence and interviews. It does not collect or repeat the batch rubric.

`python -m career_scout.assistant_access --collector PATH --analysis PATH --config PATH COMMAND` provides:

| Command | Behavior |
|---|---|
| `shortlist [--profile PROFILE] [--unseen] [--limit N]` | Existing validated recommendations, with current version/freshness rules and confirmed user-history exclusions |
| `catalog --query TEXT [--source SOURCE] [--limit N] [--offset N]` | Literal Unicode-aware title/company search, compact candidates and pagination; no fit assertion |
| `card --source SOURCE --id ID` | Full current stored normalized card and acquisition metadata; no claim of current platform availability |
| `history [--source SOURCE --id ID] [--limit N] [--offset N] [--full]` | Compact event history by default; explicit full evidence on demand |
| `record --source SOURCE --id ID --operation-id ID --kind KIND --evidence LOCATOR [--detail TEXT]` | Idempotent append with conflict detection; no replacement of prior facts or evaluations |
| `resume --profile PROFILE` | Exact full current configured resume snapshot and provenance |
| `stage-resume --profile PROFILE --text-file PATH --evidence LOCATOR` | Validated immutable resume revision and a next-run configuration; existing config, frozen runs and old CV files are unchanged |
| `status` | Latest analysis run/counts and current service resume metadata; no model calls |

Global flags precede the command. Limits are 1–200. `shortlist` uses the existing service's `recommendations`, including its selection-key, validated PASS and publication freshness rules. It excludes dismissed/applied/withdrawn/rejected identities. A deliberate `reopened` event can reopen an identity; `--unseen` additionally excludes confirmed shown events. Reads do not mutate state.

Both databases are opened in SQLite read-only mode for reads. Interaction writes are append-only rows in `career_assistant_events` inside the existing evaluator database. The collector database and evaluator outputs are never rewritten. Evidence and stable operation IDs are required; retries with the same data return the existing receipt, and conflicting reuse fails. A historical application may receive a new event through its preserved history even when it is absent from the present collection window.

The module does not access credentials, call models, send applications or schedule work. Actual platform submission/editing remains with the authorized browser/connector and must be verified before recording a factual outcome. A stored card is not a live availability check.

An optional agent wrapper can invoke this CLI using its own paths and transport.
No wrapper or host MCP is required or bundled. Global --output PATH requires an
explicit existing --output-root DIRECTORY. The destination must remain inside
that directory, is created exclusively with0600 and stays owned by the calling
OS user. No fixed agent directory or UID is assumed. Existing files are not overwritten.
This output constraint is not a replacement for OS isolation or native agent permissions.

Resume publication uses complete files and atomic exclusive publication. A new revision is verified with the same `ResumeSnapshot` schema and content hash. The active analysis configuration is not silently updated: the result identifies a next-run configuration for an explicit future run. Consecutive profile revisions should build on the prior returned configuration. Small resume edits never trigger a mass retrospective evaluation.

Legacy migration retained original Notion properties and platform observations with their evidence in the same ledger, clearly labelled historical and not independently reverified. Ambiguous identities/dates are retained as observations, not manufactured applications. Count unique applications, not raw event rows. Current intake/model behavior is unchanged.

Focused checks: `pytest -q tests/test_assistant_access.py tests/test_applications.py`. Live acceptance must separately prove an actual OpenBot request, service command execution, readable output and final agent response. Creating a profile or returning `queued` does not prove that path.

Permanent service exclusions also apply to shortlist/catalog/card: [exclusions.md](exclusions.md).
Excluded cards are not returned with full text, and catalog filters before pagination.
An explicit owner reopen can release only their own dismissal, never a code/model rejection.
Original model evaluations are immutable; availability projections preserve displaced history.
