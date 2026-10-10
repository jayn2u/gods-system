"""Local-only coverage for the Ansible Vault credential wrapper."""

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ansible_auth.py"
sys.path.insert(0, str(ROOT))

from scripts import ansible_auth  # noqa: E402


class AnsibleVaultAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ansible-auth-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_dir = self.root / "auth"
        self.passwords = {
            "vis-lab": "test-vis-lab-sudo-only",
            "ubuntu": "test-ubuntu-sudo-only",
        }

    def initialize(self):
        values = iter(self.passwords.values())
        ansible_auth.setup_credentials(
            self.config_dir, password_reader=lambda _prompt: next(values)
        )

    def test_setup_stores_host_passwords_only_in_vault_with_private_permissions(self):
        self.initialize()
        key = self.config_dir / "vault-password"
        vault = self.config_dir / "credentials.yml.vault"

        self.assertEqual(0o700, self.config_dir.stat().st_mode & 0o777)
        self.assertEqual(0o600, key.stat().st_mode & 0o777)
        self.assertEqual(0o600, vault.stat().st_mode & 0o777)
        encrypted = vault.read_text()
        self.assertTrue(encrypted.startswith("$ANSIBLE_VAULT;"))
        for secret in self.passwords.values():
            self.assertNotIn(secret, encrypted)

        viewed = subprocess.run(
            ["ansible-vault", "view", "--vault-password-file", str(key), str(vault)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for alias, secret in self.passwords.items():
            self.assertIn(f"{json.dumps(alias)}: {json.dumps(secret)}", viewed)
            host_var = self.config_dir / "host_vars" / f"{alias}.yml"
            self.assertEqual(0o600, host_var.stat().st_mode & 0o777)
            self.assertTrue(host_var.read_text().startswith("$ANSIBLE_VAULT;"))
            self.assertNotIn(secret, host_var.read_text())

    def test_playbook_wrapper_keeps_host_and_delegated_credentials_scoped(self):
        self.initialize()
        inventory = self.root / "inventory.ini"
        inventory.write_text(
            "[all]\n"
            "vis-lab ansible_connection=local\n"
            "ubuntu ansible_connection=local\n"
        )
        playbook = self.root / "auth-scope.yml"
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        sudo = fake_bin / "sudo"
        state = self.root / "sudo-call-count"
        sudo.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys\n"
            f"state = pathlib.Path({str(state)!r})\n"
            f"expected = {list(self.passwords.values())!r}\n"
            "count = int(state.read_text() if state.exists() else '0')\n"
            "print(sys.argv[sys.argv.index('-p') + 1], end='', flush=True)\n"
            "password = sys.stdin.readline().rstrip('\\r\\n')\n"
            "if count >= len(expected) or password != expected[count]: sys.exit(91)\n"
            "state.write_text(str(count + 1))\n"
            "command = sys.argv[sys.argv.index('-c') + 1]\n"
            "os.execv('/bin/sh', ['/bin/sh', '-c', command])\n"
        )
        sudo.chmod(0o700)
        playbook.write_text(
            "- hosts: vis-lab\n"
            "  gather_facts: false\n"
            "  tasks:\n"
            "    - name: Exercise become on vis-lab with its own credential\n"
            "      ansible.builtin.command: /bin/true\n"
            "      become: true\n"
            "      no_log: true\n"
            "    - name: Exercise delegated become on ubuntu with its own credential\n"
            "      ansible.builtin.command: /bin/true\n"
            "      delegate_to: ubuntu\n"
            "      become: true\n"
            "      no_log: true\n"
        )
        environment = os.environ.copy()
        environment["PATH"] = str(fake_bin) + ":" + environment.get("PATH", "")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--config-dir",
                str(self.config_dir),
                "playbook",
                "--inventory",
                str(inventory),
                str(playbook),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("2", state.read_text())
        for secret in self.passwords.values():
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_automated_playbook_fails_closed_without_credentials(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--config-dir",
                str(self.config_dir),
                "playbook",
                "--inventory",
                "localhost,",
                "-e",
                "ansible_connection=local",
                str(self.root / "unused.yml"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("credentials", (result.stdout + result.stderr).lower())

    def test_setup_refuses_noninteractive_input_without_creating_storage(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--config-dir", str(self.config_dir), "setup"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("real terminal", result.stderr)
        self.assertFalse(self.config_dir.exists())

    def test_wrapper_rejects_interactive_password_flags(self):
        self.initialize()
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--config-dir",
                str(self.config_dir),
                "playbook",
                "--ask-become-pass",
                "--inventory",
                "localhost,",
                str(self.root / "unused.yml"),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Interactive Ansible authentication flags are disabled", result.stderr)
        for secret in self.passwords.values():
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_check_rejects_insecure_vault_key_permissions(self):
        self.initialize()
        (self.config_dir / "vault-password").chmod(0o644)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--config-dir", str(self.config_dir), "check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("mode 0600", result.stderr)

    def test_check_rejects_corrupted_vault(self):
        self.initialize()
        vault = self.config_dir / "credentials.yml.vault"
        vault.write_text("$ANSIBLE_VAULT;1.1;AES256\nnot-a-valid-vault-payload\n")
        vault.chmod(0o600)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--config-dir", str(self.config_dir), "check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("could not be decrypted", result.stderr)

    def test_check_rejects_symlinked_vault(self):
        self.initialize()
        vault = self.config_dir / "credentials.yml.vault"
        outside = self.root / "outside.vault"
        outside.write_text(vault.read_text())
        outside.chmod(0o600)
        vault.unlink()
        vault.symlink_to(outside)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--config-dir", str(self.config_dir), "check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("not a symlink", result.stderr)

    def test_wrapper_refuses_inventory_that_lacks_a_credential_alias(self):
        self.initialize()
        inventory = self.root / "incomplete.ini"
        inventory.write_text("[all]\nvis-lab ansible_connection=local\n")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--config-dir",
                str(self.config_dir),
                "playbook",
                "--inventory",
                str(inventory),
                str(self.root / "unused.yml"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("caller inventory", result.stderr)


if __name__ == "__main__":
    unittest.main()
