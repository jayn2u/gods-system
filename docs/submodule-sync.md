# Submodule pointer synchronization

`gods-system` adopts specific commits from `gods-eye`, `gods-mlops`, and
`gods-watching`. A pointer update changes those adopted versions; it does not
deploy a product.

When one of the source repositories receives a push to `develop`, its
`notify-superproject` workflow sends a `submodule-develop-updated`
`repository_dispatch` event to `gods-system`. The receiver reads the current
`develop` head of all three sources when it starts. The event SHA is diagnostic
only, so a delayed event cannot roll a pointer back to an older commit. One run
validates all three candidate checkouts and adopts all changed pointers in one
commit. It does not wait for source CI or run source programs.

## GitHub App setup

Install one GitHub App on all four repositories. Give the App the repository
Contents permission required to send repository dispatch events. Do not grant
Actions, workflow, or administrator permissions. At runtime, the workflows
request narrower installation tokens:

| Use | Repositories | Token permission |
| --- | --- | --- |
| Source notification | `gods-system` | Contents: write |
| Candidate and nested-submodule checkout | `gods-eye`, `gods-mlops`, `gods-watching` | Contents: read |
| Pointer commit and push | `gods-system` | Contents: write |

Configure these Actions values on each of the four repositories:

- Repository variable `SUBMODULE_SYNC_APP_ID`
- Repository secret `SUBMODULE_SYNC_APP_PRIVATE_KEY`

The App must be installed on the repositories named by each token request.
Keep the private key in the Actions secret and do not use a personal access
token. The receiver checkout disables persisted credentials. Its source-checkout
process receives only the read token, and the superproject push process receives
only the write token. GitHub SSH submodule URLs are rewritten to HTTPS; the
authorization headers are scoped to the fixed repository URLs. File protocol
submodules are disabled in the production Git commands.

The App settings and Actions values are activation requirements. Passing the
local test suite alone does not activate synchronization.

## Event and update behavior

The receiver accepts only event type `submodule-develop-updated` and the fixed
mapping below:

| Event repository | Superproject path | Source ref |
| --- | --- | --- |
| `jayn2u/gods-eye` | `gods-eye` | `refs/heads/develop` |
| `jayn2u/gods-mlops` | `gods-mlops` | `refs/heads/develop` |
| `jayn2u/gods-watching` | `gods-watching` | `refs/heads/develop` |

The payload contains `repository`, `ref`, `sha`, and `run_url`. The receiver
validates their shape, but it never uses payload values as a Git URL, checkout
path, shell command, or selected commit. Source and superproject URLs and paths
come from the fixed map in `scripts/sync_submodules.py`.

The receiver serializes runs without cancelling an in-progress run. Before it
pushes, it fetches the current `gods-system/develop` and builds a temporary
worktree from that commit. Only staged entries that are gitlinks for the fixed
paths and validated candidate SHAs can be committed. If another push advances
`develop`, the receiver fetches again and reapplies the pointer changes, up to
three attempts. It never force-pushes. If the pointers already match, it exits
successfully without creating a commit.

## Failure handling

Any source lookup, candidate checkout, nested-submodule checkout, event, or
configuration failure stops the run before the pointer commit. A failed
superproject push also leaves the candidate pointers unapplied. Git diagnostics
are summarized without printing credential-bearing URLs or raw Git output.

After correcting the cause, the next source push to `develop` triggers a fresh
run that reads the then-current heads of all three sources. The workflow does
not schedule an automatic retry, provide a `workflow_dispatch` entry point, or
create a pull request. If `gods-system/develop` keeps changing during all three
push attempts, the run fails and a later source push can try again.

## Local verification and activation evidence

The sync tests create temporary local bare repositories and exercise real Git
fetches, checkouts, gitlink commits, and push races:

```bash
python3 -m unittest discover -s tests -v
```

These tests verify the local implementation. Activation requires the workflows
to be present on the four repositories and the App, variable, and secret setup
to be complete. To verify the live path, make a normal source `develop` change
and inspect both workflow runs. Confirm that the source notification succeeded,
the receiver completed, and its superproject commit contains exactly the
validated gitlink changes. Compare each pointer with the source `develop` head
used by the receiver run; a successful local test is not evidence that the
cross-repository event or App credentials work.
