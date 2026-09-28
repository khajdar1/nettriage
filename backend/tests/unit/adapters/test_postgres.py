import certifi

from nettriage.adapters.postgres import connect_args, engine_url


def test_urls_use_the_psycopg_3_driver() -> None:
    url = engine_url("postgresql://app_api:secret@ep-x.eu-central-1.aws.neon.tech/nettriage")

    assert url.drivername == "postgresql+psycopg"
    assert (url.username, url.host) == ("app_api", "ep-x.eu-central-1.aws.neon.tech")


def test_remote_hosts_always_verify_tls_and_skip_prepared_statements() -> None:
    url = engine_url(
        "postgresql://app_api:secret@ep-x.eu-central-1.aws.neon.tech/nettriage?sslmode=disable"
    )

    assert connect_args(url) == {
        "prepare_threshold": None,
        "sslmode": "verify-full",
        "sslrootcert": certifi.where(),
    }


def test_a_local_test_server_needs_no_tls() -> None:
    url = engine_url("postgresql://postgres:@127.0.0.1:55432/postgres")

    assert connect_args(url) == {"prepare_threshold": None}
