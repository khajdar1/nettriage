"""The checks a model's answer must pass (spec §8.3, §8.4): the schema, candidate techniques only,
and only IP addresses and ports that are in the finding's data."""

from dataclasses import replace
from typing import Any

import pytest
from aidata import good_output, scan_subject

from nettriage.application.ai_output import check_output, mentioned, output_schema


def errors(raw: Any, **subject_changes: Any) -> tuple[str, ...]:
    return check_output(raw, scan_subject(**subject_changes)).errors


def test_a_grounded_answer_in_the_schema_passes() -> None:
    checked = check_output(good_output(), scan_subject())

    assert checked.errors == ()
    assert checked.output is not None
    assert checked.output.attack_techniques[0].id == "T1595"


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"summary": "x" * 601}, "summary"),
        ({"recommended_next_steps": []}, "recommended_next_steps"),
        ({"likely_benign_explanations": ["a", "b", "c", "d"]}, "likely_benign_explanations"),
        ({"attack_techniques": [{"id": "T1595", "rationale": "r"}] * 4}, "attack_techniques"),
        ({"attack_techniques": [{"id": "T1595<script>", "rationale": "r"}]}, "attack_techniques"),
        ({"confidence": "certain"}, "confidence"),
        ({"notes": "an extra field"}, "notes"),
    ],
)
def test_an_answer_outside_the_schema_is_refused_with_where(
    change: dict[str, Any], where: str
) -> None:
    found = errors(good_output(**change))

    assert found
    assert all(error.startswith(where) for error in found), found


def test_a_missing_field_is_named() -> None:
    raw = good_output()
    del raw["why_it_matters"]

    assert errors(raw) == ("why_it_matters: Field required",)


def test_only_candidate_techniques_may_be_named() -> None:
    raw = good_output(attack_techniques=[{"id": "T1046", "rationale": "Ports."}])

    assert errors(raw) == ("attack_techniques: T1046 isn't one of the candidate techniques",)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Block 198.51.100.7 at the firewall.", "mentions 198.51.100.7"),
        ("Check what listens on port 3389.", "mentions port 3389"),
        ("Look at 10.0.0.5:3389 first.", "mentions port 3389"),
        ("Look at 10.0.0.9:22 first.", "mentions 10.0.0.9"),
        ("Search the logs for src:198.51.100.7.", "mentions 198.51.100.7"),
        ("Search the logs for port:3389.", "mentions port 3389"),
    ],
)
def test_an_ip_or_port_that_isnt_in_the_data_is_refused(text: str, problem: str) -> None:
    found = errors(good_output(recommended_next_steps=[text]))

    assert len(found) == 1
    assert found[0].startswith("recommended_next_steps")
    assert problem in found[0]


@pytest.mark.parametrize(
    ("text", "ips", "ports"),
    [
        ("src:198.51.100.7", {"198.51.100.7"}, set()),
        ("Source:198.51.100.7", {"198.51.100.7"}, set()),
        ("Device:198.51.100.7", {"198.51.100.7"}, set()),
        ("ID:198.51.100.7", {"198.51.100.7"}, set()),
        ("cafe:198.51.100.7", {"198.51.100.7"}, set()),
        ("Interface:198.51.100.7:3389", {"198.51.100.7"}, {3389}),
        ("::ffff:198.51.100.7", {"::ffff:198.51.100.7"}, set()),
        ("2001:db8:1::", {"2001:db8:1::"}, set()),
        ("::1", {"::1"}, set()),
        ("port:3389", set(), {3389}),
        ("Port: 3389", set(), {3389}),
        ("[2001:db8::1]:22", {"2001:db8::1"}, {22}),
    ],
)
def test_addresses_and_ports_are_found_after_a_label_and_in_short_forms(
    text: str, ips: set[str], ports: set[int]
) -> None:
    assert mentioned(f"Search for {text}.") == (ips, ports)


@pytest.mark.parametrize(
    "text",
    [
        "Look at 10.0.0.5:22 and port 443 on 10.0.0.5.",
        "It probed 100 ports in five minutes (version 1.2.3, at 12:05).",
        "Compare with source port 40000.",
    ],
)
def test_grounded_mentions_and_ordinary_numbers_pass(text: str) -> None:
    assert errors(good_output(summary=text)) == ()


def test_ipv6_addresses_are_compared_in_canonical_form() -> None:
    subject = scan_subject(src_ip="2001:db8::9")
    subject = replace(subject, evidence=())

    assert mentioned("From 2001:0db8:0:0::9, twice.") == ({"2001:db8::9"}, set())
    assert (
        check_output(
            good_output(
                summary="2001:db8::9 probed 10.0.0.5.",
                recommended_next_steps=["Check 2001:db8::9."],
            ),
            subject,
        ).errors
        == ()
    )


def test_the_schema_given_to_the_model_carries_the_limits() -> None:
    schema = output_schema()

    assert schema["additionalProperties"] is False
    assert schema["properties"]["summary"]["maxLength"] == 600
    assert schema["properties"]["attack_techniques"]["maxItems"] == 3
    assert set(schema["required"]) == {
        "summary",
        "why_it_matters",
        "likely_benign_explanations",
        "recommended_next_steps",
        "attack_techniques",
        "severity_assessment",
        "confidence",
        "insufficient_evidence",
    }
