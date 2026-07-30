# Changelog

이 프로젝트의 주요 변경사항. 형식은 [Keep a Changelog](https://keepachangelog.com/),
버전은 [SemVer](https://semver.org/)(브랜치·태깅 규칙은 `docs/BRANCHING.md`).

## [Unreleased]

Phase 5(확장) 남은 항목 — nvidia 백엔드(이슈 #5), 스파크라인, alerts(규칙 일반화), TOML 설정,
`--rich`/`--ascii` 렌더 선택, 차분 렌더, ML 스크립트 O4 emit(72B 파이프라인 종료 후), `amdgpu_w`→`gpu_w` 리네임.
공백 포함 `--base` 경로 basename 잘림(예: `/run/media/user/새 볼륨/...` → `새`)은 별건으로 남김
(eval는 `--label` 우선 표시로 우회). 로그 `[HH:MM:SS]` 타임스탬프 기반 epoch 산정은 여전히 회피(tz 함정).

## [0.5.0] — 2026-07-31

**Phase 5 확장(3) — 채점(eval/grading) 진행 표시 + 라우팅 버그 수정** (task #24 연장). 기존 지표는 무변경.

### Fixed
- **🔴 채점 유닛 오라우팅**: `ScoreParser.matches()`가 유닛명에 `score`만 확인 → 현재 도는
  `gpujob-grade141b-*`(개조된 `eval_hard_tsc.py`)가 **학습 파서로 오라우팅**돼 `🔧 양자화`에 멈춘 채
  채점 진행이 **안 보였다**. 매칭을 `score`/`grade`/`eval` 별칭 전체로 확장해 해결. legacy `monitor.sh`의
  `case *score*` 도 `*score*|*grade*|*eval*` 로 동일 수정.

### Added
- **eval 상세 스크레이프(`_scrape.eval_progress`)** — 현재/최근 태스크명, 누적 생성 토큰(`new=` 합),
  컴파일·채점 단계(`running tsc`), 최종 결과(`CLEAN n/m`, `SCORE g/max = pct%`, `saved →`)를 로그에서 추출.
  기존 `generated [` 카운팅(구·신 포맷 공통)은 그대로. **로그 읽기 전용 → 도는 채점·다운로드 무간섭(C2)**.
- **관측 기반 처리량·ETA(`loop.py`)** — eval 로그엔 per-line 타임스탬프가 없어 tok/s·ETA를 **루프가 틱 간
  관측**(GTT rate·watts·net rate와 동일 소유권). throughput = 누적토큰/관측 생성경과(**0태스크에서 관측
  시작했을 때만** 표시 — 못 본 구간을 나눠 과대추정하지 않도록, 아니면 `—`). ETA = 관측 생성경과 × 남은/완료
  태스크(생성단계 스코프 — 유닛 전체 경과가 아니라 88분 양자화를 제외). "관측/rough" 또는 "산정 대기"로 표기.
  관측 ETA는 main ETA 라인에도 반영해 위젯과 일치.
- **모델(`model.py`)** — `EvalProgress` dataclass + `EvalPhase`(generating/compiling/finished) + `Snapshot.eval`.
  `JobState`에 `cur_task`·`gen_tokens`·`eval_compiling`·`eval_score/max/pct/clean`, `ModelInfo`에 `eval_label`(`--label`).
- **렌더(`ui/`)** — `widgets.eval_lines` + `render` **가산적** 평가 섹션(`Snapshot.eval` 있을 때만; 학습·골든
  프레임 무영향, 디스크·네트워크 뒤). **표준 ML 평가 용어**: `task N/7`, `tok/s`, `ETA (관측/observed)`, `score`.
  i18n 한/영. `smodel`은 `--label`(eval 실행명) 우선 → base_label 잘림("새") 회피. main 진행줄 `최근:`도 태스크명 사용.
- **테스트 +15** — `test_eval_widget.py`(파서 상세·라우팅·루프 관측 tok/s·ETA·from-zero 가드·유닛변경 리셋·
  finished·가산 렌더 ko/en), `test_score_parser.py`에 grade/eval 라우팅 회귀.
- **legacy `monitor.sh`** — 라우팅 수정 + `--label` 우선 표시 + 관측 tok/s(0태스크 관측 가드) + `task N/7`·현재
  태스크명 + 종료 시 최종 SCORE. 로그 읽기만.

### Notes
- **채점=평가=eval 동일 잡**: 유닛명이 `score`→`grade`로 표류했을 뿐 `eval_hard_tsc.py` 동일. 배치 디코드
  (`--batch-size`)로 태스크가 버킷 단위로 완료돼 `task N/7`은 버킷 크기만큼 점프한다(버스티). 그래서 tok/s는
  순간 델타가 아닌 **관측 평균**으로 계산(정직·평활).

## [0.4.0] — 2026-07-30

**Phase 5 확장(2) — 네트워크 처리량(다운로드/업로드 속도) 위젯 추가** (task #24 연장). 기존 지표는 무변경.

### Added
- **네트워크 수집기 `collectors/network.py`** — 활성 인터페이스별 RX/TX 바이트 카운터를 수집.
  **`/sys/class/net/<iface>/statistics/{rx,tx}_bytes` 커널 카운터만 읽기** — 패킷 캡처·`tcpdump`·소켓·외부호출
  전무. 틱당 네트워크 I/O ≈ 0이라 도는 학습/채점이나 대용량 모델 다운로드의 링크와 경합하지 않는다(**C2 불변식**).
  인터페이스 부재/개명/권한 시 예외 없이 `present=False`(사용불가)로 우아 강등. 인터페이스 해석(자동감지)도
  주입된 루트의 sysfs/procfs만 읽어 테스트 결정적.
- **인터페이스 자동감지** — 미지정 시 기본경로(default route) 인터페이스를 `/proc/net/route`에서 감지
  (`net_auto="default"`, 없으면 모든 비-loopback으로 폴백), `net_auto="all"`이면 모든 비-loopback.
- **속도·세션 누적(`loop.py`)** — 속도 = 카운터 델타 / 경과시간. **직전 카운터·최초 카운터를 루프가 상태로 보관**
  (GTT rate·RAPL watts와 동일 소유권 모델). 카운터 리셋(음수 델타)은 그 틱 속도 `None`(RAPL 랩어라운드와 동일 처리).
  세션 누적 RX/TX = 모니터 시작 이후 증가분.
- **설정(`config.py`)** — `NetTarget`(이름+라벨) + `net_ifaces`(None=자동, `()`=끔, 명시=지정) + `net_auto`.
  환경변수 `HALO_NET_IFACES`(`라벨=이름;...`, 빈 값=끔)·`HALO_NET_AUTO`(`default`|`all`). 기존 `HALO_*` 하위호환 유지.
- **모델(`model.py`)** — `RawNetIface`(raw 카운터, stateless) + `NetStat`(파생: 속도·세션누적) + `Snapshot.net`.
- **렌더(`ui/`)** — `widgets.net_lines`(라벨 열 표시폭 정렬, `↓`/`↑` 화살표) + `render`에 네트워크 섹션(구분선 +
  인터페이스별 줄). **가산적**: `Snapshot.net`이 비면 섹션 미출력 → 기존 12줄 골든 프레임(바이트 파리티)·디스크
  블록 그대로 통과. 디스크 블록 **뒤에** 배치. i18n 한/영(`네트워크`/`Network`, `누적`/`total`, `사용불가`/`unavailable`).
- **테스트 +25** — `test_network_collector.py`(임시 sysfs/procfs 트리로 명시·자동감지(기본경로/all)·카운터읽기·부재·
  설정파싱), `test_network_render.py`(줄 포맷·가산성·디스크 뒤 배치·부재·정렬), `test_loop.py`에 네트워크
  델타(2샘플 필요·카운터리셋 skip·세션누적·부재통과·수집기 예외 복원력).
- **legacy `monitor.sh`** — 동일 네트워크 섹션 추가(`/sys/class/net/*/statistics` 카운터 델타). 수치·포맷 Python과 일치.

### Notes
- **Python↔bash 미세차(의도, 디스크와 동일)**: 라벨 정렬을 Python은 **표시폭**(CJK 2칸), bash는 문자수로 패딩.
  인터페이스명은 통상 ASCII라 실무상 동일하게 정렬된다. 수치·화살표·레이아웃 골격은 동일.
- 자동감지는 `/proc/net/route`(기본경로)·`/sys/class/net`(비-loopback 목록) 전제 — 대상 박스는 Linux.

## [0.3.0] — 2026-07-30

**Phase 5 확장(1) — 디스크 사용율·여유공간·부족경고 위젯 추가** (task #24). 기존 지표는 무변경.

### Added
- **디스크 수집기 `collectors/disk.py`** — 대상 마운트별 사용율%·여유공간(GiB)·총량을 수집.
  **`os.statvfs()` 만 사용**(커널이 캐시하는 여유블록 카운터) — `du`·디렉토리 재귀·파일읽기 전무.
  틱당 디스크 I/O 사실상 0이라 도는 학습/채점의 스토리지 대역과 경합하지 않는다(**C2 불변식**).
  마운트 미착탈/부재 시 예외 없이 `present=False`(사용불가)로 우아 강등. `os.statvfs`는 주입 가능(테스트).
- **경고 임계** — 여유공간이 `disk_warn_free_gb`(기본 10GiB) 미만 **또는** `disk_warn_free_pct`(기본 5%)
  미만이면 `⚠️위험`/`⚠️LOW` 마커. 순수함수 `disk.is_low()`로 분리해 단위테스트.
- **설정(`config.py`)** — `DiskTarget`(경로+라벨) + `disk_mounts`(기본: `/mnt/data`·외장모델
  `/run/media/user/새 볼륨`·`/`) + 두 임계. 환경변수 `HALO_DISK_MOUNTS`(`라벨=경로;...`, 경로 공백 허용,
  빈 값=끔)·`HALO_DISK_WARN_GB`·`HALO_DISK_WARN_PCT`. 기존 `HALO_*` 하위호환 유지.
- **모델(`model.py`)** — `DiskStat` dataclass + `Snapshot.disks` + `Flags.disk_low`(any 마운트 경고).
- **렌더(`ui/`)** — `widgets.disk_lines`(라벨 열을 **표시폭 기준** 정렬, CJK 2칸 처리) + `render`에
  디스크 섹션(구분선+마운트별 줄). **가산적**: `Snapshot.disks`가 비면 섹션 미출력이라 기존 12줄
  골든 프레임(바이트 파리티)은 그대로 통과. i18n 한/영(`디스크`/`Disk`, `여유`/`free`, `사용불가`/`unavailable`).
- **테스트 +25** — `test_disk_collector.py`(statvfs 모킹으로 사용율·여유·경고 임계·부재·설정파싱),
  `test_disk_render.py`(줄 포맷·경고마커·부재표시·가산성·열 정렬), `test_loop.py`에 disk 통과·플래그·복원력.
- **legacy `monitor.sh`** — 동일 디스크 섹션 추가(`df -B1`=statvfs, du·재귀 없음). 수치는 Python과 동일.

### Notes
- **Python↔bash 미세차(의도)**: 라벨 정렬을 Python은 **표시폭**(CJK 2칸), bash는 문자수로 패딩 →
  한글 라벨이 섞이면 콜론 정렬이 bash에서 약간 어긋난다. 수치·경고·레이아웃 골격은 동일. (DEVLOG Phase 5 참조)
- `df --output`은 GNU coreutils 전제(기존 `date -d`/`free -m`와 동일 전제). 대상 박스는 Linux.

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
