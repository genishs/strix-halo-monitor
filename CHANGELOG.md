# Changelog

이 프로젝트의 주요 변경사항. 형식은 [Keep a Changelog](https://keepachangelog.com/),
버전은 [SemVer](https://semver.org/)(브랜치·태깅 규칙은 `docs/BRANCHING.md`).

## [Unreleased]

Phase 5(확장) 남은 항목 — nvidia 백엔드(이슈 #5), 스파크라인, alerts(규칙 일반화), TOML 설정,
`--rich`/`--ascii` 렌더 선택, 차분 렌더, ML 스크립트 O4 emit(72B 파이프라인 종료 후), `amdgpu_w`→`gpu_w` 리네임.
로그 `[HH:MM:SS]` 타임스탬프 기반 epoch 산정은 여전히 회피(tz 함정).

## [0.5.1] — 2026-08-30

**온도 위젯 추가** (Phase 7) — 학습·채점 중 GPU/CPU 스로틀링을 판단할 근거가 대시보드에 없었다.
34시간 GPU 캠페인 실측(GPU edge 88°C, CPU Tctl 87°C 정상 운용)을 바탕으로 임계치 기본값을 잡았다.

### Added
- **`collectors/temperature.py`(`TemperatureCollector`)** — `sys/class/hwmon/hwmon*/name` 파일
  내용으로 GPU(`amdgpu`)·CPU(`k10temp`)·NVMe(`nvme`, 드라이브마다 별도 hwmon) 칩을 자동탐지하고,
  칩 안에서는 `temp*_label` 텍스트(`edge`/`Tctl`/`Composite`)로 정확한 센서를 고른다 —
  hwmon 번호도 `tempN` 번호도 부팅마다 바뀔 수 있어 둘 다 식별자로 쓰지 않는다
  (`mounts.py`/`battery.py`와 같은 원칙). **`rocm-smi` 호출 없음** — GPU edge 값은 sysfs
  hwmon 직접 읽기와 완전히 동일한 값(실측 확인, 프로세스 기동 비용 0).
- **`model.TempStat`** — `key`/`label`/`temp_c`/`warn_c`/`crit_c`/`alert`(`ok`/`warn`/`crit`).
  센서가 없는 장비(APU 아님·k10temp 없음·NVMe 없음)에서는 해당 항목이 리스트에서 그냥 빠진다
  (배터리 위젯처럼 `present=False` 자리표시 행 없음 — 온도 센서 구성은 프로세스 수명 내내 고정).
- 🔴 **NVMe 임계값 쓰레기값 필터링** — 이 장비의 한 NVMe 드라이브는 `temp2_max`(sysfs
  device-limit)로 65261°C라는 오버플로 값을 보고한다. 그대로 썼다면 그 드라이브는 아무리
  뜨거워져도 경보가 영원히 안 울렸을 것. `_sane_threshold()`가 0~150°C 범위 밖 값을 버리고
  설정 기본값으로 대체한다(범위 안이면 드라이브 자체 값을 우선 — 기본값보다 더 정확한
  드라이브별 지식이므로).
- **경보 임계치**(2단계, 배터리 위젯과 같은 `ok`/`warn`/`crit` 체계):
  `HALO_TEMP_WARN_C`(기본 95°C)/`HALO_TEMP_CRIT_C`(기본 105°C) — GPU/CPU 공용.
  `HALO_NVME_TEMP_WARN_C`(기본 70°C)/`HALO_NVME_TEMP_CRIT_C`(기본 80°C) — NVMe 전용
  (컨슈머 NVMe는 90°C보다 훨씬 낮은 온도에서 스로틀링을 시작하는 경우가 흔해 GPU/CPU와
  다른 낮은 기준이 필요). 기본값은 이 장비의 34시간 캠페인 실측(GPU 88°C/CPU 87°C 정상
  부하, NVMe 53~64°C 유휴/경부하)에서 **정상 운용 구간을 절대 경보로 잡지 않도록** 여유를
  두고 정했다(GPU/CPU는 실측값보다 약 7~8°C 위, NVMe는 실측값보다 충분히 위 + 일반적인
  컨슈머 드라이브 스로틀 시작점보다 아래).
- 렌더러: 배터리 섹션 바로 뒤, 디스크 섹션 바로 앞에 `온도`/`Temp` 구간 추가(GPU 온도가
  스로틀링 판단에 가장 중요한 지표라 상단 경보 그룹에, NVMe 온도는 디스크 섹션과 붙여서).
  센서가 하나도 없는 장비에서는 섹션 자체가 나타나지 않아(additive) 기존 골든 프레임에
  영향 없음.

### 판단 — 넣지 않은 것
- **acpitz(메인보드)·mt7921(WiFi) 온도는 제외.** 우선순위 낮음(두목 지시) + 화면이 이미
  길어 추가 판단 가치 대비 화면 비용이 크다고 판단.

> 🔖 **버전 번호 재조정 (2026-08-29, 두목 결정).**
> `main` 의 마지막 릴리즈는 **v0.2.1**(2026-07-18)이었고, 그 뒤 아래에 기록된
> **0.3.0 ~ 0.8.0 은 develop 에서만 쓰인 내부 개발 번호로 태그·릴리즈된 적이 없다.**
> 증가 폭이 과했다는 판단에 따라, **그 누적 결과 전체를 `v0.5.0` 하나로 릴리즈**한다.
> 즉 이 항목의 내용 = 아래 [0.8.0] 항목의 내용이며, 앞으로는 **패치 자리(0.5.1, 0.5.2 …)** 를
> 올려가며 완만하게 버전을 매긴다.
> ⚠️ 아래 [0.3.0]~[0.8.0] 항목과 `docs/DEVLOG.md`, 그리고 커밋 메시지의 `(v0.7.0)` 같은 표기는
> **당시 실제로 그렇게 진행된 기록이므로 고치지 않고 그대로 둔다.** 릴리즈 번호와는 별개다.

**배터리·전원 위젯 추가** (내부번호 0.8.0) + **Phase 5 전체**(디스크·네트워크·채점·GPU 위젯,
디스크 자동탐지, 모델명 버그 수정 — 내부번호 0.3.0~0.7.0). 상세는 아래 각 항목 참조.

### Fixed
- **`tests/test_power_collector.py`의 RAPL sysfs 픽스처를 콜론 경로 커밋에서 런타임 생성으로 전환**
  (이슈 #7). `sys/class/powercap/intel-rapl:0`(`:0:0` 코어 도메인 포함) 은 실제 Linux sysfs
  레이아웃을 그대로 반영한 경로지만, NTFS는 `:` 를 대체 데이터 스트림 구분자로 예약해
  `git checkout` 은 물론 **`os.makedirs()` 런타임 생성조차 거부**한다 — 이 장비의
  `/mnt/data`(NTFS 공유 볼륨, `fuseblk`) 체크아웃에서 실측 재현. 커밋된 픽스처 파일 4개가
  조용히 사라지면서 `test_full_fixture_raw_readings`·`test_no_backend_no_amdgpu_watts` 가
  RED 였다. 정적 픽스처 트리(`tests/fixtures/sysfs`, colon-free)를
  `tempfile.TemporaryDirectory()`(시스템 실제 임시 디렉터리 — 체크아웃과 다른 파일시스템)로
  복사한 뒤 그 위에 RAPL 콜론 디렉터리·파일을 매 테스트 클래스 실행 시 새로 써 넣는 방식으로
  변경. 프로덕션 `power.py`의 `intel-rapl:0` 경로 문자열은 실제 Linux 런타임 sysfs 경로이므로
  그대로 유지(변경 없음).

## [0.8.0] — 2026-08-29 *(내부번호 — v0.5.0 으로 릴리즈됨)*

**배터리·전원 위젯 추가** — 충전기 용량 부족으로 밤샘 학습이 새벽에 강제중단됐던 사고
(`gfx1151-4bit-training.md`: 100W 충전기·100W+ 부하 → 배터리 6%에서 05:00 강제종료)의 재발 방지.

### Added
- **`collectors/battery.py`(`BatteryCollector`)** — `power_supply/*/type` 파일로 배터리·충전기
  장치를 이름이 아닌 **타입으로 자동탐지**(`Battery`/`Mains`/`USB`), `mounts.py`의 하드코딩 탐지
  수정과 같은 원칙. `BAT0`/`ADP0`/`ucsi-source-psy-*` 같은 벤더별 이름에 의존하지 않는다.
- **`model.BatteryStat`** — `present`(배터리 유무), `ac_online`(충전기 연결), `status`(원문),
  `capacity_pct`, `discharging`, **`discharge_w`(방전 전력, 가장 중요한 값)**,
  `time_remaining_s`(방전 중일 때만), `alert`(`ok`/`warn`/`crit`).
  🔴 **충전기 자체의 정격 와트수는 sysfs에서 읽지 못한다** (이 장비의 `ucsi-source-psy-*`
  USB-PD 노드는 실제로 전력을 공급 중에도 `online=0`, current/voltage 전부 0을 보고한다).
  그래서 정격을 추정하는 대신 **배터리에서 실제로 빠져나가는 전력(discharge_w)** 을 보고한다 —
  0이면 충전기가 부하를 감당 중, 양수면 충전기가 연결돼 있어도 배터리가 소모되는 중이라는 뜻이고,
  이게 정확히 그 사고의 조기 신호다. `power_now`(µW) 우선, 없으면 `current_now×voltage_now` 대체,
  잔여시간은 `energy_now`(없으면 `charge_now×voltage`) 기반.
- **경보 임계치** `HALO_BATTERY_WARN_PCT`(기본 30) / `HALO_BATTERY_CRIT_PCT`(기본 15).
  `alert_level()`: 잔량이 crit 미만이면 충전 중이라도 무조건 `crit`, **방전 중이면 잔량과
  무관하게 최소 `warn`**(충전기가 연결된 채로 방전이 시작되는 것 자체가 34시간 무인 운전 중엔
  이상신호이므로), 방전이 아니어도 warn 미만이면 `warn`.
- **위젯(`ui/widgets.battery_lines`)** — sclk/유닛 줄 바로 다음(디스크·네트워크 섹션보다 위)에
  배치해 화면을 스쳐 봐도 가장 먼저 눈에 들어오게 했다. 경보 마커는 2단계로 구분:
  `⚠️낮음`(warn, 기존 RAM/디스크 경고와 같은 톤)과 **`🚨위험`(crit, 새 이모지 — 다른 경고와
  섞이지 않게)**. 배터리가 없는 장비(데스크톱/미니PC)에서는 `BatteryCollector.available()`이
  `False`를 반환해 위젯 자체가 조용히 사라진다 — 별도 on/off 스위치 불필요.
- **테스트 +40** — 임계치 판정(`alert_level`) 단독 테스트, 타입 기반 탐지(비표준 이름·USB-PD
  online=0 오판 방지), `power_now`/`current_now×voltage_now`/`charge_now×voltage` 3중 폴백,
  배터리 없음, 충전 중, 방전 중(경고/위험 양쪽), AC 정보 전혀 없는 기기, 렌더 additive 검증
  (배터리 없으면 기존 12줄 프레임 그대로), ko/en 라벨.

### Changed
- `model.Flags`에 `battery_low`(alert가 warn/crit이면 True), `model.Snapshot`에 `battery`
  필드 추가(기본값 `BatteryStat()`이므로 배터리 없는 스냅샷은 기존 그대로 동작).
- `config.Config`에 `battery_warn_pct`/`battery_crit_pct`, `loop.UpdateLoop`에 `battery`
  콜렉터 인자 추가(다른 콜렉터와 동일하게 필수 — 실패해도 `_safe()`로 감싸 tick이 죽지 않음).

### Notes
- C2(무간섭) 유지: `power_supply/*` 아래 몇 개 텍스트 파일만 읽는다. `upower`/`acpi` 서브프로세스
  없음, 폴링 루프 자체도 없음(다른 콜렉터처럼 매 틱 스냅샷 읽기).
- ADR-0002(HALOJSON 상태줄)는 **미변경** — 그건 학습/채점 스크립트가 로그에 emit하는 계약이고
  배터리는 로컬 sysfs 읽기라 별개다. 스키마에 필드를 더하지 않았으므로 갱신 대상 아님.
- 실기(이 장비)에서 충전기 연결·`Full`·100%·방전 0W 프레임 확인. **방전/경보 경로는 이 장비가
  현재 방전 중이 아니라 실기로 재현 불가 — 픽스처 모킹으로만 검증했다.**

## [0.7.0] — 2026-08-04

**디스크 위젯이 마운트된 디스크를 다 못 보여주던 버그 수정** (하드코딩 목록 → 자동탐지).

### Fixed
- **🔴 외장 2개 중 1개만 표시**: 디스크 위젯이 `config.DEFAULT_DISK_MOUNTS`에 **하드코딩된 3개**
  (`/mnt/data`, `/run/media/user/새 볼륨`, `/`)만 봤다. 개수 제한 코드가 있었던 게 아니라 **목록에 없는
  마운트는 존재 자체가 보이지 않았다** — 실제로 마운트된 `/run/media/user/새 볼륨1`(1.9T)이 통째로 누락.
  이제 기본값이 **자동탐지**다: `/proc/mounts`를 읽어 실제 마운트된 파일시스템 전부를 표시한다.
  실측 결과 4개(`/mnt/data` 701G, `새 볼륨` 932G, `새 볼륨1` 1.9T, `/` 210G) 모두 나온다.
- **컬럼 정렬 깨짐**: 사용량 칸이 고정폭(`:>5`)이라 1.9T 드라이브의 4자리 값(`1422.1`)이 칸을 넘쳐
  그 줄부터 막대·퍼센트·여유 칸이 밀렸다. 자릿수를 **프레임마다 계산**해 정렬(라벨 정렬과 같은 방식).
  자동탐지로 TB급 드라이브가 처음 표시되면서 드러난 문제.

### Added
- **마운트 자동탐지(`collectors/mounts.py`)** — `/proc/mounts` 파싱. `\040` 이스케이프를 디코드하므로
  **공백 포함 경로**(`새 볼륨1`)가 정확히 처리된다. 노이즈 제외: `tmpfs`/`devtmpfs`/`squashfs`(snap loop)/
  `overlay` 등 의사 파일시스템, `/boot/efi`, `/proc|/sys|/dev|/snap|/run`(단 `/run/media`는 **유지**).
  네트워크 FS(nfs/cifs/sshfs…)도 제외 — 끊긴 마운트에 `statvfs`가 걸리면 TUI 전체가 멎기 때문.
  같은 블록장치의 bind mount 중복 제거. 라벨은 이동식=볼륨 폴더명(`새 볼륨1`), 고정=경로 그대로.
  표시 순서는 기존과 동일하게 고정 → 이동식 → `/` 마지막.
- **드라이브 착탈 대응** — 탐지 결과를 `HALO_DISK_RESCAN_S`(기본 5초)마다 갱신. 실행 중 외장을 꽂거나
  빼면 재시작 없이 반영된다. 매 틱(~2초) 재탐지를 피하는 캐시일 뿐이라 C2(무간섭) 유지.
- **개수 상한** — `HALO_DISK_MAX`(기본 8). 초과 시 **용량 큰 순**으로 남겨 화면이 무너지지 않게 한다.
  상한 미만이면 표시 순서 그대로. **명시 목록(`HALO_DISK_MOUNTS`)에는 상한을 적용하지 않는다**
  (사용자가 이름을 대서 지정한 것이므로 전부 표시).
- **테스트 +41** — 이스케이프 디코드, 파싱, 제외필터(의사FS·snap·EFI·gvfs·네트워크FS), bind 중복제거,
  라벨·정렬 규칙, 착탈(장착·탈거·캐시 유효기간), 상한, 컬럼 정렬, 하위호환(명시 목록 우선·상한 미적용).

### Changed
- **`Config.disk_mounts` 기본값이 `None`(자동탐지 신호)** — `net_ifaces`와 같은 규약. `()`(빈 튜플)은
  종전대로 위젯 비활성, 비어있지 않은 튜플은 종전대로 그 목록만. **`HALO_DISK_MOUNTS`의 기존 동작
  (`;` 구분 `label=path`, 공백 포함 경로 포함)은 그대로**다.
- `config.DEFAULT_DISK_MOUNTS` 상수 제거(이 상수가 곧 버그였다). 기존 테스트
  `test_default_mounts_present`는 하드코딩 목록을 단언하던 것이라, 같은 보장(`/mnt/data`·`/`가 나온다)을
  실제로 결정하는 계층(탐지)에서 검증하도록 `test_default_is_auto_discovery` + 탐지 테스트로 대체했다.

### Notes
- C2(무간섭) 유지: `statvfs` + `/proc/mounts`(커널 메모리상 텍스트)만 읽는다. `df`·`lsblk` 서브프로세스
  없음 — 비용 문제만이 아니라, 공백 포함 경로를 셸에 태우는 것이 이 프로젝트에서 반복된 함정이라서다.

## [0.6.1] — 2026-07-31

**모델정보 줄 모델명이 "새"로만 나오는 버그 수정** (공백 포함 `--base` 경로 파싱).

### Fixed
- **🔴 모델명 "새"**: 학습/채점 유닛 커맨드의 `--base` 값이 `/run/media/user/새 볼륨/<model>`(공백 포함
  경로)인데, 모델정보 파서가 값을 `\S+`(공백 없는 런)로 잡아 **첫 공백에서 잘려** basename이 `새`가 됐다.
  정답은 마지막 경로요소(예: `mistral-large-2411` → `Mistral-Large 123B`, `mixtral-8x22b-v0.1`).
  경로형 옵션(`--base`·`--adapter`)의 값을 **다음 `" --플래그"`(또는 줄끝)까지 통째로** 취득하도록 수정
  (`modelinfo._PATH_VALUE`). 숫자/식별자 옵션은 단일 토큰 그대로. legacy `monitor.sh`도 `sed`로 동일 수정
  (`--base` 뒤 → ` --` 앞까지). eval 위젯의 `--label` 우선 라벨은 그대로(중복 방지책이었을 뿐, 이제 근본 수정).
- **테스트 +3** — 공백 경로 `--base`(중간·줄끝), 공백 경로 `--adapter`. 기존 파싱 케이스 전부 유지.

### Notes
- 이 파서는 로그의 `command` 줄만 읽는다(읽기전용, C2 무간섭). systemd 접근 불필요.
- 렌더 레이아웃/골든 프레임 영향 없음(모델명 문자열만 정확해짐).

## [0.6.0] — 2026-07-31

**Phase 5 확장(4) — GPU 사용율(utilization %) 표시** (task #24 연장). 기존 지표는 무변경.

### Added
- **GPU 사용율** — `/sys/class/drm/card*/device/gpu_busy_percent`(amdgpu 커널 카운터)를 **GTT를 읽는
  그 카드에서 함께** 읽어 GTT 줄 끝에 `GPU 사용 62%`(EN `GPU busy 62%`)로 표시. 순간값(델타 불요),
  **읽기전용·간섭 0(C2)**. `AmdgpuBackend.mem_info()`가 `MemoryStats.gpu_busy_pct`로 채우고
  `MemoryCollector`가 통과시킴. 커널/카드에 파일이 없으면 `None`(우아한 공백).
- **렌더(`ui/`)** — GTT 줄에 **값이 있을 때만** 사용율을 덧붙임(가산적) → gpu_busy_pct 없는 골든
  픽스처의 **바이트 파리티 프레임 무변경**. i18n 한/영(`GPU 사용`/`GPU busy`).
- **테스트 +4** — 백엔드(사용율 읽기·부재시 None), 메모리 수집기(통과), 렌더(GTT 줄 덧붙임 ko/en·부재시 무변경).
- **legacy `monitor.sh`** — 동일: `gpu_busy_percent`를 읽어 GTT 줄 끝에 값 있을 때만 표시. 수치·포맷 Python과 일치.
- **테스트 픽스처** — `tests/fixtures/sysfs/.../card1/device/gpu_busy_percent`(62) 추가. `sysfs_no_rapl`엔
  일부러 없음(부재 경로 검증).

### Notes
- **sclk**: 클럭은 기존대로 별도 줄(`sclk: NMhz`)에 이미 표시 중이라 중복 없이 그대로 뒀다(요청의 "여유되면 참고").
- 사용율은 순간 카운터라 틱마다 크게 변동한다(정상). GTT rate/watts처럼 델타를 쓰지 않는다.

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
