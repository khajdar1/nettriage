"""Kill switches in SSM (spec §9.7): re-read once a minute, off until known."""

from datetime import timedelta

import boto3
import pytest
from botocore.exceptions import ClientError
from conftest import FakeClock
from moto import mock_aws

from nettriage.adapters.kill_switch import KillSwitch, ssm_parameter

FAILURE = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Get")


class Parameter:
    """A fake parameter: each read returns (or raises) the next value."""

    def __init__(self, *values: str | Exception) -> None:
        self.values = list(values)
        self.reads = 0

    def __call__(self) -> str:
        value = self.values[min(self.reads, len(self.values) - 1)]
        self.reads += 1
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.parametrize(
    ("value", "on"),
    [("true", True), (" TRUE\n", True), ("false", False), ("", False), ("yes", False)],
)
def test_a_switch_is_on_only_while_its_parameter_reads_true(
    clock: FakeClock, value: str, on: bool
) -> None:
    assert KillSwitch(Parameter(value), clock).is_on() is on


def test_it_is_read_at_most_once_a_minute(clock: FakeClock) -> None:
    parameter = Parameter("true", "false")
    switch = KillSwitch(parameter, clock)

    first = switch.is_on()
    clock.advance(timedelta(seconds=59))
    cached = switch.is_on()
    clock.advance(timedelta(seconds=1))
    later = switch.is_on()

    assert (first, cached, later, parameter.reads) == (True, True, False, 2)


def test_a_failed_read_keeps_the_last_value(clock: FakeClock) -> None:
    switch = KillSwitch(Parameter("true", FAILURE), clock)

    first = switch.is_on()
    clock.advance(timedelta(minutes=1))

    assert (first, switch.is_on()) == (True, True)


def test_a_switch_that_was_never_read_is_off(clock: FakeClock) -> None:
    assert KillSwitch(Parameter(FAILURE), clock).is_on() is False


def test_a_parameter_that_does_not_exist_reads_as_off() -> None:
    with mock_aws():
        client = boto3.client("ssm", region_name="eu-north-1")
        client.put_parameter(Name="/nettriage/test/uploads-enabled", Value="true", Type="String")

        present = ssm_parameter(client, "/nettriage/test/uploads-enabled")()
        missing = ssm_parameter(client, "/nettriage/test/missing")()

    assert (present, missing) == ("true", "")
