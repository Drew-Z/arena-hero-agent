import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows
    fcntl = None


REPO_ROOT = Path(__file__).resolve().parent
INSTALLER = REPO_ROOT / "scripts" / "install-systemd.sh"
ROLLBACK = REPO_ROOT / "scripts" / "rollback-systemd.sh"


@unittest.skipUnless(
    os.name == "posix" and fcntl is not None,
    "systemd transaction tests require POSIX symlinks and flock",
)
class SystemdDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.install_root = self.root / "opt" / "arena-hero-agent"
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.systemctl_log = self.root / "systemctl.log"
        self.key_file = self.root / "arena-key.txt"
        self.key_file.write_text("test-credential-value\n", encoding="utf-8")
        self._write_fake_commands()

        etc_root = self.root / "etc"
        unit_dir = etc_root / "systemd" / "system"
        runtime_dir = etc_root / "arena-hero-agent"
        rollback_bin = self.root / "usr" / "local" / "sbin" / "arena-hero-rollback"
        unit_dir.mkdir(parents=True)
        runtime_dir.mkdir(parents=True)
        rollback_bin.parent.mkdir(parents=True)
        self.etc_root = etc_root
        self.unit_dir = unit_dir
        self.runtime_dir = runtime_dir
        self.rollback_bin = rollback_bin
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.fake_bin}{os.pathsep}{self.env['PATH']}",
                "PYTHON_BIN": str(self.fake_bin / "python3"),
                "ARENA_INSTALL_ROOT": str(self.install_root),
                "ARENA_AGENT_ENV": str(etc_root / "arena-hero-agent.env"),
                "ARENA_RUNTIME_DIR": str(runtime_dir),
                "ARENA_SUPERVISOR_ENV": str(etc_root / "arena-hero-supervisor.env"),
                "ARENA_SYSTEMD_UNIT_DIR": str(unit_dir),
                "ARENA_ROLLBACK_BIN": str(rollback_bin),
                "ARENA_SYSTEMCTL_BIN": "systemctl",
                "ARENA_HEALTH_ATTEMPTS": "1",
                "ARENA_HEALTH_INTERVAL": "0",
                "FAKE_SYSTEMCTL_LOG": str(self.systemctl_log),
            }
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _write_fake_commands(self) -> None:
        fake_python = r'''#!/bin/sh
set -eu
if [ "${1:-}" = "-c" ]; then
    exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
    target=$3
    mkdir -p "$target/bin"
    cp "$0" "$target/bin/python"
    chmod 0755 "$target/bin/python"
    exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ]; then
    if [ -n "${FAKE_PIP_FAIL_ONCE_FILE:-}" ] && [ ! -e "$FAKE_PIP_FAIL_ONCE_FILE" ]; then
        : > "$FAKE_PIP_FAIL_ONCE_FILE"
        exit 1
    fi
    case " $* " in
        *" --no-build-isolation "*)
            bin_dir=$(dirname "$0")
            for command_name in arena-hero-agent arena-hero-health arena-hero-optimizer arena-hero-supervisor arena-hero-version-monitor; do
                cat > "$bin_dir/$command_name" <<'EOF'
#!/bin/sh
if [ "$(basename "$0")" = "arena-hero-health" ] && [ "$#" -eq 0 ] && [ "${FAKE_HEALTH_FAIL:-0}" = "1" ]; then
    exit 1
fi
exit 0
EOF
                chmod 0755 "$bin_dir/$command_name"
            done
            ;;
    esac
    exit 0
fi
exit 0
'''
        self._write_executable(self.fake_bin / "python3", fake_python)
        self._write_executable(
            self.fake_bin / "systemctl",
            r'''#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_LOG"
if [ "${1:-}" = "is-active" ] || [ "${1:-}" = "is-enabled" ]; then
    exit 1
fi
if [ -n "${FAKE_SYSTEMCTL_SIGNAL_ONCE_PATTERN:-}" ]; then
    case "$*" in
        *"$FAKE_SYSTEMCTL_SIGNAL_ONCE_PATTERN"*)
            marker=${FAKE_SYSTEMCTL_SIGNAL_MARKER:?}
            if [ ! -e "$marker" ]; then
                : > "$marker"
                kill -TERM "$PPID"
                sleep 1
                exit 1
            fi
            ;;
    esac
fi
if [ -n "${FAKE_SYSTEMCTL_FAIL_ONCE_PATTERN:-}" ]; then
    case "$*" in
        *"$FAKE_SYSTEMCTL_FAIL_ONCE_PATTERN"*)
            marker=${FAKE_SYSTEMCTL_FAIL_MARKER:?}
            if [ ! -e "$marker" ]; then
                : > "$marker"
                exit 1
            fi
            ;;
    esac
fi
exit 0
''',
        )
        self._write_executable(
            self.fake_bin / "id",
            "#!/bin/sh\nif [ \"${1:-}\" = \"-u\" ]; then echo 0; fi\nexit 0\n",
        )
        self._write_executable(self.fake_bin / "chown", "#!/bin/sh\nexit 0\n")
        self._write_executable(self.fake_bin / "useradd", "#!/bin/sh\nexit 0\n")
        self._write_executable(
            self.fake_bin / "install",
            f'''#!{sys.executable}
import os
import sys

args = []
i = 1
while i < len(sys.argv):
    if sys.argv[i] in {"-o", "-g"}:
        i += 2
        continue
    args.append(sys.argv[i])
    i += 1
os.execv("/usr/bin/install", ["install", *args])
''',
        )

    def _run(self, script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(script), *args],
            cwd=REPO_ROOT,
            env=env or self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _install(self, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return self._run(
            INSTALLER,
            "--api-key-file",
            str(self.key_file),
            *extra,
            env=env,
        )

    def _resolved(self, name: str) -> Path:
        return (self.install_root / name).resolve(strict=True)

    def test_install_upgrade_and_rollback_swap_immutable_releases(self) -> None:
        first = self._install("--no-start")
        self.assertEqual(first.returncode, 0, first.stderr)
        first_release = self._resolved("current")
        self.assertFalse((self.install_root / "previous").exists())

        second = self._install("--no-start")
        self.assertEqual(second.returncode, 0, second.stderr)
        second_release = self._resolved("current")
        self.assertNotEqual(first_release, second_release)
        self.assertEqual(self._resolved("previous"), first_release)
        self.assertTrue((first_release / ".venv" / "bin" / "arena-hero-agent").is_file())

        rolled_back = self._run(ROLLBACK)
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertEqual(self._resolved("current"), first_release)
        self.assertEqual(self._resolved("previous"), second_release)

    def test_installed_units_and_rollback_use_configured_paths(self) -> None:
        result = self._install("--no-start")

        self.assertEqual(result.returncode, 0, result.stderr)
        agent_unit = (self.unit_dir / "arena-hero-agent.service").read_text(encoding="utf-8")
        self.assertIn(f"WorkingDirectory={self.install_root}/current", agent_unit)
        self.assertIn(f"EnvironmentFile={self.etc_root}/arena-hero-agent.env", agent_unit)
        self.assertIn(f"EnvironmentFile=-{self.runtime_dir}/runtime.env", agent_unit)
        self.assertNotIn("WorkingDirectory=/opt/arena-hero-agent", agent_unit)
        self.assertNotIn("ExecStart=/opt/arena-hero-agent", agent_unit)
        installed_rollback = self.rollback_bin.read_text(encoding="utf-8")
        self.assertIn(f"ARENA_INSTALL_ROOT:-{self.install_root}", installed_rollback)

    def test_build_failure_leaves_active_links_unchanged(self) -> None:
        self.assertEqual(self._install("--no-start").returncode, 0)
        current = self._resolved("current")
        release_count = len(list((self.install_root / "releases").iterdir()))
        env = self.env | {"FAKE_PIP_FAIL_ONCE_FILE": str(self.root / "pip-failed")}

        failed = self._install("--no-start", env=env)

        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self._resolved("current"), current)
        self.assertEqual(len(list((self.install_root / "releases").iterdir())), release_count)

    def test_restart_failure_restores_original_link_pair(self) -> None:
        self.assertEqual(self._install("--no-start").returncode, 0)
        current = self._resolved("current")
        env = self.env | {
            "FAKE_SYSTEMCTL_FAIL_ONCE_PATTERN": "restart arena-hero-agent.service",
            "FAKE_SYSTEMCTL_FAIL_MARKER": str(self.root / "restart-failed"),
        }

        failed = self._install(env=env)

        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self._resolved("current"), current)
        self.assertFalse((self.install_root / "previous").exists())
        self.assertIn("restoring the previous release", failed.stderr)

    def test_health_failure_restores_original_link_pair(self) -> None:
        self.assertEqual(self._install("--no-start").returncode, 0)
        current = self._resolved("current")
        env = self.env | {"FAKE_HEALTH_FAIL": "1"}

        failed = self._install(env=env)

        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self._resolved("current"), current)
        self.assertFalse((self.install_root / "previous").exists())
        calls = self.systemctl_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("disable arena-hero-agent.service", calls)
        self.assertIn("stop arena-hero-agent.service", calls)
        self.assertIn("disable arena-hero-version-monitor.timer", calls)
        self.assertIn("stop arena-hero-version-monitor.timer", calls)

    def test_no_start_activates_without_service_lifecycle_commands(self) -> None:
        result = self._install("--no-start")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.systemctl_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(calls, ["daemon-reload"])

    def test_signal_during_activation_restores_links_and_service_state(self) -> None:
        self.assertEqual(self._install("--no-start").returncode, 0)
        current = self._resolved("current")
        env = self.env | {
            "FAKE_SYSTEMCTL_SIGNAL_ONCE_PATTERN": "start arena-hero-version-monitor.service",
            "FAKE_SYSTEMCTL_SIGNAL_MARKER": str(self.root / "signal-sent"),
        }

        interrupted = self._install(env=env)

        self.assertNotEqual(interrupted.returncode, 0)
        self.assertEqual(self._resolved("current"), current)
        self.assertFalse((self.install_root / "previous").exists())
        self.assertFalse((self.install_root / ".link-transaction").exists())
        calls = self.systemctl_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("disable arena-hero-agent.service", calls)
        self.assertIn("stop arena-hero-agent.service", calls)

    def test_rollback_rejects_previous_link_outside_release_root(self) -> None:
        self.assertEqual(self._install("--no-start").returncode, 0)
        self.assertEqual(self._install("--no-start").returncode, 0)
        current = self._resolved("current")
        outside = self.root / "outside"
        self._create_fake_release(outside)
        previous = self.install_root / "previous"
        previous.unlink()
        previous.symlink_to(outside, target_is_directory=True)

        result = self._run(ROLLBACK)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._resolved("current"), current)
        self.assertIn("No valid previous release", result.stderr)

    def test_pending_link_transaction_is_recovered_before_rollback(self) -> None:
        self.assertEqual(self._install("--no-start").returncode, 0)
        first_release = self._resolved("current")
        self.assertEqual(self._install("--no-start").returncode, 0)
        second_release = self._resolved("current")
        transaction = self.install_root / ".link-transaction"
        transaction.write_text(
            f"{second_release.name}\n{first_release.name}\n",
            encoding="utf-8",
        )
        current = self.install_root / "current"
        current.unlink()
        current.symlink_to(Path("releases") / first_release.name, target_is_directory=True)

        result = self._run(ROLLBACK)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Recovered an interrupted", result.stdout)
        self.assertEqual(self._resolved("current"), first_release)
        self.assertEqual(self._resolved("previous"), second_release)
        self.assertFalse(transaction.exists())

    def test_deployment_lock_rejects_concurrent_rollback(self) -> None:
        self.assertEqual(self._install("--no-start").returncode, 0)
        lock_path = self.install_root / ".deploy.lock"
        with lock_path.open("w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self._run(ROLLBACK)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("in progress", result.stderr)

    def test_legacy_venv_is_preserved_as_first_rollback_target(self) -> None:
        legacy = self.install_root / ".venv"
        self._create_fake_venv(legacy)

        result = self._install("--no-start")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(legacy.is_dir())
        self.assertEqual(self._resolved("previous").name, "legacy-pre-atomic")
        self.assertEqual((self._resolved("previous") / ".venv").resolve(), legacy.resolve())

    def _create_fake_venv(self, path: Path) -> None:
        bin_dir = path / "bin"
        bin_dir.mkdir(parents=True)
        for command_name in (
            "arena-hero-agent",
            "arena-hero-health",
            "arena-hero-optimizer",
            "arena-hero-supervisor",
            "arena-hero-version-monitor",
        ):
            self._write_executable(bin_dir / command_name, "#!/bin/sh\nexit 0\n")

    def _create_fake_release(self, path: Path) -> None:
        self._create_fake_venv(path / ".venv")


if __name__ == "__main__":
    unittest.main()
