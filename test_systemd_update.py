from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parent
UPDATER = REPO_ROOT / "scripts" / "update-systemd.sh"


@unittest.skipUnless(os.name == "posix", "systemd updater tests require POSIX sh")
class SystemdUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.project = self.root / "project"
        self.scripts = self.project / "scripts"
        self.fake_bin = self.root / "fake-bin"
        self.scripts.mkdir(parents=True)
        self.fake_bin.mkdir()

        updater = self.scripts / "update-systemd.sh"
        updater.write_text(UPDATER.read_text(encoding="utf-8"), encoding="utf-8")
        updater.chmod(0o755)
        self.updater = updater
        self.git_log = self.root / "git.log"
        self.sudo_log = self.root / "sudo.log"
        self.installer_log = self.root / "installer.log"
        self.merge_marker = self.root / "merge-target.txt"
        self.archive_dir = self.root / "archives"
        self.archive_dir.mkdir()
        self._write_fake_commands()
        self._write_executable(
            self.scripts / "install-systemd.sh",
            r'''#!/bin/sh
printf 'key=%s\n' "${ARENA_HERO_API_KEY-unset}" > "$FAKE_INSTALLER_LOG"
printf 'commit=%s\n' "${ARENA_SOURCE_COMMIT-unset}" >> "$FAKE_INSTALLER_LOG"
if [ -n "${ARENA_PIP_INDEX_URL:-}" ]; then
    printf 'index=%s\n' "$ARENA_PIP_INDEX_URL" >> "$FAKE_INSTALLER_LOG"
fi
exit "${FAKE_INSTALLER_EXIT:-0}"
''',
        )

        self.env = os.environ.copy()
        self.env.update(
            {
                "ARENA_UPDATE_GIT_BIN": str(self.fake_bin / "git"),
                "ARENA_UPDATE_SUDO_BIN": str(self.fake_bin / "sudo"),
                "ARENA_UPDATE_ID_BIN": str(self.fake_bin / "id"),
                "ARENA_UPDATE_STAT_BIN": str(self.fake_bin / "stat"),
                "FAKE_PROJECT_ROOT": str(self.project),
                "FAKE_GIT_LOG": str(self.git_log),
                "FAKE_SUDO_LOG": str(self.sudo_log),
                "FAKE_INSTALLER_LOG": str(self.installer_log),
                "FAKE_MERGE_MARKER": str(self.merge_marker),
                "TMPDIR": str(self.archive_dir),
                "FAKE_CURRENT_COMMIT": "1111111111111111111111111111111111111111",
                "FAKE_TARGET_COMMIT": "2222222222222222222222222222222222222222",
            }
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _write_fake_commands(self) -> None:
        self._write_executable(
            self.fake_bin / "git",
            r'''#!/bin/sh
set -eu
if [ "${1:-}" != "-C" ] || [ "${2:-}" != "$FAKE_PROJECT_ROOT" ]; then
    exit 90
fi
shift 2
command_name=$1
shift
printf '%s %s\n' "$command_name" "$*" >> "$FAKE_GIT_LOG"
case "$command_name" in
    rev-parse)
        case "$*" in
            --show-toplevel) printf '%s\n' "$FAKE_PROJECT_ROOT" ;;
            '--abbrev-ref --symbolic-full-name @{upstream}') printf 'origin/main\n' ;;
            '--symbolic-full-name @{upstream}') printf 'refs/remotes/origin/main\n' ;;
            '--verify HEAD^{commit}')
                if [ -e "$FAKE_MERGE_MARKER" ]; then
                    printf '%s\n' "$FAKE_TARGET_COMMIT"
                else
                    printf '%s\n' "$FAKE_CURRENT_COMMIT"
                fi
                ;;
            '--verify @{upstream}^{commit}') printf '%s\n' "$FAKE_TARGET_COMMIT" ;;
            '--short=12 '*) printf '%.12s\n' "$FAKE_TARGET_COMMIT" ;;
            *) exit 91 ;;
        esac
        ;;
    status)
        printf '%s' "${FAKE_GIT_STATUS:-}"
        ;;
    symbolic-ref)
        printf 'main\n'
        ;;
    config)
        case "$*" in
            '--get branch.main.remote') printf 'origin\n' ;;
            '--get branch.main.merge') printf 'refs/heads/main\n' ;;
            *) exit 93 ;;
        esac
        ;;
    fetch)
        exit "${FAKE_FETCH_EXIT:-0}"
        ;;
    merge-base)
        [ "${FAKE_NON_FAST_FORWARD:-0}" = "0" ]
        ;;
    merge)
        printf '%s\n' "$FAKE_TARGET_COMMIT" > "$FAKE_MERGE_MARKER"
        ;;
    archive)
        [ "${1:-}" = "--format=tar" ] || exit 94
        [ "${2:-}" = "--output" ] || exit 94
        tar -cf "$3" -C "$FAKE_PROJECT_ROOT" scripts/install-systemd.sh
        ;;
    *)
        exit 92
        ;;
esac
''',
        )
        self._write_executable(
            self.fake_bin / "sudo",
            r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SUDO_LOG"
if [ "${FAKE_MUTATE_CHECKOUT_INSTALLER:-0}" = "1" ]; then
    printf '#!/bin/sh\nexit 88\n' > "$FAKE_PROJECT_ROOT/scripts/install-systemd.sh"
fi
exec "$@"
''',
        )
        self._write_executable(
            self.fake_bin / "id",
            "#!/bin/sh\nif [ \"${1:-}\" = \"-u\" ]; then echo 1000; fi\n",
        )
        self._write_executable(
            self.fake_bin / "stat",
            "#!/bin/sh\nprintf '%s\\n' \"${FAKE_REPOSITORY_UID:-1000}\"\n",
        )

    def _run(
        self,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(self.updater), *args],
            cwd=self.project,
            env=env or self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_fast_forward_deploys_once_without_forwarding_api_key(self) -> None:
        env = self.env | {"ARENA_HERO_API_KEY": "must-not-reach-installer"}

        result = self._run(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.installer_log.read_text(encoding="utf-8"),
            f"key=\ncommit={self.env['FAKE_TARGET_COMMIT']}\n",
        )
        self.assertEqual(
            self.merge_marker.read_text(encoding="utf-8").strip(),
            self.env["FAKE_TARGET_COMMIT"],
        )
        self.assertNotIn("must-not-reach-installer", result.stdout + result.stderr)
        self.assertIn("the new strategy is running", result.stdout)
        self.assertIn("stopped any previous strategy process", result.stdout)
        self.assertEqual(list(self.archive_dir.iterdir()), [])
        git_calls = self.git_log.read_text(encoding="utf-8")
        self.assertIn(
            "fetch --prune origin +refs/heads/main:refs/remotes/origin/main",
            git_calls,
        )

    def test_deploys_archived_commit_when_checkout_changes_before_sudo(self) -> None:
        env = self.env | {"FAKE_MUTATE_CHECKOUT_INSTALLER": "1"}

        result = self._run(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.installer_log.read_text(encoding="utf-8"),
            f"key=\ncommit={self.env['FAKE_TARGET_COMMIT']}\n",
        )
        self.assertIn("exit 88", (self.scripts / "install-systemd.sh").read_text())

    def test_forwards_explicit_https_package_index_through_sudo(self) -> None:
        env = self.env | {
            "ARENA_HERO_API_KEY": "must-not-reach-installer",
            "ARENA_PIP_INDEX_URL": "https://pypi.org/simple",
        }

        result = self._run(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.installer_log.read_text(encoding="utf-8"),
            (
                f"key=\ncommit={self.env['FAKE_TARGET_COMMIT']}\n"
                "index=https://pypi.org/simple\n"
            ),
        )
        self.assertNotIn("must-not-reach-installer", result.stdout + result.stderr)
        self.assertIn(
            "ARENA_PIP_INDEX_URL=https://pypi.org/simple",
            self.sudo_log.read_text(encoding="utf-8"),
        )

    def test_rejects_unsafe_package_index_before_git(self) -> None:
        for index_url in (
            "http://pypi.org/simple",
            "https://user@example.com/simple",
            "https:///simple",
            "https://pypi.org/simple path",
        ):
            with self.subTest(index_url=index_url):
                result = self._run(
                    env=self.env | {"ARENA_PIP_INDEX_URL": index_url}
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn("ARENA_PIP_INDEX_URL", result.stderr)
                self.assertFalse(self.git_log.exists())
                self.assertFalse(self.installer_log.exists())

    def test_current_upstream_is_redeployed_without_merge(self) -> None:
        env = self.env | {
            "FAKE_TARGET_COMMIT": self.env["FAKE_CURRENT_COMMIT"],
        }

        result = self._run(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.installer_log.is_file())
        self.assertFalse(self.merge_marker.exists())

    def test_dirty_worktree_stops_before_fetch_or_install(self) -> None:
        env = self.env | {"FAKE_GIT_STATUS": " M arena_farmer.py\n"}

        result = self._run(env=env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("worktree is not clean", result.stderr)
        self.assertNotIn("fetch", self.git_log.read_text(encoding="utf-8"))
        self.assertFalse(self.installer_log.exists())

    def test_non_fast_forward_stops_before_merge_or_install(self) -> None:
        env = self.env | {"FAKE_NON_FAST_FORWARD": "1"}

        result = self._run(env=env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("not a fast-forward", result.stderr)
        self.assertFalse(self.merge_marker.exists())
        self.assertFalse(self.installer_log.exists())

    def test_installer_failure_exit_code_is_preserved(self) -> None:
        env = self.env | {"FAKE_INSTALLER_EXIT": "75"}

        result = self._run(env=env)

        self.assertEqual(result.returncode, 75)
        self.assertIn("Review", result.stderr)
        self.assertNotIn("kept or restored", result.stderr)

    def test_checkout_owner_mismatch_stops_before_fetch_or_install(self) -> None:
        env = self.env | {"FAKE_REPOSITORY_UID": "2000"}

        result = self._run(env=env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("checkout owner", result.stderr)
        self.assertNotIn("fetch", self.git_log.read_text(encoding="utf-8"))
        self.assertFalse(self.installer_log.exists())

    def test_help_rejects_extra_arguments(self) -> None:
        result = self._run("--help", "--python")

        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.git_log.exists())

    def test_sudo_invocation_is_rejected_before_git(self) -> None:
        self._write_executable(
            self.fake_bin / "id",
            "#!/bin/sh\nif [ \"${1:-}\" = \"-u\" ]; then echo 0; fi\n",
        )
        env = self.env | {"SUDO_USER": "operator"}

        result = self._run(env=env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("without sudo", result.stderr)
        self.assertFalse(self.git_log.exists())


if __name__ == "__main__":
    unittest.main()
