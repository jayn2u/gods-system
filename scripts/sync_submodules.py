#!/usr/bin/env python3
"""Validate and adopt the latest develop commit from each configured submodule."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


EVENT_TYPE = "submodule-develop-updated"
DEVELOP_REF = "refs/heads/develop"
SUPERPROJECT_URL = "https://github.com/jayn2u/gods-system.git"
MAX_PUSH_ATTEMPTS = 3
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"

# Payload repository names and checkout URLs are never used as dynamic input.
SOURCES = {
    "jayn2u/gods-eye": ("gods-eye", "https://github.com/jayn2u/gods-eye.git"),
    "jayn2u/gods-mlops": ("gods-mlops", "https://github.com/jayn2u/gods-mlops.git"),
    "jayn2u/gods-watching": (
        "gods-watching",
        "https://github.com/jayn2u/gods-watching.git",
    ),
}
SHA_RE = re.compile(r"\A[0-9a-f]{40,64}\Z")
RUN_URL_RE = re.compile(
    r"\Ahttps://github\.com/jayn2u/(?:gods-eye|gods-mlops|gods-watching)"
    r"/actions/runs/[0-9]+(?:/attempts/[0-9]+)?\Z"
)


class SyncError(Exception):
    """A safe-to-print failure that omits Git output and credentials."""


class PushConflict(SyncError):
    pass


def _git_env(token: str, repositories: tuple[str, ...]) -> dict[str, str]:
    """Create Git auth for an explicit repository list without URL credentials."""
    environment = os.environ.copy()
    environment.pop("SUBMODULE_SOURCE_TOKEN", None)
    environment.pop("SUPERPROJECT_PUSH_TOKEN", None)

    # Ignore inherited per-invocation Git config so an outer checkout credential
    # cannot be forwarded to source or nested-submodule operations.
    for key in tuple(environment):
        if key == "GIT_CONFIG_COUNT" or re.fullmatch(r"GIT_CONFIG_(?:KEY|VALUE)_\d+", key):
            environment.pop(key, None)

    config: list[tuple[str, str]] = [
        ("url.https://github.com/.insteadOf", "git@github.com:"),
    ]
    authorization = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    header = f"AUTHORIZATION: basic {authorization}"
    for repository in repositories:
        config.append((f"http.https://github.com/{repository}.git.extraheader", header))
    config.append(("protocol.file.allow", "never"))

    environment["GIT_CONFIG_COUNT"] = str(len(config))
    for index, (key, value) in enumerate(config):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _run_git(
    args: list[str], *, cwd: Path, environment: dict[str, str], stage: str
) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise SyncError(f"{stage} failed (git exit {result.returncode})")
    return result.stdout.strip()


def _run_git_result(
    args: list[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _validate_event(event: Any) -> None:
    if not isinstance(event, dict) or event.get("action") != EVENT_TYPE:
        raise SyncError("event type is invalid")
    payload = event.get("client_payload")
    if not isinstance(payload, dict) or set(payload) != {
        "repository",
        "ref",
        "sha",
        "run_url",
    }:
        raise SyncError("event payload fields are invalid")

    repository = payload.get("repository")
    if not isinstance(repository, str) or repository not in SOURCES:
        raise SyncError("event repository is not allowed")
    if payload.get("ref") != DEVELOP_REF:
        raise SyncError("event ref is not allowed")
    if not isinstance(payload.get("sha"), str) or not SHA_RE.fullmatch(payload["sha"]):
        raise SyncError("event SHA is invalid")
    run_url = payload.get("run_url")
    if not isinstance(run_url, str) or not RUN_URL_RE.fullmatch(run_url):
        raise SyncError("event run URL is invalid")


def _read_event(path: Path) -> dict[str, Any]:
    try:
        event = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise SyncError("event file is unreadable or invalid JSON") from None
    _validate_event(event)
    return event


def _configured_submodules(gitmodules: Path, *, cwd: Path, environment: dict[str, str]) -> dict[str, str]:
    if not gitmodules.is_file():
        raise SyncError("superproject .gitmodules is missing")
    output = _run_git(
        ["config", "--file", str(gitmodules), "--null", "--list"],
        cwd=cwd,
        environment=environment,
        stage="reading submodule configuration",
    )
    modules: dict[str, dict[str, str]] = {}
    for record in output.split("\0"):
        if not record:
            continue
        key, separator, value = record.partition("\n")
        if not separator or not key.startswith("submodule."):
            continue
        setting = key.rsplit(".", 1)[-1].lower()
        if setting not in {"path", "url"}:
            continue
        name = key[len("submodule.") : -len(setting) - 1]
        values = modules.setdefault(name, {})
        if setting in values:
            raise SyncError("duplicate submodule configuration")
        values[setting] = value

    configured: dict[str, str] = {}
    for name, settings in modules.items():
        path = settings.get("path")
        url = settings.get("url")
        if not path or not url or path in configured:
            raise SyncError("submodule path or URL is missing or duplicated")
        configured[path] = url
    return configured


def _validate_superproject_submodules(
    worktree: Path, *, environment: dict[str, str]
) -> None:
    configured = _configured_submodules(worktree / ".gitmodules", cwd=worktree, environment=environment)
    expected: dict[str, set[str]] = {}
    for repository, (path, url) in SOURCES.items():
        expected[path] = {
            url,
            f"git@github.com:{repository}.git",
        }
    if set(configured) != set(expected):
        raise SyncError("superproject submodule paths do not match the fixed source map")
    if any(configured[path] not in urls for path, urls in expected.items()):
        raise SyncError("superproject submodule URLs do not match the fixed source map")


def _latest_source_sha(
    url: str, *, environment: dict[str, str], repository: Path
) -> str:
    output = _run_git(
        ["ls-remote", "--exit-code", url, DEVELOP_REF],
        cwd=repository,
        environment=environment,
        stage="reading source develop",
    )
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != DEVELOP_REF:
        raise SyncError("source develop did not return one commit")
    sha = rows[0][0]
    if not SHA_RE.fullmatch(sha):
        raise SyncError("source develop returned an invalid commit")
    return sha


def _validate_source_checkout(
    repository_name: str,
    url: str,
    sha: str,
    checkout: Path,
    *,
    environment: dict[str, str],
) -> None:
    checkout.parent.mkdir(parents=True, exist_ok=True)
    checkout.mkdir()
    _run_git(["init", "--quiet", str(checkout)], cwd=checkout.parent, environment=environment, stage="initializing source checkout")
    _run_git(["remote", "add", "origin", url], cwd=checkout, environment=environment, stage="configuring source checkout")
    _run_git(
        ["fetch", "--quiet", "--no-tags", "origin", f"+{DEVELOP_REF}:refs/remotes/origin/develop"],
        cwd=checkout,
        environment=environment,
        stage=f"fetching {repository_name} develop",
    )
    resolved = _run_git(
        ["rev-parse", "--verify", f"{sha}^{{commit}}"],
        cwd=checkout,
        environment=environment,
        stage=f"checking {repository_name} commit",
    )
    if resolved != sha:
        raise SyncError(f"{repository_name} candidate is not a commit")
    ancestry = _run_git_result(
        ["merge-base", "--is-ancestor", sha, "refs/remotes/origin/develop"],
        cwd=checkout,
        environment=environment,
    )
    if ancestry.returncode:
        raise SyncError(f"{repository_name} candidate is no longer on develop")
    _run_git(
        ["checkout", "--quiet", "--detach", sha],
        cwd=checkout,
        environment=environment,
        stage=f"checking out {repository_name} candidate",
    )
    _run_git(
        [
            "-c",
            "protocol.file.allow=never",
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--checkout",
        ],
        cwd=checkout,
        environment=environment,
        stage=f"checking out nested submodules for {repository_name}",
    )


def _validate_all_sources(
    candidates: dict[str, str],
    root: Path,
    *,
    environment: dict[str, str],
) -> None:
    for repository_name, sha in candidates.items():
        path, url = SOURCES[repository_name]
        checkout = root / f"source-{path}"
        _validate_source_checkout(
            repository_name,
            url,
            sha,
            checkout,
            environment=environment,
        )


def _tree_gitlink(worktree: Path, path: str, *, environment: dict[str, str]) -> str:
    entry = _run_git(
        ["ls-tree", "HEAD", "--", path],
        cwd=worktree,
        environment=environment,
        stage="reading superproject pointer",
    )
    try:
        mode, kind, value = entry.split(" ", 2)
        sha, actual_path = value.split("\t", 1)
    except ValueError:
        raise SyncError("superproject pointer entry is invalid") from None
    if mode != "160000" or kind != "commit" or actual_path != path:
        raise SyncError("superproject pointer is not a gitlink")
    return sha


def _stage_gitlinks(
    worktree: Path, candidates: dict[str, str], *, environment: dict[str, str]
) -> list[str]:
    changed: list[str] = []
    for repository_name, sha in candidates.items():
        path, _ = SOURCES[repository_name]
        if _tree_gitlink(worktree, path, environment=environment) == sha:
            continue
        _run_git(
            ["update-index", "--cacheinfo", f"160000,{sha},{path}"],
            cwd=worktree,
            environment=environment,
            stage="staging submodule pointer",
        )
        changed.append(path)

    if not changed:
        return []

    staged = _run_git(
        ["diff", "--cached", "--name-only", "--no-renames", "-z"],
        cwd=worktree,
        environment=environment,
        stage="checking staged paths",
    ).split("\0")
    staged_paths = {path for path in staged if path}
    if staged_paths != set(changed):
        raise SyncError("staged paths include unexpected changes")
    raw = _run_git(
        ["diff", "--cached", "--raw", "--no-abbrev"],
        cwd=worktree,
        environment=environment,
        stage="checking staged modes",
    )
    expected_shas = {SOURCES[repository][0]: sha for repository, sha in candidates.items()}
    for line in raw.splitlines():
        metadata, separator, path = line.partition("\t")
        modes = metadata.removeprefix(":").split()
        if not separator or len(modes) < 2 or modes[0] != "160000" or modes[1] != "160000":
            raise SyncError("staged change is not a gitlink update")
        if path not in staged_paths:
            raise SyncError("staged path does not match the validated gitlinks")
        if len(modes) < 4 or modes[3] != expected_shas[path]:
            raise SyncError("staged gitlink does not match the validated candidate")
    if len(raw.splitlines()) != len(staged_paths):
        raise SyncError("staged gitlink list is incomplete")
    return sorted(changed)


def _push(worktree: Path, environment: dict[str, str]) -> None:
    result = _run_git_result(
        ["push", SUPERPROJECT_URL, "HEAD:refs/heads/develop"],
        cwd=worktree,
        environment=environment,
    )
    if result.returncode == 0:
        return
    output = f"{result.stdout}\n{result.stderr}".lower()
    if (
        "non-fast-forward" in output
        or "fetch first" in output
        or "[rejected]" in output
        or "cannot lock ref" in output
        or "failed to update ref" in output
    ):
        raise PushConflict("superproject develop changed during push")
    raise SyncError(f"pushing superproject develop failed (git exit {result.returncode})")


def sync(event: dict[str, Any], repository: Path) -> str:
    _validate_event(event)
    source_token = os.environ.get("SUBMODULE_SOURCE_TOKEN")
    push_token = os.environ.get("SUPERPROJECT_PUSH_TOKEN")
    if not source_token or not push_token:
        raise SyncError("role-scoped GitHub App tokens are missing")

    root = repository.resolve()
    if not root.is_dir():
        raise SyncError("superproject checkout directory is missing")
    source_environment = _git_env(source_token, tuple(SOURCES))
    push_environment = _git_env(push_token, ("jayn2u/gods-system",))

    top_level = _run_git(
        ["rev-parse", "--show-toplevel"],
        cwd=root,
        environment=push_environment,
        stage="checking superproject checkout",
    )
    if Path(top_level).resolve() != root:
        raise SyncError("repository path is not the superproject root")
    dirty = _run_git(
        ["status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        environment=push_environment,
        stage="checking superproject worktree",
    )
    if dirty:
        raise SyncError("superproject checkout has local changes")

    candidates = {
        repository_name: _latest_source_sha(url, environment=source_environment, repository=root)
        for repository_name, (_, url) in SOURCES.items()
    }

    run_id = uuid.uuid4().hex
    remote_ref = f"refs/submodule-sync/{run_id}/develop"
    with tempfile.TemporaryDirectory(prefix="gods-system-sync-") as temp_dir:
        temp_root = Path(temp_dir)
        try:
            for attempt in range(1, MAX_PUSH_ATTEMPTS + 1):
                _validate_all_sources(
                    candidates,
                    temp_root / f"validation-{attempt}",
                    environment=source_environment,
                )
                _run_git(
                    [
                        "fetch",
                        "--quiet",
                        "--no-tags",
                        SUPERPROJECT_URL,
                        f"+refs/heads/develop:{remote_ref}",
                    ],
                    cwd=root,
                    environment=push_environment,
                    stage="fetching superproject develop",
                )
                base = _run_git(
                    ["rev-parse", "--verify", remote_ref],
                    cwd=root,
                    environment=push_environment,
                    stage="reading superproject develop",
                )
                worktree = temp_root / f"worktree-{attempt}"
                _run_git(
                    ["worktree", "add", "--detach", str(worktree), base],
                    cwd=root,
                    environment=push_environment,
                    stage="creating isolated superproject worktree",
                )
                try:
                    _validate_superproject_submodules(worktree, environment=push_environment)
                    changed = _stage_gitlinks(worktree, candidates, environment=push_environment)
                    if not changed:
                        return "No submodule pointers changed."
                    _run_git(
                        [
                            "-c",
                            f"user.name={BOT_NAME}",
                            "-c",
                            f"user.email={BOT_EMAIL}",
                            "commit",
                            "--quiet",
                            "-m",
                            "chore: update validated submodule pointers",
                        ],
                        cwd=worktree,
                        environment=push_environment,
                        stage="committing validated submodule pointers",
                    )
                    try:
                        _push(worktree, push_environment)
                    except PushConflict:
                        if attempt == MAX_PUSH_ATTEMPTS:
                            raise SyncError("superproject develop kept changing after three push attempts") from None
                        continue
                    return f"Updated submodule pointers: {', '.join(changed)}."
                finally:
                    _run_git(
                        ["worktree", "remove", "--force", str(worktree)],
                        cwd=root,
                        environment=push_environment,
                        stage="removing isolated superproject worktree",
                    )
        finally:
            _run_git_result(
                ["update-ref", "-d", remote_ref],
                cwd=root,
                environment=push_environment,
            )
    raise SyncError("submodule synchronization did not complete")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-path", required=True, type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        event = _read_event(args.event_path)
        print(sync(event, args.repository))
        return 0
    except SyncError as error:
        print(f"submodule sync failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
