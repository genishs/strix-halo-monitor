# 브랜치 전략 — git-flow

이 저장소는 **git-flow** 모델을 따른다. bash → Python 풀 마이그레이션이 Phase별 다수 PR로 진행되는 시점부터
안정 릴리스(`main`)와 통합 작업(`develop`)을 분리해 관리하기 위해 도입했다.

## Baseline

- 마이그레이션 이전, 현재 bash 도구가 완성된 상태의 3커밋이 `main`의 baseline이다.
  1. `73502d3` feat: Strix Halo 학습/채점 모니터링 대시보드 초기 커밋
  2. `8e74f91` feat: 채점 단계 대략 완료예상시간(ETA) 표시
  3. `5373f01` feat: --english 영어 UI 모드 추가
- `develop`는 이 baseline(`5373f01`)에서 분기했다. 이 시점에 `v0.1.0` 태그로 baseline을 고정했다
  (bash 버전 최종 상태 — Python 마이그레이션 시작 전 되돌아올 지점).

## 브랜치 종류

| 브랜치 | 목적 | 분기 출발점 | 머지 대상 |
|---|---|---|---|
| `main` | 안정/릴리스. 태그(`vX.Y.Z`)가 찍히는 이력만 남는다. **직접 커밋 금지.** | — | `release/*`, `hotfix/*`만 머지됨 |
| `develop` | 통합 기본 브랜치. 일상 작업·PR은 전부 이곳을 향한다. **GitHub 기본 브랜치.** | `main` | `feature/*` 머지됨, `release/*`로 분기 |
| `feature/<name>` | 기능·Phase 단위 작업(예: `feature/py-migration-phase1`) | `develop` | PR로 `develop`에 머지 |
| `release/x.y.z` | 릴리스 준비(버전 고정, changelog, 막판 버그픽스) | `develop` | `main`과 `develop` 양쪽에 머지 + `main`에 태깅 |
| `hotfix/x.y.z` | 긴급 수정. `main`에 이미 릴리스된 버그를 급히 고칠 때 | `main` | `main`과 `develop` 양쪽에 머지 + `main`에 태깅 |

## PR 흐름

```
feature/*  ──PR──▶  develop  ──(릴리스 준비)──▶  release/x.y.z  ──PR──▶  main (태그 vX.Y.Z)
                        ▲                                │                    │
                        └──────────── merge back ─────────┴──── merge back ───┘
                                                                        ▲
                                                    hotfix/x.y.z ──PR──▶ main (태그) ──▶ develop
```

텍스트로 요약:

1. `feature/<name>`을 `develop`에서 분기 → 작업 → PR → `develop`에 머지.
2. 릴리스 시점에 `release/x.y.z`를 `develop`에서 분기 → 버전/문서 정리 → PR로 `main`에 머지 후
   `main`에 `vX.Y.Z` 태그 → 같은 내용을 `develop`에도 머지백.
3. `main`에서 발견된 긴급 버그는 `hotfix/x.y.z`를 `main`에서 분기 → 수정 → PR로 `main`에 머지 후
   패치 버전 태그(`vX.Y.(Z+1)`) → `develop`에도 머지백.

## SemVer 태깅

`vMAJOR.MINOR.PATCH` (예: `v0.1.0`).

- **MAJOR**: 호환성 깨지는 변경 (예: 설정 파일 포맷 변경, CLI 인터페이스 대개편)
- **MINOR**: 하위호환 기능 추가 (예: Python 마이그레이션의 각 Phase 완료)
- **PATCH**: 버그 수정, 문서, 사소한 개선
- `v1.0.0`은 Python 마이그레이션이 완전히 끝나 bash 버전을 완전히 대체하는 시점에 붙이는 것을 권장한다.

## 커밋 컨벤션

[Conventional Commits](https://www.conventionalcommits.org/) 스타일을 따른다.

```
<type>(<scope>): <설명>

[본문 — 선택]

Co-Authored-By: <agent 이름> <noreply@anthropic.com>
```

주요 `type`: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `style`. `scope`는 선택(예: `feat(gtt):`).

## 브랜치 보호 (main)

**권장 보호 규칙** (private repo + 현재 GitHub 플랜 제약으로 `gh`/API로 강제 적용이 불가능하거나 일부만 적용됨 —
아래 "적용 현황" 참고):

- 직접 push 금지 — `release/*`, `hotfix/*` 브랜치의 PR을 통해서만 머지
- PR 필수, 최소 리뷰 1건 승인 후 머지
- 머지 전 상태 체크(CI) 통과 필수 — CI 파이프라인 구축 후 적용
- force-push / 브랜치 삭제 금지

### 적용 현황 (2026-07-18 확인)

`gh api`로 두 가지 방식 모두 시도했으나 이 저장소(private + 무료/기본 플랜)에서는 **둘 다 403으로 거부됨**:

- `PUT /repos/genishs/strix-halo-monitor/branches/main/protection` (classic branch protection)
- `POST /repos/genishs/strix-halo-monitor/rulesets` (신규 Repository Rulesets)

응답: `"Upgrade to GitHub Pro or make this repository public to enable this feature."`

즉 **GitHub 설정으로 강제되는 보호 규칙은 현재 없음.** 위 "권장 보호 규칙"은 팀 합의/수동 준수 사항으로
취급한다 (PR 없이 `main`에 직접 push하지 않기, `release/*`·`hotfix/*` 경유만 허용 등). 저장소를 public으로
전환하거나 GitHub Pro로 업그레이드하면 즉시 재시도해 실제 강제 규칙으로 승격할 것.

## 요약

- 기본 브랜치: `develop`
- `main`은 릴리스 태그만 쌓이는 안정 브랜치
- 지금 당장 새 작업(Python 마이그레이션 Phase들)은 `feature/<phase-name>`을 `develop`에서 분기해 시작한다
