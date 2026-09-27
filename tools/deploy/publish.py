"""Publish the web build: hashed assets cached for a year, everything else revalidated."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tools.deploy.runner import Runner


def publish_web(run: Runner, env: Mapping[str, str], web_dir: Path, bucket: str, distribution_id: str) -> None:
    run(
        ["aws", "s3", "sync", str(web_dir / "assets"), f"s3://{bucket}/assets",
         "--cache-control", "public,max-age=31536000,immutable"],
        env=env,
    )
    # demo/ holds the public demo snapshot (spec §3.2); --delete must never remove it.
    run(
        ["aws", "s3", "sync", str(web_dir), f"s3://{bucket}",
         "--exclude", "assets/*", "--exclude", "demo/*", "--cache-control", "no-cache", "--delete"],
        env=env,
    )
    run(
        ["aws", "cloudfront", "create-invalidation", "--distribution-id", distribution_id,
         "--paths", "/index.html", "/"],
        env=env,
    )
