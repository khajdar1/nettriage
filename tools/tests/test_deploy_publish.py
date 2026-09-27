from pathlib import Path

from tools.deploy import publish
from tools.tests.deploy_fakes import FakeRun


def test_assets_are_cached_forever_index_never_and_demo_is_kept(tmp_path: Path) -> None:
    run = FakeRun().on("aws", "s3", "sync").on("aws", "cloudfront", "create-invalidation")

    publish.publish_web(run, {}, tmp_path, "nettriage-dev-web-1", "E123")

    assets, rest, invalidation = (call.args for call in run.calls)
    assert assets[:5] == ["aws", "s3", "sync", str(tmp_path / "assets"), "s3://nettriage-dev-web-1/assets"]
    assert "public,max-age=31536000,immutable" in assets
    assert rest[3:5] == [str(tmp_path), "s3://nettriage-dev-web-1"]
    assert "--delete" in rest and "no-cache" in rest
    assert rest.count("--exclude") == 2 and "assets/*" in rest and "demo/*" in rest
    assert invalidation[-3:] == ["--paths", "/index.html", "/"]
