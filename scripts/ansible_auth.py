#!/usr/bin/env python3
"""Run Ansible with host-specific become credentials encrypted by Ansible Vault."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence


DEFAULT_CONFIG_DIR = Path.home() / ".config" / "ansible" / "gods-system"
DEFAULT_HOSTS = ("vis-lab", "ubuntu")
KEY_NAME = "vault-password"
VAULT_NAME = "credentials.yml.vault"
INVENTORY_NAME = "credentials.inventory.ini"
HOST_VARS_NAME = "host_vars"


class AuthConfigurationError(RuntimeError):
    """The local Vault credential store is missing or unsafe to use."""


def _ensure_private_directory(config_dir: Path, *, create: bool) -> None:
    try:
        info = config_dir.lstat()
    except FileNotFoundError:
        if not create:
            raise AuthConfigurationError("Ansible Vault credentials are not initialized; run setup first.")
        config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            info = config_dir.lstat()
        except FileNotFoundError as exc:
            raise AuthConfigurationError("Could not create the Ansible Vault credential directory.") from exc
        if stat.S_ISLNK(info.st_mode):
            raise AuthConfigurationError("The Ansible Vault credential directory must not be a symlink.")
        if info.st_uid != os.getuid():
            raise AuthConfigurationError("The Ansible Vault credential directory must be owned by this user.")
        config_dir.chmod(0o700)
        info = config_dir.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise AuthConfigurationError("The Ansible Vault credential path must be a real directory.")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise AuthConfigurationError("The Ansible Vault credential directory must be user-owned with mode 0700.")


def _validate_private_file(path: Path, *, create_allowed: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if create_allowed:
            return
        raise AuthConfigurationError(
            "Ansible Vault credentials are missing or incomplete; run setup in a terminal first."
        )
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise AuthConfigurationError(f"Credential file {path.name} must be a regular file, not a symlink.")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        raise AuthConfigurationError(f"Credential file {path.name} must be user-owned, single-linked, and mode 0600.")


def _write_new_key(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        _validate_private_file(path)
        return
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(secrets.token_hex(32).encode("ascii") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def _atomic_private_write(path: Path, contents: bytes) -> None:
    if path.exists() or path.is_symlink():
        _validate_private_file(path, create_allowed=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".ansible-auth-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _vault_encrypt(plaintext: bytes, key_path: Path) -> bytes:
    executable = shutil.which("ansible-vault")
    if executable is None:
        raise AuthConfigurationError("ansible-vault is unavailable; no credentials were written.")
    try:
        result = subprocess.run(
            [executable, "encrypt", "--vault-password-file", str(key_path), "--output", "-"],
            input=plaintext,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuthConfigurationError("ansible-vault could not encrypt the credential payload.") from exc
    if result.returncode != 0 or not result.stdout.startswith(b"$ANSIBLE_VAULT;"):
        raise AuthConfigurationError("ansible-vault could not encrypt the credential payload.")
    return result.stdout


def setup_credentials(
    config_dir: Path,
    *,
    password_reader: Callable[[str], str] = getpass.getpass,
) -> None:
    """Prompt for host passwords and write only an encrypted credential file."""
    aliases = DEFAULT_HOSTS
    if not aliases or len(set(aliases)) != len(aliases) or any(
        not re.fullmatch(r"[A-Za-z0-9_.-]+", alias) or alias in {".", ".."} for alias in aliases
    ):
        raise AuthConfigurationError("Host aliases must be distinct and contain only letters, digits, dot, dash, or underscore.")

    config_dir = Path(config_dir).expanduser()
    _ensure_private_directory(config_dir, create=True)
    key_path = config_dir / KEY_NAME
    vault_path = config_dir / VAULT_NAME
    host_vars_dir = config_dir / HOST_VARS_NAME
    _ensure_private_directory(host_vars_dir, create=True)
    _validate_private_file(key_path, create_allowed=True)
    _validate_private_file(vault_path, create_allowed=True)

    passwords: dict[str, str] = {}
    for alias in aliases:
        value = password_reader(f"sudo password for Ansible host '{alias}': ")
        if not isinstance(value, str) or not value:
            raise AuthConfigurationError(f"A non-empty sudo password is required for host '{alias}'.")
        passwords[alias] = value

    _write_new_key(key_path)
    lines = ["vault_become_passwords:"]
    lines.extend(f"  {json.dumps(alias)}: {json.dumps(password)}" for alias, password in passwords.items())
    encrypted = _vault_encrypt(("\n".join(lines) + "\n").encode("utf-8"), key_path)
    encrypted_host_vars = {
        alias: _vault_encrypt(
            f"ansible_become_password: {json.dumps(password)}\n".encode("utf-8"), key_path
        )
        for alias, password in passwords.items()
    }

    _atomic_private_write(vault_path, encrypted)
    for alias, host_vars in encrypted_host_vars.items():
        _atomic_private_write(host_vars_dir / f"{alias}.yml", host_vars)
    inventory = "[all]\n" + "".join(f"{alias}\n" for alias in aliases)
    _atomic_private_write(config_dir / INVENTORY_NAME, inventory.encode("utf-8"))


def _paths(config_dir: Path) -> tuple[Path, Path]:
    config_dir = Path(config_dir).expanduser()
    _ensure_private_directory(config_dir, create=False)
    key_path = config_dir / KEY_NAME
    vault_path = config_dir / VAULT_NAME
    _validate_private_file(key_path)
    _validate_private_file(vault_path)
    if not key_path.read_bytes().strip():
        raise AuthConfigurationError("The Ansible Vault password file is empty; run setup again.")
    return key_path, vault_path


def _host_var_paths(config_dir: Path) -> dict[str, Path]:
    host_vars_dir = Path(config_dir) / HOST_VARS_NAME
    _ensure_private_directory(host_vars_dir, create=False)
    expected_names = {f"{alias}.yml" for alias in DEFAULT_HOSTS}
    try:
        actual_names = {path.name for path in host_vars_dir.iterdir()}
    except OSError as exc:
        raise AuthConfigurationError("Per-host Vault credential files could not be listed.") from exc
    if actual_names != expected_names:
        raise AuthConfigurationError("Per-host Vault credential files are incomplete or unexpected; run setup again.")
    paths: dict[str, Path] = {}
    for path in sorted(host_vars_dir.glob("*.yml")):
        alias = path.stem
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", alias) or alias in {".", ".."}:
            raise AuthConfigurationError("A credential host alias is invalid; run setup again.")
        _validate_private_file(path)
        paths[alias] = path
    if not paths:
        raise AuthConfigurationError("Per-host Vault credentials are missing; run setup again.")
    return paths


def _validate_auth_inventory(config_dir: Path, aliases: Sequence[str]) -> Path:
    inventory = Path(config_dir) / INVENTORY_NAME
    _validate_private_file(inventory)
    expected = "[all]\n" + "".join(f"{alias}\n" for alias in aliases)
    try:
        contents = inventory.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AuthConfigurationError("The generated Vault inventory could not be read.") from exc
    if contents != expected:
        raise AuthConfigurationError("The generated Vault inventory changed; rerun setup before using it.")
    return inventory


def _validate_vault(key_path: Path, vault_path: Path) -> None:
    executable = shutil.which("ansible-vault")
    if executable is None:
        raise AuthConfigurationError("ansible-vault is unavailable; no Ansible command ran.")
    try:
        result = subprocess.run(
            [executable, "view", "--vault-password-file", str(key_path), str(vault_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuthConfigurationError("The Ansible Vault credential file could not be checked.") from exc
    if result.returncode != 0:
        raise AuthConfigurationError("The Ansible Vault credential file could not be decrypted; no Ansible command ran.")


def check_credentials(config_dir: Path) -> None:
    key_path, vault_path = _paths(config_dir)
    _validate_vault(key_path, vault_path)
    host_var_paths = _host_var_paths(config_dir)
    if set(host_var_paths) != set(DEFAULT_HOSTS):
        raise AuthConfigurationError("Vault host credentials do not match the current inventory aliases; run setup again.")
    _validate_auth_inventory(config_dir, DEFAULT_HOSTS)
    for path in host_var_paths.values():
        _validate_vault(key_path, path)


def run_ansible(config_dir: Path, executable_name: str, ansible_args: Sequence[str]) -> int:
    key_path, vault_path = _paths(config_dir)
    _validate_vault(key_path, vault_path)
    blocked_auth_flags = {"-K", "--ask-become-pass", "-k", "--ask-pass", "-J", "--ask-vault-pass", "--ask-vault-password"}
    if any(arg.split("=", 1)[0] in blocked_auth_flags for arg in ansible_args):
        raise AuthConfigurationError("Interactive Ansible authentication flags are disabled; use the Vault setup command.")
    host_var_paths = _host_var_paths(config_dir)
    if set(host_var_paths) != set(DEFAULT_HOSTS):
        raise AuthConfigurationError("Vault host credentials do not match the current inventory aliases; run setup again.")
    for path in host_var_paths.values():
        _validate_vault(key_path, path)

    inventory_args: list[str] = []
    tokens = list(ansible_args)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"-i", "--inventory", "--inventory-file"} and index + 1 < len(tokens):
            inventory_args.extend([token, tokens[index + 1]])
            index += 2
            continue
        if token.startswith("--inventory=") or token.startswith("--inventory-file="):
            inventory_args.append(token)
        elif token.startswith("-i") and token != "-i":
            inventory_args.append(token)
        index += 1

    inventory_cli = shutil.which("ansible-inventory")
    if inventory_cli is None:
        raise AuthConfigurationError("ansible-inventory is unavailable; no Ansible command ran.")
    try:
        inventory_result = subprocess.run(
            [inventory_cli, "--vault-password-file", str(key_path), *inventory_args, "--list"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
        inventory_data = json.loads(inventory_result.stdout) if inventory_result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise AuthConfigurationError("The requested Ansible inventory could not be checked; no host command ran.") from exc
    if inventory_result.returncode != 0:
        raise AuthConfigurationError("The requested Ansible inventory could not be checked; no host command ran.")
    inventory_hosts = {
        host
        for name, group in inventory_data.items()
        if name != "_meta" and isinstance(group, dict)
        for host in group.get("hosts", [])
    }
    config_dir = Path(config_dir).expanduser()
    auth_hosts = set(host_var_paths)
    if not auth_hosts or not auth_hosts.issubset(inventory_hosts):
        raise AuthConfigurationError("The caller inventory must already contain every configured Vault host alias.")
    auth_inventory = _validate_auth_inventory(config_dir, DEFAULT_HOSTS)
    try:
        host_vars_check = subprocess.run(
            [inventory_cli, "--vault-password-file", str(key_path), *inventory_args, "--inventory", str(auth_inventory), "--list"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuthConfigurationError("The encrypted per-host credentials could not be loaded; no host command ran.") from exc
    if host_vars_check.returncode != 0:
        raise AuthConfigurationError("The encrypted per-host credentials could not be loaded; no host command ran.")

    executable = shutil.which(executable_name)
    if executable is None:
        raise AuthConfigurationError(f"{executable_name} is unavailable; no host command ran.")
    args = list(ansible_args)
    if args[:1] == ["--"]:
        args = args[1:]
    command = [
        executable,
        "--vault-password-file",
        str(key_path),
        *args,
        "--inventory",
        str(config_dir / INVENTORY_NAME),
        "--extra-vars",
        f"@{vault_path}",
    ]
    return subprocess.run(command, stdin=subprocess.DEVNULL, check=False).returncode


def main(argv: Sequence[str] | None = None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    config_dir = DEFAULT_CONFIG_DIR
    if tokens[:1] == ["--config-dir"]:
        if len(tokens) < 2:
            print("--config-dir requires a path.", file=sys.stderr)
            return 2
        config_dir = Path(tokens[1])
        tokens = tokens[2:]
    elif tokens[:1] and tokens[0].startswith("--config-dir="):
        config_dir = Path(tokens[0].split("=", 1)[1])
        tokens = tokens[1:]

    if not tokens or tokens[0] in {"-h", "--help"}:
        print(__doc__)
        print("Usage: ansible_auth.py [--config-dir PATH] {setup,check,playbook,ad-hoc} [ARGS...]")
        print("\nCommands:\n  setup      enter sudo passwords in a terminal and encrypt them\n  check      validate local Vault files without contacting hosts\n  playbook   run ansible-playbook with vaulted host credentials\n  ad-hoc     run ansible with vaulted host credentials")
        return 0 if tokens else 2
    command, command_args = tokens[0], tokens[1:]
    try:
        if command == "setup":
            if command_args:
                print("setup does not accept additional arguments.", file=sys.stderr)
                return 2
            if not sys.stdin.isatty() or not sys.stdout.isatty() or not sys.stderr.isatty():
                raise AuthConfigurationError("setup requires a real terminal; no credentials were read or written.")
            setup_credentials(config_dir)
            print(f"Encrypted sudo credentials are ready for: {', '.join(DEFAULT_HOSTS)}")
            return 0
        if command == "check":
            if command_args:
                print("check does not accept additional arguments.", file=sys.stderr)
                return 2
            check_credentials(config_dir)
            print("Ansible Vault credentials are ready.")
            return 0
        if command == "playbook":
            return run_ansible(config_dir, "ansible-playbook", command_args)
        if command == "ad-hoc":
            return run_ansible(config_dir, "ansible", command_args)
        print("Use setup, check, playbook, or ad-hoc.", file=sys.stderr)
        return 2
    except AuthConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
