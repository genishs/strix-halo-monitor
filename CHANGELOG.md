# Changelog

이 프로젝트의 주요 변경사항. 형식은 [Keep a Changelog](https://keepachangelog.com/),
버전은 [SemVer](https://semver.org/)(브랜치·태깅 규칙은 `docs/BRANCHING.md`).

## [Unreleased]

Python 풀 마이그레이션 진행 중. bash 도구는 `legacy/monitor.sh`로 보존(Phase 3 파리티 전까지 안전망).

### Added
- **Phase 0 — 레포 스켈레톤**: `pyproject.toml`(의존성 0, 콘솔스크립트 `halo-monitor`), `src/halo_monitor/`
  패키지 스켈레톤, `python -m halo_monitor` 진입점 스텁, `.gitignore` Python 항목.
- **Phase 1 — 도메인 모델 + 파서**:
  - `model.py`: 중심 계약 dataclass/enum(`Snapshot`,`JobState`,`ModelInfo`,`Phase`,`EtaNote` 등).
  - `status_schema.py`: 머신리더블 상태줄 계약(HALOJSON, ADR-0002) — 예외안전 emit + 소비 파서.
  - `config.py`: 계층 설정 + `HALO_*` 환경변수(bash 하위호환) + 라벨맵 외부화.
  - `jobs/`: train/score 파서(**JSON 우선 + regex fallback**), modelinfo, eta, 레지스트리.
  - `unittest` 39케이스 + `tests/fixtures/logs/` 픽스처.
- **Phase 2 — 수집기 + 갱신 루프**:
  - `model.py`: `RawPower`(RAPL 카운터·amdgpu 순간전력 raw) 추가.
  - `collectors/`: `Collector`/`GpuBackend` 프로토콜, `amdgpu` 백엔드(번호 아닌 내용매칭, `pp_dpm_sclk`),
    `nvidia` 스텁, memory/power/clocks 수집기(stateless raw), `select_backend`.
  - `loop.py`: 틱 엔진 — GTT rate·RAPL watts·랩어라운드 skip(bash 파리티)·SIGINT/SIGWINCH·복원력. DI로 테스트가능.
  - sysfs 가짜트리 + 마스킹 실로그 픽스처 + 수집기/실로그 테스트 28케이스(누적 71).
- **Phase 3 — 렌더러 파리티 + systemd 감지 + app 조립** (⚠️ 큐선생 QA 게이트 통과 후 머지):
  - `ui/`: `render`/`widgets`/`theme`/`i18n` — `legacy/monitor.sh` 출력과 **바이트 단위 파리티**(ko/en).
  - `jobs/detect.py`: systemd `--user` 유닛 감지(read-only 하드게이트 — 화이트리스트 밖 동사 `ValueError`).
  - `app.py`/`__main__`: DI 조립. `python -m halo_monitor`가 실제 대시보드 실행(`--english`/`-e`).
  - `JobState`에 `unit_name`·`loss_disp`/`sstep_disp`(로그 원문 보존, 렌더 파리티) 추가.
  - 골든 렌더 12 + detect 16 테스트(누적 99). 라이브 bash↔py 파리티·C1 RSS(~15.7MB) 실측 확인.
- 문서: `docs/DEVLOG.md`, `docs/adr/0001`(언어·런타임), `docs/adr/0002`(상태줄 스키마).

### Changed
- `monitor.sh` → `legacy/monitor.sh` (경로 이동, 동작 동일). README 실행경로 갱신.

### Notes
- ML 스크립트(`train_directml.py`/`eval_hard_tsc.py`)의 O4 상태줄 **emit은 아직 미적용** —
  인플라이트 파이프라인 안전상 보류(ADR-0002). 파서는 현행 로그에 대해 regex fallback으로 동작.

## [0.1.0] — 2026-07-18

Python 마이그레이션 이전 bash 버전의 최종 baseline(`main` 태그 `v0.1.0`).

### Added
- `monitor.sh`: Strix Halo(gfx1151) 학습/채점 통합 모니터링 대시보드(GTT/VRAM/RAM/swap, 전력 3분할,
  진행/ETA/모델정보). 채점 ETA. `--english` 영어 UI.
