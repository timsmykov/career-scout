"""Native Decisions experiment on frozen, locally authored labels (no API key export)."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from career_scout.common import digest, utcnow
from career_scout.decisions import (
    INSTRUCTIONS,
    MODEL,
    PROFILES,
    DecisionsClient,
    calibrate_margin,
    select_profiles,
)
from career_scout.models import Vacancy


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(directory, workspace):
    manifest = json.loads((directory / "frozen-manifest.json").read_text())
    policy = digest({"model": MODEL, "instructions": INSTRUCTIONS, "profiles": PROFILES})
    if (
        manifest["labels_sha256"] != sha(directory / "labels.json")
        or manifest["sample_sha256"] != sha(directory / "sample.json")
        or manifest["policy_hash"] != policy
    ):
        raise ValueError("frozen_experiment_changed")
    labels = json.loads((directory / "labels.json").read_text())["records"]
    sample = json.loads((directory / "sample.json").read_text())
    by_id = {v["label_sample_id"]: v for v in sample}
    if len(labels) != 100 or len(by_id) != 100 or len({r["sample_id"] for r in labels}) != 100:
        raise ValueError("experiment_requires_100_unique_cases")
    if (
        sum(r["split"] == "development" for r in labels) != 60
        or sum(r["split"] == "holdout" for r in labels) != 40
    ):
        raise ValueError("expected_split_60_40")
    # Validate the entire frozen corpus before the first paid request.
    vacancies = []
    for label in labels:
        value = by_id[label["sample_id"]]
        if label["description_sha256"] != hashlib.sha256(value["description"].encode()).hexdigest():
            raise ValueError("label_description_changed")
        vacancy = Vacancy.model_validate(
            {k: v for k, v in value.items() if k in Vacancy.model_fields}
        )
        vacancies.append(vacancy)
    client = DecisionsClient(workspace)
    rows = []
    for index, (label, vacancy) in enumerate(zip(labels, vacancies, strict=True), 1):
        result = client.evaluate(vacancy)
        rows.append({**label, **result})
        # Each native receipt is already persisted; this is a recoverable progress view.
        (directory / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        print(
            json.dumps({"completed": index, "total": len(labels), "cached": result["cached"]}),
            flush=True,
        )
    dev = [r for r in rows if r["split"] == "development"]
    heldout = [r for r in rows if r["split"] == "holdout"]
    if len(dev) != 60 or len(heldout) != 40:
        raise ValueError("expected_split_60_40")
    calibration = {
        "created_at": utcnow(),
        "policy_hash": policy,
        "labels_sha256": manifest["labels_sha256"],
        "sample_sha256": manifest["sample_sha256"],
        "annotation_status": (
            "single author provisional labels; not independent or owner-confirmed gold"
        ),
        "selection": "purposeful boundary sample, short texts preferred; not representative",
        **calibrate_margin(dev, heldout),
    }
    calibration["distribution"] = {
        str(n): sum(
            len(select_profiles(r["probabilities"], calibration["margin"])) == n for r in rows
        )
        for n in (0, 1, 2)
    }
    calibration["input_tokens"] = sum((r["usage"] or {}).get("input_tokens", 0) for r in rows)
    calibration["estimated_usd_uncached"] = calibration["input_tokens"] * 0.10 / 1_000_000
    (directory / "calibration.json").write_text(
        json.dumps(calibration, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps({k: v for k, v in calibration.items() if k != "grid"}, ensure_ascii=False),
        flush=True,
    )
    return calibration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    execute(args.directory.resolve(), args.workspace.resolve())


if __name__ == "__main__":
    main()
