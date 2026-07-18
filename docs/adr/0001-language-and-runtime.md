# ADR-0001 — 언어·런타임: Python 3 (stdlib 우선), 의존성 0 배포

- 상태: Accepted
- 날짜: 2026-07-18
- 관련: DESIGN.md §1, 오픈이슈 O1/O2/O3/O5

## 배경

bash `monitor.sh`(단일 210줄)를 유지보수·확장·테스트가 쉬운 구조로 풀 마이그레이션한다.
언어·런타임 선택이 이후 모든 결정을 좌우한다. 앜선생 설계안(DESIGN §1)을 피샘·두목이 리뷰·승인했고,
본 ADR은 그 결정을 구현 관점에서 확정 기록한다.

## 결정

- **Python 3, stdlib 우선.** 무거운 TUI 프레임워크(`textual`) 금지. `rich`도 기본 비의존 —
  `--rich` 선택 시 lazy import(향후 Phase 5). 기본 렌더러는 stdlib ANSI.
- **런타임 의존성 0.** `pyproject.toml`의 `dependencies = []`. `rich`/`pytest`는 optional-extras.
- **테스트는 stdlib `unittest`** 로 작성 → `python3 -m unittest`로 무설치 실행. pytest도 지원하되 불요.
- **대상 Python: 3.11+** (`tomllib`·최신 dataclass). 박스 실측: 3.12/3.14 확인(O3 해소).
- **배포**: 개발=`pipx`, 박스=`zipapp` 단일 `.pyz`(의존성 0, 시스템 python3 실행) — Phase 4/5에서 구현.

## 근거

1. **C4(팀 유지보수)가 결정적 제약.** 팀은 Python 주력. Go/Rust는 RSS·정적바이너리 이점이 있으나
   팀이 못 고치면 유틸리티로 실패.
2. **C1(경량) 달성 가능.** 상주 프로세스 1개가 sysfs를 직접 읽어 in-process 파싱 → **틱당 fork 0**
   (bash는 틱당 30+ fork/exec). 스왑 박스에 오히려 더 친화적.
3. **테스트 용이성.** 파서=순수 함수, 수집기=주입 루트, 렌더러=Snapshot→문자열. HW 없이 CI 검증.

## 정직한 대안

C4가 없었다면 Go+Bubbletea가 기술적 최적(정적 단일 바이너리·낮은 RSS·의존성 0). 계층화(수집기/렌더러
인터페이스 분리)로 이식 여지는 남겨둔다. 팀 밖 배포가 필요해지면 재검토.

## 영향

- steady RSS 목표 12–18MB. Phase 3에서 bash와 병행 실측(C1 게이트).
- 파서/렌더 i18n 분리(O10): phase는 키/enum 반환, 번역은 렌더러.
