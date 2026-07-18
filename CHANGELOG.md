# Changelog

이 프로젝트의 주요 변경사항. 형식은 [Keep a Changelog](https://keepachangelog.com/),
버전은 [SemVer](https://semver.org/)(브랜치·태깅 규칙은 `docs/BRANCHING.md`).

## [Unreleased]

Phase 5(확장) 예정 — nvidia 백엔드(이슈 #5), 스파크라인, alerts, TOML 설정, `--rich`/`--ascii` 렌더 선택,
차분 렌더, ML 스크립트 O4 emit(72B 파이프라인 종료 후), `amdgpu_w`→`gpu_w` 리네임. 아직 커밋된 변경 없음.

## [0.2.1] — 2026-07-18

### Fixed
- **버전 메타데이터 불일치**: `v0.2.0` 릴리스 시 `pyproject.toml`의 `version`은 `0.2.0`으로 올렸지만
  `src/halo_monitor/__init__.py`의 `__version__`이 `0.1.0.dev0`으로 하드코딩된 채 남아있어
  `halo-monitor --version`/`.pyz --version`이 잘못된 버전을 출력했다. 두 값을 `0.2.1`로 동기화.
  기능 변경 없음 — 순수 메타데이터 수정.

## [0.2.0] — 2026-07-18

**첫 정식 Python 릴리스 — bash → Python 풀 마이그레이션 완료(Phase 0~4 컷오버).** `legacy/monitor.sh`는
참조 baseline/fallback으로 보존되지만 기본 도구는 이제 `halo_monitor` Python 패키지다.

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
- **Phase 4 — 컷오버**: Python 기본 승격. `scripts/build-pyz.sh`+`make pyz`로 의존성 0 단일파일
  `halo-monitor.pyz`(stdlib zipapp) 빌드. `Makefile`(test/pyz/run/clean). README 한/영 Python-primary 갱신.
- **O4 스키마 v1 공통 확정**(양노드): `s_step` 통일 + 선택필드(`label`/`val_loss`/`eta_s`/`gpu_gb`/`host_avail_gb`).
  ADR-0002 갱신, 소비측 `s_step`+`sstep` alias. (이슈 #5에 nvidia 명세 + 공통 스키마.)
- 문서: `docs/DEVLOG.md`, `docs/adr/0001`(언어·런타임), `docs/adr/0002`(상태줄 스키마).

### Changed
- `monitor.sh` → `legacy/monitor.sh` (경로 이동, 동작 동일). Python판이 기본 도구로 승격, bash는 fallback.

### Fixed
- **loss 추출 파리티 예외(의도적 버그수정)**: bash `legacy/monitor.sh`는 `loss(avg8)`에서 `grep`가 `avg8`의
  `8`까지 주워 loss가 두 줄(`8`+실제값)로 깨져 박스 레이아웃을 무너뜨린다. Python판은 캡처그룹으로 실제 값만
  추출해 **올바르다**. 이 한 케이스는 "바이트 단위 파리티"의 의도적 예외이며 legacy(참조 baseline)는 원본 보존을
  위해 고치지 않는다.

### Notes
- ML 스크립트(`train_directml.py`/`eval_hard_tsc.py`)의 O4 상태줄 **emit은 아직 미적용** —
  인플라이트 파이프라인 안전상 보류(ADR-0002). 파서는 현행 로그에 대해 regex fallback으로 동작.

## [0.1.0] — 2026-07-18

Python 마이그레이션 이전 bash 버전의 최종 baseline(`main` 태그 `v0.1.0`).

### Added
- `monitor.sh`: Strix Halo(gfx1151) 학습/채점 통합 모니터링 대시보드(GTT/VRAM/RAM/swap, 전력 3분할,
  진행/ETA/모델정보). 채점 ETA. `--english` 영어 UI.
