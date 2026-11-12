"""One resume per Codex invocation, up to twenty independently validated vacancies."""

import copy
import json
from collections import Counter
from dataclasses import dataclass

from pydantic import ValidationError

from career_scout.application import result_model
from career_scout.codex_worker import CodexWorker, WorkerReply, build_prompt, output_schema
from career_scout.evaluation import EvaluationError, validate_result
from career_scout.evidence import documents, hydrate_response


def batch_payload(requests):
    if not 1 <= len(requests) <= 20:
        raise EvaluationError("invalid_batch_size", fatal=True)
    first = requests[0]
    if len({r.evaluation_key for r in requests}) != len(requests):
        raise EvaluationError("duplicate_batch_input", fatal=True)
    for r in requests:
        if (r.profile_id, r.resume, r.rubric, r.common_instructions, r.model_config_hash) != (
            first.profile_id,
            first.resume,
            first.rubric,
            first.common_instructions,
            first.model_config_hash,
        ):
            raise EvaluationError("mixed_profile_batch", fatal=True)
    header, raw = build_prompt(first).rsplit("\n\n", 1)
    values = json.loads(raw)
    shared = {k: values[k] for k in ("schema_version", "wire_version", "profile_id", "resume")}
    cases, variants, definitions = [], [], {}
    for index, request in enumerate(requests):
        prefix = f"B{index}V"
        _, raw = build_prompt(request).rsplit("\n\n", 1)
        item = json.loads(raw)
        item["vacancy"]["evidence"] = documents(request, prefix)[0].payload()
        cases.append({k: item[k] for k in ("evaluation_key", "vacancy", "filters")})
        schema = copy.deepcopy(output_schema(request, prefix))
        defs = schema.pop("$defs")
        renamed = {key: f"B{index}_{key}" for key in defs}

        def replace_refs(node, renamed=renamed):
            if isinstance(node, dict):
                if "$ref" in node:
                    key = node["$ref"].split("/")[-1]
                    node["$ref"] = "#/$defs/" + renamed[key]
                for value in node.values():
                    replace_refs(value)
            elif isinstance(node, list):
                for value in node:
                    replace_refs(value)

        replace_refs(schema)
        for name, value in defs.items():
            replace_refs(value)
            definitions[renamed[name]] = value
        schema["properties"]["evaluation_key"] = {"type": "string", "const": request.evaluation_key}
        schema["properties"]["profile_id"] = {"type": "string", "const": request.profile_id}
        variants.append(schema)
    shared["cases"] = cases
    prompt = (
        header
        + (
            "\n\nБатч независимых вакансий. Одно общее резюме применяется отдельно к каждой. "
            "Верни объект results со всеми evaluation_key ровно по одному разу. "
            "Не переноси требования, факты или выводы между вакансиями. "
            "Вакансионные ссылки B0V…, B1V… принадлежат только своему случаю; "
            "ссылки R… относятся к общему резюме. Сохраняй отдельные основания и вопросы.\n\n"
        )
        + json.dumps(shared, ensure_ascii=False, sort_keys=True)
    )
    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {"anyOf": variants},
                "minItems": len(requests),
                "maxItems": len(requests),
            }
        },
        "required": ["results"],
        "additionalProperties": False,
        "$defs": definitions,
    }
    return prompt, schema


def batch_size_characters(requests):
    prompt, schema = batch_payload(requests)
    return len(prompt) + len(json.dumps(schema, ensure_ascii=False))


@dataclass
class BatchReply:
    replies: dict
    errors: dict
    usage: dict | None
    wire: dict


class CodexBatchWorker(CodexWorker):
    batch_max_input_characters = None

    def batch(self, requests):
        if any(r.model_config_hash != self.config.key() for r in requests):
            raise EvaluationError("model_config_mismatch", fatal=True)
        prompt, schema = batch_payload(requests)
        if self.batch_max_input_characters is None:
            wire, usage = self.invoke(prompt, schema)
        else:
            wire, usage = self.invoke(
                prompt, schema, max_input_characters=self.batch_max_input_characters
            )
        items = wire.get("results", []) if isinstance(wire, dict) else []
        if not isinstance(items, list):
            items = []
        items = [
            x for x in items if isinstance(x, dict) and isinstance(x.get("evaluation_key"), str)
        ]
        counts = Counter(x["evaluation_key"] for x in items)
        by_key = {x["evaluation_key"]: x for x in items}
        replies, errors = {}, {}
        for index, request in enumerate(requests):
            key = request.evaluation_key
            if counts[key] != 1:
                errors[key] = "missing_or_duplicate_batch_result"
                continue
            try:
                result = hydrate_response(request, by_key[key], f"B{index}V")
                checked = validate_result(request, result_model(request).model_validate(result))
            except EvaluationError as exc:
                errors[key] = exc.code
            except ValidationError:
                errors[key] = "invalid_evaluation_result"
            else:
                replies[key] = WorkerReply(checked.model_dump(mode="json"), wire_result=by_key[key])
        return BatchReply(replies, errors, usage, wire)
