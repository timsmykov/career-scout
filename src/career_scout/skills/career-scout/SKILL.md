---
name: career-scout
description: Configure and operate Career Scout through its JSON CLI to collect vacancies, compare them with full resumes and produce a shortlist. Use for a personal job search with Career Scout, including setup and resuming interrupted runs.
---

# Career Scout

Use the installed `career-scout` CLI. If running from a checkout, use
`uv run --no-sync career-scout`. The tool owns storage, pagination, deduplication,
queue admission, finite budgets and validation; the agent supplies the candidate's
context and search plan. There is no fixed set of professions.

## Establish the installation and intent

Run `career-scout version`, `career-scout schema` and `career-scout provider list`.
Use runtime schemas for exact fields, not guessed commands. Select an explicit
private `--workspace` for this person's installation. Reuse an existing workspace
and its context/plan/connection when continuing their search.

Use the supplied full resumes, goals and important conditions to build context.
Preserve authorship and facts. Do not invent achievements or silently shorten a
resume. Distinguish mandatory preferences from desirable ones. Unknown pay,
location or experience is uncertainty, not evidence of a conflict. Ask only for
information necessary to start, unless the owner asked for unattended execution;
then use supplied facts, keep unknowns and state assumptions.

Read [setup.md](references/setup.md) for first setup, model connections and plans.
Read [workflow.md](references/workflow.md) for collection, evaluation and recovery.

## Control execution

- Every stateful workspace mutation requires a unique `--request-id`. Record the
  exact arguments, returned context/plan/run IDs, versions and evaluation DB path.
  A retry of the same operation uses the same request ID and same inputs. A new
  operation uses a new ID. `operation show REQUEST_ID` reconciles a lost receipt.
- JSON `status=ok` and exit 0 mean the command ran, not that the search is complete.
  Inspect `result.status`, counters, errors and remaining budgets. Follow cursors
  until absent when reading paginated lists.
- Preview before collection. Keep source and model budgets finite within the
  owner's authorization. Never increase them silently. Model calls are disabled
  in a new connection until explicitly enabled.
- Do not read/copy native login files. The CLI uses the owner's existing Codex or
  Claude Code login, or an API key reference in the environment/`--env` file.
  Never put key values into JSON inputs, prompts, receipts or shared artifacts.
- One resume needs no Decisions connector when auto-exclusion is off. Multiple
  resumes require the separate documented Decisions connector. Do not substitute
  keyword rejection or pretend routing was performed.
- Resume the same run after interruption; prepare admits the next unprocessed
  portion. Never delete the database or start a replacement run to bypass an
  unknown-delivery block. Reconcile the provider receipt first.

## Deliver the useful result

Return links to PASS vacancies, grouped by the selected resume, with short factual
reasons. Show UNCERTAIN separately only if requested. Use `explain` for contested
results. Report scope, incomplete sources and unfinished work. Do not call mocked
checks live validation or describe semantic quality as measured without labels.

Career Scout does not send applications, edit online resumes or schedule itself.
Installing this skill does not authorize those actions or any additional spending.
