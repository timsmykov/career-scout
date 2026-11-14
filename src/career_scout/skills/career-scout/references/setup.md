# Initial setup

Install the public package from a checkout with `uv sync --locked`, or install its
built wheel with Python 3.12. Install this skill using:

```sh
career-scout skill install --agent codex
career-scout skill install --agent claude
```

Use one command for the agent in use. `--directory PATH` overrides its skills parent
directory. Existing different contents cause a conflict, preserving that skill.
Installation does not install the CLI or log in to providers. Restart/reload the
agent's skill discovery after installation. Other agents can load the SKILL.md
folder using their own Agent Skills mechanism.

Discover input contracts:

```sh
career-scout schema --name context
career-scout schema --name plan
career-scout schema --name connection
```

Write JSON files in a private directory using the current schemas. A minimal
context contains `resumes: [{id, title, text}]`; add goals, preferences, attributes
and explicit fact_rules as appropriate. There are no implied IT/salary/city filters.
Use plans with the owner's queries and permitted sources. Do not enable SuperJob
storage without their permission. Credentials follow the repository .env.example.

```sh
career-scout --workspace /private/search context create --input context.json --request-id context-001
career-scout --workspace /private/search plan put --context CONTEXT_ID --input plan.json --request-id plan-001
career-scout --workspace /private/search plan preview PLAN_ID
career-scout --workspace /private/search connection put default --input connection.json --request-id connection-001
career-scout --workspace /private/search doctor --context CONTEXT_ID
```

Use `result.context_id` from context create and `result.id` from plan put for
CONTEXT_ID and PLAN_ID. Context updates require `--if-version`; a changed
context needs a new plan and a new evaluation preparation. Old evaluations stay intact.

## Evaluator choice

`connection.model.provider` selects one of the entries in `provider list`.

- `codex_subscription`: existing host Codex login through ChatGPT; model must be
  available to that account.
- `claude_subscription`: existing `claude auth login` through claude.ai, current
  Claude Code with safe-mode support. Select its own model, e.g. `sonnet`.
  Native subscription limits and current Anthropic billing rules apply.
- APIs: choose a provider, explicit model ID and optional `api` settings. By default
  its documented environment variable holds the key; `api.key_env` overrides the
  variable name only. `api.max_output_tokens` bounds each response.

Example API connection (supply a real available model ID):

```json
{"model":{"provider":"openrouter","model":"PROVIDER/MODEL","api":{"max_output_tokens":16000}},"external_calls_enabled":false,"batch_size":10,"workers":4}
```

Set the environment outside the agent's visible output or pass `--env /private/models.env`
to `doctor` and `evaluation run/resume`. The CLI never sources the file as shell.
For a first check use one resume, a small sample and a finite call budget. JSON mode
is portable; schema mode requires a model that supports the supplied schema. There
is no silent provider fallback or implicit billed retry in the API transport.

A multi-CV connection also needs `decisions: {binary, workspace, workspace_root}`.
It is an external connector, separate from the evaluator provider. Do not claim
that choosing OpenRouter supplies a Decisions implementation.
