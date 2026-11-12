"""Check bootstrap contracts and documentation offline; this is not a runtime test."""

from __future__ import annotations

import copy
import json
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {"product", "business_analyst", "business_assistant", "automation"}


def read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def check_schemas() -> int:
    schemas = {}
    for path in sorted((ROOT / "schemas").glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        schemas[path.name] = schema
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    validators = {
        name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
        for name, schema in schemas.items()
    }
    pairs = {
        "vacancy.synthetic.json": "vacancy-snapshot.schema.json",
        "vacancy-v2.synthetic.json": "vacancy-v2.schema.json",
        "worker-input.synthetic.json": "worker-input.schema.json",
        "worker-input-borderline.synthetic.json": "worker-input.schema.json",
        "worker-input-reject.synthetic.json": "worker-input.schema.json",
        "evaluation-pass.synthetic.json": "evaluation-result.schema.json",
        "evaluation-borderline.synthetic.json": "evaluation-result.schema.json",
        "evaluation-reject.synthetic.json": "evaluation-result.schema.json",
        "collection-report.synthetic.json": "collection-report.schema.json",
    }
    for example, schema in pairs.items():
        validators[schema].validate(read_json("examples/" + example))

    invalid_input = copy.deepcopy(read_json("examples/worker-input.synthetic.json"))
    invalid_input["vacancy"]["detail_status"] = "partial"
    if validators["worker-input.schema.json"].is_valid(invalid_input):
        raise ValueError("Worker input schema admits an incomplete vacancy")
    invalid_pass = copy.deepcopy(read_json("examples/evaluation-pass.synthetic.json"))
    invalid_pass["questions"] = ["Unresolved mandatory condition"]
    if validators["evaluation-result.schema.json"].is_valid(invalid_pass):
        raise ValueError("Result schema admits PASS with an unresolved question")
    invalid_evidence = copy.deepcopy(read_json("examples/evaluation-pass.synthetic.json"))
    invalid_evidence["assessments"][0]["resume_quote"] = None
    if validators["evaluation-result.schema.json"].is_valid(invalid_evidence):
        raise ValueError("Result schema admits confirmed without evidence")

    for result_name, input_name in [
        ("evaluation-pass.synthetic.json", "worker-input.synthetic.json"),
        ("evaluation-borderline.synthetic.json", "worker-input-borderline.synthetic.json"),
        ("evaluation-reject.synthetic.json", "worker-input-reject.synthetic.json"),
    ]:
        result = read_json("examples/" + result_name)
        worker_input = read_json("examples/" + input_name)
        if result["evaluation_key"] != worker_input["evaluation_key"]:
            raise ValueError("Example result and input have different identities")
        for assessment in result["assessments"]:
            if assessment["vacancy_quote"] not in worker_input["vacancy"]["description"]:
                raise ValueError("Synthetic vacancy quote is absent from its input")
            quote = assessment["resume_quote"]
            if quote and quote not in worker_input["resume"]["text"]:
                raise ValueError("Synthetic resume quote is absent from its input")
    return len(pairs)


def check_config() -> None:
    with (ROOT / "configs/project.example.toml").open("rb") as stream:
        config = tomllib.load(stream)
    if config["external_calls_enabled"] or config["evaluation"]["enabled"]:
        raise ValueError("Bootstrap must not enable external calls")
    if any(source["enabled"] for source in config["sources"].values()):
        raise ValueError("Bootstrap must not enable a source")
    order = config["evaluation"]["order"]
    if len(order) != 4 or set(order) != PROFILES:
        raise ValueError("Routing must contain each of the four profiles exactly once")
    if config["evaluation"]["order_confirmed"]:
        raise ValueError("The example must preserve the unconfirmed order")
    profiles = read_json("configs/profiles.example.json")["profiles"]
    if len(profiles) != 4 or {profile["id"] for profile in profiles} != PROFILES:
        raise ValueError("Incorrect profile set")
    for profile in profiles:
        if not (ROOT / profile["rubric_path"]).is_file():
            raise ValueError("Missing profile rubric")
        if profile["resume_hash"] is not None:
            raise ValueError("Example cannot claim a current imported resume")


def check_markdown() -> int:
    checked = 0
    for path in ROOT.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            continue
        content = path.read_text(encoding="utf-8")
        for raw in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", content):
            url = urlsplit(raw.strip("<>"))
            if url.scheme or url.netloc or not url.path:
                continue
            target = (path.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(ROOT) or not target.exists():
                raise ValueError(f"Broken local link: {path.relative_to(ROOT)} -> {raw}")
            checked += 1
    return checked


def main() -> None:
    examples = check_schemas()
    check_config()
    links = check_markdown()
    print(f"Bootstrap OK: {examples} schema examples; {links} local links; offline defaults.")
    print(
        "Runtime behavior is checked by pytest; "
        "Codex subscription calls are separate live acceptance."
    )


if __name__ == "__main__":
    main()
