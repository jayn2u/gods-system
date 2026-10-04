# Submodule Pointer Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 세 소스 저장소의 develop 변경만으로 gods-system의 포인터를 검증 후 자동 갱신한다.

**Architecture:** 소스별 push 송신 워크플로와 슈퍼 저장소의 직렬 수신 워크플로를 둔다. Python 표준 라이브러리 기반 갱신기는 세 최신 SHA를 고정하여 실제 체크아웃 검증 후 gitlink만 커밋한다.

**Tech Stack:** GitHub Actions, GitHub App installation tokens, Git, Python 3 표준 라이브러리, unittest.

**Spec:** docs/superpowers/specs/2026-10-04-submodule-pointer-sync-design.md

## Global Constraints

- 봇이 gods-system/develop에 직접 커밋한다. PR 생성, 예약 실행, workflow_dispatch 진입점, 제품 배포는 포함하지 않는다.
- 실행 시점의 최신 develop SHA를 채택하며 모든 소스의 후보를 검증한 뒤 한 번에 반영한다.
- 실패하면 포인터를 유지한다. 다음 소스 변경 이벤트가 재검증 계기다.
- GitHub App 토큰을 역할별 저장소·권한으로 제한한다. 비밀값은 출력하거나 커밋하지 않는다.
- 코드 작성자는 GPT Luna max다. 각 저장소의 전용 브랜치에서만 수정하고 사용자 Git identity를 바꾸지 않는다.
- 현재 전용 브랜치의 문서 변경은 이번 작업 소유다. 사용자 변경과 혼동하거나 제거하지 않는다.

## Review Focus

- 서로 다른 저장소의 대기 이벤트 교체: 매 실행에서 모든 소스를 조회하여 변경을 보존한다.
- 악의적 이벤트 필드: 고정 허용 목록과 형식 검증으로 임의 URL·명령 실행을 막는다.
- 비공개 SSH submodule URL: 실제 워크플로의 HTTPS 토큰 인증 경로로 체크아웃한다.
- push 직전 슈퍼 저장소 변경: 타인의 파일·커밋을 보존하고 제한된 충돌 처리만 수행한다.
- 중첩 서브모듈 접근 실패: 원격 포인터를 전혀 변경하지 않고 실패한다.

### Task 1: 갱신기와 네 저장소 워크플로

**Files:**
- Create: scripts/sync_submodules.py
- Create: tests/test_sync_submodules.py
- Create: .github/workflows/sync-submodules.yml
- Create: .github/workflows/ci.yml (갱신기 테스트 전용; 포인터 갱신 트리거와 별개)
- Create in each of gods-eye, gods-mlops, gods-watching: .github/workflows/notify-superproject.yml
- Create: docs/submodule-sync.md

**Interfaces:**
- `python3 scripts/sync_submodules.py --event-path FILE --repository DIR` consumes repository_dispatch JSON, existing superrepo checkout and role-scoped credentials from the workflow environment.
- Event type: `submodule-develop-updated`; client_payload keys: `repository`, `ref`, `sha`, `run_url`.
- Fixed source map: jayn2u/gods-eye → gods-eye; jayn2u/gods-mlops → gods-mlops; jayn2u/gods-watching → gods-watching.
- Fixed source ref: refs/heads/develop. Payload SHA is diagnostic only.
- Settings: repository variable `SUBMODULE_SYNC_APP_ID`; secret `SUBMODULE_SYNC_APP_PRIVATE_KEY` on four repositories.
- Tests use local bare remotes through internal injected source maps; production CLI accepts no arbitrary source map or URL.

- [ ] Write failing real-Git tests for changed gitlinks, no-op, latest SHA adoption with stale events, multi-source adoption, invalid event rejection, checkout failure with no push, nested checkout failure, external push preserving unrelated files, and bounded conflict failure. Run `python3 -m unittest discover -s tests -v` and record expected failure before implementation.
- [ ] Implement the CLI and validation using subprocess argument arrays, isolated checkout validation, exact staged-path/mode checks, and at most three push attempts against freshly fetched superrepo develop. Do not execute source programs. App bot commit identity is local to each commit command; retain configured human identity.
- [ ] Add sender workflows for develop push only (skip branch deletion), generating JSON without shell interpolation of event fields and issuing dispatch with an App token scoped to gods-system Contents write.
- [ ] Add receiver workflow for repository_dispatch only, fixed concurrency group and cancel-in-progress false. Acquire separate source read and superrepo write tokens; avoid passing the write credential into recursive submodule checkout. Pin third-party action references to verified immutable commits.
- [ ] Add CI tests for the sync code on pull_request and push without introducing another pointer-update entry point. Validate all four automation workflow files with actionlint.
- [ ] Write setup/operation documentation for App permissions, settings, event-driven failure recovery, bot identity and real-run verification. Explicitly distinguish local testing from activation.
- [ ] Run the complete new unittest suite and workflow checks, record outputs, and self-review.

### Task 2: Review and activation readiness

**Files:** documentation and fixes to Task 1 files only.

**Interfaces:** consumes Task 1 implementation, test report and diff; produces reviewed changes and precise activation status.

- [ ] Independent reviewer checks implementation against spec and real runtime paths, especially authentication, concurrency and failure behavior.
- [ ] GPT Luna max fixes any important findings with regression coverage. Re-run affected checks and review fixes.
- [ ] Verify repository status and exact changed files. Do not move superrepo gitlinks to unpublished local submodule commits.
- [ ] If an installed App and key are available, prepare configuration and an activation procedure. External shared-branch integration is separate from local implementation and must honor user delivery instructions.
- [ ] Report implementation verification separately from real source-push → dispatch → bot-commit evidence. If credentials or integration are unavailable, identify the exact remaining step.
