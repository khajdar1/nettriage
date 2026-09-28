from pathlib import Path

from tools.check_pinned_actions import main, unpinned_actions

SHA = "08c6903cd8c0fde910a37f88322edcfb5dd907a8"


def test_actions_pinned_to_a_full_sha_pass() -> None:
    text = f"steps:\n  - uses: actions/checkout@{SHA} # v5\n"
    assert unpinned_actions(text) == []


def test_sub_path_actions_pinned_to_a_sha_pass() -> None:
    text = f"      - uses: github/codeql-action/init@{SHA} # v3\n"
    assert unpinned_actions(text) == []


def test_tags_branches_and_quoted_refs_fail() -> None:
    text = (
        "  - uses: actions/checkout@v5\n"
        "  - uses: foo/bar@main\n"
        '  - uses: "actions/setup-node@v5"\n'
    )
    assert unpinned_actions(text) == ["actions/checkout@v5", "foo/bar@main", "actions/setup-node@v5"]


def test_local_actions_are_allowed() -> None:
    assert unpinned_actions("  - uses: ./.github/actions/setup\n") == []


def test_main_reports_unpinned_actions(tmp_path: Path) -> None:
    (tmp_path / "ci.yml").write_text("steps:\n  - uses: actions/checkout@v5\n", encoding="utf-8")
    (tmp_path / "ok.yml").write_text(f"steps:\n  - uses: actions/checkout@{SHA}\n", encoding="utf-8")

    assert main([str(tmp_path)]) == 1


def test_main_passes_when_everything_is_pinned(tmp_path: Path) -> None:
    (tmp_path / "ok.yml").write_text(f"steps:\n  - uses: actions/checkout@{SHA}\n", encoding="utf-8")

    assert main([str(tmp_path)]) == 0
