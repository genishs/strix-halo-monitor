# 개발 이력 (DEVLOG)

`strix-halo-monitor` bash → Python 풀 마이그레이션의 Phase별 진행·결정·근거 상세 기록.
정본 설계는 [`DESIGN.md`](../DESIGN.md), 브랜치 규칙은 [`docs/BRANCHING.md`](BRANCHING.md),
주요 설계결정은 [`docs/adr/`](adr/).

리드: 개선생(dev-lead) · 승인: 피샘(PM)/두목 · 설계: 앜선생(system-architect) · 형상관리: 깃선생(scm)

---

## Phase 0 — 레포 스켈레톤 (동작 변화 0)

브랜치: `feature/phase-0-skeleton` (off `develop`)

### 한 일
- `pyproject.toml` 신설: 패키징(`src/` 레이아웃), 콘솔스크립트 `halo-monitor`, **런타임 의존성 0**,
  optional-extras(`rich`, `dev=[pytest]`), pytest 설정.
- 패키지 스켈레톤 `src/halo_monitor/`: `__init__`, `__main__`(마이그레이션 상태 안내 스텁),
  하위 패키지 placeholder(`collectors/`,`collectors/backends/`,`state/`,`ui/`), `py.typed`.
- `monitor.sh` → `legacy/monitor.sh` 이동(git rename). Phase 3 파리티 확인 전까지 **일상 도구·안전망**.
- `.gitignore`에 Python 항목 추가 + **테스트 픽스처 로그(`tests/fixtures/logs/*.log`)는 추적**하도록 예외.
- README에 실행경로 갱신(현재 도구=`legacy/monitor.sh`, Python은 진행중) — 한/영 유지.
- 문서 인프라: `docs/DEVLOG.md`(이 문서), `CHANGELOG.md`, `docs/adr/0001`(언어·런타임 확정).

### 결정·근거
- **테스트를 stdlib `unittest`로** 작성 → 박스/CI에 pytest 미설치여도 `python3 -m unittest`로 무설치 실행.
  의존성 0 배포 철학(ADR-0001)과 정합. pytest는 있으면 동일 테스트를 그대로 수집.
- **박스 Python 실측 3.12/3.14** 확인 → `requires-python = ">=3.11"` 확정(O3 해소, `tomllib` 사용 가능).
- 동작 변화 0: 이 Phase는 순수 구조. bash 도구는 경로만 `legacy/`로 바뀜.

### 검증
- `python -m halo_monitor` / `--version` 정상 동작(3.14·3.12). 패키지 import 정상.

---

<!-- 다음 Phase 기록은 해당 feature 브랜치에서 이 아래에 추가된다. -->
