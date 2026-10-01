"""The model's answer (spec §8.3, §8.4): output schema v1, and the checks an answer must pass before
anyone sees it. It must match the schema, name only candidate techniques, and mention only IP
addresses and ports that are in the finding's data."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from nettriage.application.ai_input import TriageSubject

OUTPUT_SCHEMA_VERSION = "v1"

Text600 = Annotated[str, StringConstraints(min_length=1, max_length=600)]
Item200 = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Severity = Literal["low", "medium", "high", "critical"]

# Runs of characters an IP address (or IPv4:port) is made of. One character class, so matching
# is linear.
_ADDRESS_LIKE = re.compile(r"[0-9A-Fa-f:.]+")
# Ports are checked only when written as "port N" (or "<ip>:N"), so counts such as "100 ports"
# aren't read as ports.
_PORT = re.compile(r"\bport\s+([0-9]{1,5})\b", re.IGNORECASE)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TechniqueClaim(_Strict):
    id: Annotated[str, Field(pattern=r"^T[0-9]{4}(\.[0-9]{3})?$")]
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=400)]


class SeverityAssessment(_Strict):
    agrees_with_detector: bool
    suggested_severity: Severity
    reason: Annotated[str, StringConstraints(min_length=1, max_length=300)]


class TriageOutput(_Strict):
    summary: Text600
    why_it_matters: Text600
    likely_benign_explanations: Annotated[list[Item200], Field(max_length=3)]
    recommended_next_steps: Annotated[list[Item200], Field(min_length=1, max_length=5)]
    attack_techniques: Annotated[list[TechniqueClaim], Field(max_length=3)]
    severity_assessment: SeverityAssessment
    confidence: Literal["low", "medium", "high"]
    insufficient_evidence: bool


@dataclass(frozen=True)
class Checked:
    """The answer, if it passed every check; otherwise the reasons, for one repair attempt."""

    output: TriageOutput | None
    errors: tuple[str, ...]


def output_schema() -> dict[str, Any]:
    """The JSON schema the provider constrains the model's answer to."""
    return TriageOutput.model_json_schema()


def check_output(raw: object, subject: TriageSubject) -> Checked:
    try:
        output = TriageOutput.model_validate(raw)
    except ValidationError as error:
        return Checked(None, tuple(_schema_error(item) for item in error.errors()))
    errors = (*_technique_errors(output, subject), *_grounding_errors(output, subject))
    return Checked(None if errors else output, errors)


def _schema_error(item: Any) -> str:
    where = ".".join(str(part) for part in item["loc"]) or "answer"
    return f"{where}: {item['msg']}"


def _technique_errors(output: TriageOutput, subject: TriageSubject) -> list[str]:
    allowed = {candidate.id for candidate in subject.candidates}
    return [
        f"attack_techniques: {claim.id} isn't one of the candidate techniques"
        for claim in output.attack_techniques
        if claim.id not in allowed
    ]


def _grounding_errors(output: TriageOutput, subject: TriageSubject) -> list[str]:
    known_ips = {
        _normalized(address)
        for address in (
            subject.src_ip,
            subject.dst_ip,
            *(row.src_ip for row in subject.evidence),
            *(row.dst_ip for row in subject.evidence),
        )
        if address is not None
    }
    known_ports = {
        port
        for port in (
            subject.dst_port,
            *(row.src_port for row in subject.evidence),
            *(row.dst_port for row in subject.evidence),
        )
        if port is not None
    }
    errors = []
    for field, text in _texts(output):
        ips, ports = mentioned(text)
        errors += [f"{field} mentions {ip}, which isn't in the data" for ip in ips - known_ips]
        errors += [
            f"{field} mentions port {p}, which isn't in the data" for p in ports - known_ports
        ]
    return errors


def mentioned(text: str) -> tuple[set[str], set[int]]:
    """The IP addresses and ports a text mentions."""
    ips: set[str] = set()
    ports = {int(port) for port in _PORT.findall(text)}
    for token in _ADDRESS_LIKE.findall(text):
        token = token.strip(".:")
        if token.count(".") == 3 and token.count(":") == 1:
            address, _, port = token.partition(":")
            if _is_ip(address) and port.isdigit():
                ips.add(_normalized(address))
                ports.add(int(port))
        elif (token.count(".") == 3 or token.count(":") >= 2) and _is_ip(token):
            ips.add(_normalized(token))
    return ips, ports


def _texts(output: TriageOutput) -> list[tuple[str, str]]:
    return [
        ("summary", output.summary),
        ("why_it_matters", output.why_it_matters),
        *(("likely_benign_explanations", item) for item in output.likely_benign_explanations),
        *(("recommended_next_steps", item) for item in output.recommended_next_steps),
        *(("attack_techniques", claim.rationale) for claim in output.attack_techniques),
        ("severity_assessment", output.severity_assessment.reason),
    ]


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _normalized(value: str) -> str:
    return str(ipaddress.ip_address(value))
