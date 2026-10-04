from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "scripts" / "sync_submodules.py"
SOURCES = {
    "jayn2u/gods-eye": "gods-eye",
    "jayn2u/gods-mlops": "gods-mlops",
    "jayn2u/gods-watching": "gods-watching",
}
EVENT_TYPE = "submodule-develop-updated"


def git(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr}"
        )
    return result.stdout.strip()


class SyncSubmodulesGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="submodule-sync-test-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.bares = self.root / "bare"
        self.bares.mkdir()
        self.source_bares: dict[str, Path] = {}
        self.source_clones: dict[str, Path] = {}
        self.initial_shas: dict[str, str] = {}

        for repository in SOURCES:
            slug = repository.rsplit("/", 1)[1]
            bare = self.bares / f"{slug}.git"
            clone = self.root / f"source-{slug}"
            git("init", "--bare", "--initial-branch=develop", str(bare))
            git("init", "--initial-branch=develop", str(clone))
            self._configure_identity(clone)
            (clone / "README.md").write_text(f"{repository} initial\n")
            git("add", "README.md", cwd=clone)
            git("commit", "-m", "initial", cwd=clone)
            git("remote", "add", "origin", bare.as_uri(), cwd=clone)
            git("push", "origin", "HEAD:refs/heads/develop", cwd=clone)
            self.source_bares[repository] = bare
            self.source_clones[repository] = clone
            self.initial_shas[repository] = git("rev-parse", "HEAD", cwd=clone)

        self.superproject_bare = self.bares / "gods-system.git"
        git("init", "--bare", "--initial-branch=develop", str(self.superproject_bare))
        root_seed = self.root / "superproject-seed"
        git("init", "--initial-branch=develop", str(root_seed))
        self._configure_identity(root_seed)
        (root_seed / "README.md").write_text("superproject\n")
        module_lines: list[str] = []
        for repository, path in SOURCES.items():
            module_lines.extend(
                [
                    f'[submodule "{path}"]',
                    f"\tpath = {path}",
                    f"\turl = git@github.com:{repository}.git",
                    "\tbranch = develop",
                    "",
                ]
            )
            (root_seed / path).mkdir()
        (root_seed / ".gitmodules").write_text("\n".join(module_lines))
        git("add", "README.md", ".gitmodules", cwd=root_seed)
        for repository, path in SOURCES.items():
            git(
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{self.initial_shas[repository]},{path}",
                cwd=root_seed,
            )
        git("commit", "-m", "initial pointers", cwd=root_seed)
        git("remote", "add", "origin", self.superproject_bare.as_uri(), cwd=root_seed)
        git("push", "origin", "HEAD:refs/heads/develop", cwd=root_seed)

        self.repository = self.root / "superproject-checkout"
        git(
            "clone",
            "--branch",
            "develop",
            self.superproject_bare.as_uri(),
            str(self.repository),
        )
        self.initial_superproject_sha = git(
            "--git-dir", str(self.superproject_bare), "rev-parse", "refs/heads/develop"
        )

        self.git_config = self.root / "gitconfig"
        config_lines: list[str] = []
        for repository, bare in self.source_bares.items():
            config_lines.extend(
                [
                    f'[url "{bare.as_uri()}"]',
                    f"\tinsteadOf = https://github.com/{repository}.git",
                ]
            )
        config_lines.extend(
            [
                f'[url "{self.superproject_bare.as_uri()}"]',
                "\tinsteadOf = https://github.com/jayn2u/gods-system.git",
            ]
        )
        self.git_config.write_text("\n".join(config_lines) + "\n")
        self.cli_env = os.environ.copy()
        self.cli_env.update(
            {
                "GIT_CONFIG_GLOBAL": str(self.git_config),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ALLOW_PROTOCOL": "file",
                "SUBMODULE_SOURCE_TOKEN": "test-source-read-token",
                "SUPERPROJECT_PUSH_TOKEN": "test-superproject-write-token",
            }
        )

    @staticmethod
    def _configure_identity(repository: Path) -> None:
        git("config", "user.name", "Git Test", cwd=repository)
        git("config", "user.email", "git-test@example.invalid", cwd=repository)

    def _event(
        self,
        repository: str = "jayn2u/gods-eye",
        *,
        sha: str | None = None,
        ref: str = "refs/heads/develop",
    ) -> dict[str, object]:
        return {
            "action": EVENT_TYPE,
            "client_payload": {
                "repository": repository,
                "ref": ref,
                "sha": sha or self.initial_shas.get(repository, "0" * 40),
                "run_url": f"https://github.com/{repository}/actions/runs/123",
            },
        }

    def _run_sync(self, event: dict[str, object] | None = None) -> subprocess.CompletedProcess[str]:
        event_path = self.root / "event.json"
        event_path.write_text(json.dumps(event or self._event()))
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--event-path",
                str(event_path),
                "--repository",
                str(self.repository),
            ],
            cwd=REPOSITORY,
            env=self.cli_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _advance_source(self, repository: str, contents: str) -> str:
        clone = self.source_clones[repository]
        marker = clone / "revision.txt"
        marker.write_text(contents)
        git("add", "revision.txt", cwd=clone)
        git("commit", "-m", "advance develop", cwd=clone)
        git("push", "origin", "HEAD:refs/heads/develop", cwd=clone)
        return git("rev-parse", "HEAD", cwd=clone)

    def _remote_gitlink(self, path: str) -> str:
        entry = git(
            "--git-dir",
            str(self.superproject_bare),
            "ls-tree",
            "refs/heads/develop",
            "--",
            path,
        )
        mode, kind, sha_and_path = entry.split(" ", 2)
        sha, actual_path = sha_and_path.split("\t", 1)
        self.assertEqual("160000", mode)
        self.assertEqual("commit", kind)
        self.assertEqual(path, actual_path)
        return sha

    def _remote_files(self) -> set[str]:
        listing = git(
            "--git-dir", str(self.superproject_bare), "ls-tree", "-r", "--name-only", "develop"
        )
        return set(listing.splitlines())

    def _install_external_push_hook(self, *, pushes: int) -> tuple[Path, Path]:
        external = self.root / "external-clone"
        git("clone", "--branch", "develop", self.superproject_bare.as_uri(), str(external))
        self._configure_identity(external)
        hooks = self.root / "hooks"
        hooks.mkdir()
        counter = self.root / "external-push-count"
        hook = hooks / "pre-push"
        hook.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX\n"
            f"counter={shlex.quote(str(counter))}\n"
            f"external={shlex.quote(str(external))}\n"
            "count=0\n"
            "if [ -f \"$counter\" ]; then count=$(cat \"$counter\"); fi\n"
            "count=$((count + 1))\n"
            "printf '%s\\n' \"$count\" > \"$counter\"\n"
            f"if [ \"$count\" -le {pushes} ]; then\n"
            "  git -C \"$external\" fetch --quiet origin develop\n"
            "  git -C \"$external\" reset --hard --quiet origin/develop\n"
            "  printf 'preserved external change %s\\n' \"$count\" > \"$external/external-$count.txt\"\n"
            "  git -C \"$external\" add -- \"external-$count.txt\"\n"
            "  git -C \"$external\" -c user.name=External -c user.email=external@example.invalid commit --quiet -m \"external change $count\"\n"
            "  git -C \"$external\" push --quiet origin HEAD:refs/heads/develop\n"
            "fi\n"
            "exit 0\n"
        )
        hook.chmod(0o755)
        git("config", "core.hooksPath", str(hooks), cwd=self.repository)
        return hook, counter

    def test_changed_source_updates_only_its_gitlink(self) -> None:
        latest = self._advance_source("jayn2u/gods-eye", "second\n")

        result = self._run_sync()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(latest, self._remote_gitlink("gods-eye"))
        self.assertEqual(self.initial_shas["jayn2u/gods-mlops"], self._remote_gitlink("gods-mlops"))
        changed = git(
            "--git-dir",
            str(self.superproject_bare),
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "develop",
        )
        self.assertEqual({"gods-eye"}, set(changed.splitlines()))

    def test_noop_does_not_create_a_superproject_commit(self) -> None:
        result = self._run_sync()

        self.assertEqual(0, result.returncode, result.stderr)
        latest = git("--git-dir", str(self.superproject_bare), "rev-parse", "develop")
        self.assertEqual(self.initial_superproject_sha, latest)

    def test_stale_event_adopts_current_develop_sha(self) -> None:
        stale = self.initial_shas["jayn2u/gods-eye"]
        latest = self._advance_source("jayn2u/gods-eye", "latest after stale event\n")

        result = self._run_sync(self._event(sha=stale))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotEqual(stale, latest)
        self.assertEqual(latest, self._remote_gitlink("gods-eye"))

    def test_one_run_adopts_changes_from_multiple_sources(self) -> None:
        eye_sha = self._advance_source("jayn2u/gods-eye", "eye latest\n")
        mlops_sha = self._advance_source("jayn2u/gods-mlops", "mlops latest\n")

        result = self._run_sync(self._event(repository="jayn2u/gods-mlops"))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(eye_sha, self._remote_gitlink("gods-eye"))
        self.assertEqual(mlops_sha, self._remote_gitlink("gods-mlops"))

    def test_invalid_event_is_rejected_without_a_push(self) -> None:
        event = self._event(repository="attacker/repository")

        result = self._run_sync(event)

        self.assertEqual(1, result.returncode, result.stderr)
        latest = git("--git-dir", str(self.superproject_bare), "rev-parse", "develop")
        self.assertEqual(self.initial_superproject_sha, latest)

    def test_unavailable_candidate_tree_fails_without_a_push(self) -> None:
        source = "jayn2u/gods-eye"
        candidate = self._advance_source(source, "candidate with missing tree\n")
        tree = git("--git-dir", str(self.source_bares[source]), "rev-parse", f"{candidate}^{{tree}}")
        object_path = self.source_bares[source] / "objects" / tree[:2] / tree[2:]
        self.assertTrue(object_path.exists(), "test fixture expects a loose tree object")
        object_path.unlink()

        result = self._run_sync(self._event(sha=candidate))

        self.assertEqual(1, result.returncode, result.stderr)
        latest = git("--git-dir", str(self.superproject_bare), "rev-parse", "develop")
        self.assertEqual(self.initial_superproject_sha, latest)

    def test_nested_checkout_failure_leaves_all_pointers_unchanged(self) -> None:
        source = "jayn2u/gods-eye"
        clone = self.source_clones[source]
        (clone / ".gitmodules").write_text(
            '[submodule "nested"]\n'
            "\tpath = nested\n"
            "\turl = https://github.com/jayn2u/missing-nested.git\n"
            "\tupdate = none\n"
        )
        (clone / "nested").mkdir(exist_ok=True)
        git("add", ".gitmodules", cwd=clone)
        git(
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{'1' * 40},nested",
            cwd=clone,
        )
        git("commit", "-m", "add unavailable nested module", cwd=clone)
        git("push", "origin", "HEAD:refs/heads/develop", cwd=clone)
        with self.git_config.open("a") as config:
            config.write(f'\n[url "{(self.root / "not-present.git").as_uri()}"]\n')
            config.write("\tinsteadOf = https://github.com/jayn2u/missing-nested.git\n")

        result = self._run_sync(self._event(sha=git("rev-parse", "HEAD", cwd=clone)))

        self.assertEqual(1, result.returncode, result.stderr)
        latest = git("--git-dir", str(self.superproject_bare), "rev-parse", "develop")
        self.assertEqual(self.initial_superproject_sha, latest)

    def test_external_push_is_preserved_when_pointer_update_retries(self) -> None:
        latest = self._advance_source("jayn2u/gods-eye", "pointer candidate\n")
        _, counter = self._install_external_push_hook(pushes=1)

        result = self._run_sync()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("2", counter.read_text().strip())
        self.assertEqual(latest, self._remote_gitlink("gods-eye"))
        self.assertTrue(any(name.startswith("external-") for name in self._remote_files()))

    def test_repeated_external_pushes_stop_after_three_attempts(self) -> None:
        self._advance_source("jayn2u/gods-eye", "pointer candidate\n")
        _, counter = self._install_external_push_hook(pushes=3)

        result = self._run_sync()

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual("3", counter.read_text().strip())
        self.assertEqual(self.initial_shas["jayn2u/gods-eye"], self._remote_gitlink("gods-eye"))
        self.assertEqual(
            {"external-1.txt", "external-2.txt", "external-3.txt"},
            {name for name in self._remote_files() if name.startswith("external-")},
        )


if __name__ == "__main__":
    unittest.main()
