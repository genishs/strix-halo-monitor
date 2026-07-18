# Changelog

이 프로젝트의 주요 변경사항. 형식은 [Keep a Changelog](https://keepachangelog.com/),
버전은 [SemVer](https://semver.org/)(브랜치·태깅 규칙은 `docs/BRANCHING.md`).

## [Unreleased]

Python 풀 마이그레이션 진행 중. bash 도구는 `legacy/monitor.sh`로 보존(Phase 3 파리티 전까지 안전망).

### Added
- **Phase 0 — 레포 스켈레톤**: `pyproject.toml`(의존성 0, 콘솔스크립트 `halo-monitor`), `src/halo_monitor/`
  패키지 스켈레톤, `python -m halo_monitor` 진입점 스텁, `.gitignore` Python 항목.
- 문서: `docs/DEVLOG.md`, `docs/adr/0001`(언어·런타임).

### Changed
- `monitor.sh` → `legacy/monitor.sh` (경로 이동, 동작 동일). README 실행경로 갱신.

## [0.1.0] — 2026-07-18

Python 마이그레이션 이전 bash 버전의 최종 baseline(`main` 태그 `v0.1.0`).

### Added
- `monitor.sh`: Strix Halo(gfx1151) 학습/채점 통합 모니터링 대시보드(GTT/VRAM/RAM/swap, 전력 3분할,
  진행/ETA/모델정보). 채점 ETA. `--english` 영어 UI.
