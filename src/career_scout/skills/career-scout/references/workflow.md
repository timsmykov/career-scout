# Collect, evaluate, resume

All commands below need real returned IDs and paths. Keep a local operation journal
without secrets. Runtime schemas and `--help` own options/defaults.

## Collection

Create limits with a finite `max_requests` allowance (discover the exact structure
using `schema --name limits`). Initial collection uses a publication window, default
14 days. Plans carry source order and explicit search restrictions.

```sh
career-scout --workspace /private/search --env /private/sources.env run start --context CONTEXT_ID --plan PLAN_ID --limits limits.json --mode initial --request-id collect-start-001
career-scout --workspace /private/search run execute RUN_ID --request-id collect-execute-001
career-scout --workspace /private/search run status RUN_ID
career-scout --workspace /private/search run results RUN_ID --full --limit 20
```

If partial, inspect errors before `run resume RUN_ID --request-id collect-resume-001`.
Use `--additional-requests N` only within approved budget. To collect newly posted
vacancies later, start `--mode new`; source IDs prevent old detail downloads.

## Evaluation

```sh
career-scout --workspace /private/search evaluation prepare --context CONTEXT_ID --collection-run RUN_ID --limit 100 --max-calls 10 --max-decisions 0 --request-id eval-prepare-001
career-scout --workspace /private/search evaluation run EVALUATION_RUN_ID --db RETURNED_DB --enable-calls --request-id eval-run-001
career-scout --workspace /private/search evaluation status EVALUATION_RUN_ID --db RETURNED_DB
career-scout --workspace /private/search evaluation shortlist --db RETURNED_DB --limit 20
career-scout --workspace /private/search evaluation metrics EVALUATION_RUN_ID --db RETURNED_DB
```

`--max-decisions 0` suits one CV with auto-exclusion off; multiple CVs need a finite
routing allowance. `--enable-calls` explicitly activates this run, so use it only
when calls are authorized. One batch uses one full resume and at most 20 complete
vacancies. Up to four workers operate in parallel; mixed vacancies can reach two
resumes. Evidence and verdict checks are identical across providers.

If partial, use `evaluation resume` with the same run/DB and a new operation ID.
Only known technical failures are candidates for `--retry-errors`; unknown delivery
requires reconciliation and must not be retried blindly. A completed run isn't the
whole collection: repeat `evaluation prepare` to admit the next unprocessed portion
until no new items are admitted. Preserve all prior runs and cached results.

Read paginated shortlist results using returned cursors. `explain --analysis-db
RETURNED_DB --source hh --id SOURCE_ID` returns supporting evidence. Import existing
applications through `history import` only from owner-provided verified records.
The tool never sends an application. Quality metrics need separate human/control
labels; without them `quality.measured` is false.
