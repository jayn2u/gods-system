# MLOps PR checkpoint and worktree cleanup

The integrated work is paused at actual human review, not declared complete.

## Related draft pull requests

- `jayn2u/gods-mlops#2`: accumulated MLOps implementation and verified deployment fixes.
- `jayn2u/gods-watching#18`: opt-in asynchronous candidate export.

The parent repository pins their exact source commits for review; merge dependent child PRs before this parent checkpoint. No merge is requested.

## Acceptance boundary

Verified: new K3s/GPU infrastructure, fresh resource observations, authenticated video-loop capture, four durable sample receipts, four Label Studio tasks with matching media hashes. Four targeted deployment regression tests passed at this checkpoint.

Incomplete: actual human bbox/caption annotation, immutable dataset publication from those samples, corresponding RT-DETR/CLIP training/evaluation, full UI verification, repeated reclaim/reconnect. Browser login and bucket-hook initialization remain unresolved. Source-use approval does not satisfy human annotation.

## Cleanup boundary

Remove inactive development worktrees only after verified private archives preserve unique source changes, operational receipts, captured frames, and ignored configuration. Keep source branches and common Git histories. Regenerable `.venv` and caches need not be archived.

Preserve production source folders, deployed K3s/storage roots, databases, models, backups, and currently referenced bind mounts. A directory used by an active container is not a safe duplicate. Do not stop or delete those containers merely to satisfy a folder cleanup request without resolving their runtime/data scope.

Private archives are kept outside Git at `/mnt/data/gods-work-archive/`; do not publish their contents. Archives may include sensitive recovery material. Credentials remain encrypted where originally encrypted; private archive access must remain restricted.
