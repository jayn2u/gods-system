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

Preserve production source folders, deployed K3s/storage roots, databases, models, and backups. The two remaining development bind-mount directories were traced to old test containers and local test processes, not the preserved product services. Those obsolete test services were stopped explicitly before archiving and removing their source worktrees.

Cleanup verified: eleven temporary sibling work directories removed after private archive comparison; seven obsolete task8/task10/task11 test containers removed without deleting their Docker volumes; two old local test processes and the temporary Label Studio port-forward stopped. Git branches and shared repository histories remain available. Unrelated CUHK-PEDES/Codex worktrees are not part of this MLOps cleanup.

Private archives are kept outside Git at `/mnt/data/gods-work-archive/`; do not publish their contents. Archives may include sensitive recovery material. Credentials remain encrypted where originally encrypted; private archive access must remain restricted.
