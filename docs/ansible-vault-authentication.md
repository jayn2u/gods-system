# Ansible Vault authentication

Use `scripts/ansible_auth.py` to store sudo credentials in Ansible Vault and run playbooks or ad-hoc commands without interactive password prompts. The helper is configured for the inventory aliases `vis-lab` and `ubuntu`.

## Prerequisites

Install Python 3 and Ansible Core. The helper was verified with `ansible-core` 2.16.3. Its commands require `ansible-vault`, `ansible-inventory`, and the relevant Ansible runner (`ansible-playbook` or `ansible`) on `PATH`.

The inventory used with the wrapper must contain both configured aliases. For example:

```ini
[managed]
vis-lab ansible_connection=local
ubuntu ansible_connection=ssh ansible_host=ubuntu.example.invalid
```

Replace connection settings with the values for your hosts. The wrapper checks the caller's inventory before adding its encrypted `host_vars` source, so the helper does not add hosts to an existing `all` selection.

## One-time setup

From the repository root, run this in a real terminal:

```sh
python3 scripts/ansible_auth.py setup
```

Enter the local sudo password when prompted for `vis-lab`, then the remote sudo password when prompted for `ubuntu`. The terminal does not echo the characters. Setup generates a random Vault decryption key at `$HOME/.config/ansible/gods-system/vault-password` with mode `0600`, inside a mode `0700` directory. The key itself is plaintext; the account that can read it can decrypt the Vault files. Host sudo passwords are encrypted in Vault files under that directory, and setup creates no plaintext sudo-password temporary file.

Check the local files without contacting hosts:

```sh
python3 scripts/ansible_auth.py check
```

This verifies ownership and file permissions, and confirms the Vault files can be decrypted. It does not confirm that the sudo passwords are still accepted by the hosts.

## Run Ansible

Pass the same inventory and playbook or ad-hoc arguments you would normally use:

```sh
python3 scripts/ansible_auth.py playbook --inventory inventory.ini site.yml
python3 scripts/ansible_auth.py ad-hoc --inventory inventory.ini all -m ping
python3 scripts/ansible_auth.py ad-hoc --inventory inventory.ini all -b -m command -a 'id -u'
```

Use Ansible's ordinary `--limit` option to run against one host. The wrapper rejects interactive password flags such as `--ask-become-pass`, disables stdin for Ansible execution, and exits before host access if credentials are missing, insecure, undecryptable, or do not match the inventory. Run `id -u` after setup as a harmless check that both hosts accept the saved sudo credentials.
