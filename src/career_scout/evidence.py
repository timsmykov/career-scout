"""Lossless source anchors for model transport; persisted quotes remain literal."""

import copy
import re

from career_scout.evaluation import EvaluationError, vacancy_evidence_text

WIRE_VERSION = "source-ranges-1"


class EvidenceDocument:
    def __init__(self, text: str, prefix: str):
        # Keep separators with their text. Joining all segments reproduces the
        # original, including blank lines, Unicode and repeated whitespace.
        self.parts = []
        start = 0
        for boundary in re.finditer(r"\n|(?<=[.!?;])\s+", text):
            self._append(text[start : boundary.end()])
            start = boundary.end()
        self._append(text[start:])
        self.ids = [f"{prefix}{index:04d}" for index in range(1, len(self.parts) + 1)]
        self.positions = {key: index for index, key in enumerate(self.ids)}

    def _append(self, text):
        while text:
            boundary = min(len(text), 900)
            if boundary < len(text):
                space = text.rfind(" ", 0, boundary)
                if space > 0:
                    boundary = space + 1
            self.parts.append(text[:boundary])
            text = text[boundary:]

    def payload(self):
        return [{"id": key, "text": part} for key, part in zip(self.ids, self.parts, strict=True)]

    def reference_schema(self):
        return {
            "type": "object",
            "properties": {
                "from": {"type": "string", "enum": self.ids},
                "to": {"type": "string", "enum": self.ids},
            },
            "required": ["from", "to"],
            "additionalProperties": False,
        }

    def resolve(self, reference):
        if not isinstance(reference, dict) or set(reference) != {"from", "to"}:
            raise EvaluationError("invalid_evidence_reference")
        if not all(isinstance(value, str) for value in reference.values()):
            raise EvaluationError("invalid_evidence_reference")
        try:
            start = self.positions[reference["from"]]
            end = self.positions[reference["to"]]
        except (KeyError, TypeError):
            raise EvaluationError("unknown_evidence_reference") from None
        if end < start:
            raise EvaluationError("reversed_evidence_reference")
        quote = "".join(self.parts[start : end + 1]).strip()
        if not quote or len(quote) > 1000:
            raise EvaluationError("invalid_evidence_range_length")
        return quote


def documents(request, vacancy_prefix="V"):
    return (
        EvidenceDocument(vacancy_evidence_text(request.vacancy), vacancy_prefix),
        EvidenceDocument(request.resume.text, "R"),
    )


def hydrate_response(request, wire, vacancy_prefix="V"):
    """Resolve contiguous ranges only. Never invent, merge or fuzzy-match quotes."""
    vacancy, resume = documents(request, vacancy_prefix)
    value = copy.deepcopy(wire)
    if not isinstance(value, dict) or not isinstance(value.get("assessments"), list):
        raise EvaluationError("invalid_wire_response")
    for item in value["assessments"]:
        if not isinstance(item, dict) or {"vacancy_quote", "resume_quote"} & item.keys():
            raise EvaluationError("invalid_wire_response")
        if "vacancy_reference" not in item or "resume_reference" not in item:
            raise EvaluationError("missing_evidence_reference")
        item["vacancy_quote"] = vacancy.resolve(item.pop("vacancy_reference"))
        reference = item.pop("resume_reference")
        item["resume_quote"] = None if reference is None else resume.resolve(reference)
    if request.filters.get("evaluation_policy") == "context-1":
        if not isinstance(value.get("preference_checks"), list):
            raise EvaluationError("missing_preference_checks")
        for item in value["preference_checks"]:
            if not isinstance(item, dict) or "vacancy_quote" in item:
                raise EvaluationError("invalid_wire_response")
            if "vacancy_reference" not in item:
                raise EvaluationError("missing_evidence_reference")
            ref = item.pop("vacancy_reference")
            item["vacancy_quote"] = vacancy.resolve(ref) if ref is not None else None
    return value
