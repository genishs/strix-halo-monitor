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

## Phase 1 — 도메인 모델 + 파서 이관 (최우선)

브랜치: `feature/phase-1-parsers` (off `feature/phase-0-skeleton` → PR to `develop`)

이 Phase의 산출물은 아직 대시보드가 아니라 **검증된 파싱 엔진**이다.

### 한 일
- **`model.py`** — 중심 계약. 순수 dataclass/enum만(로직·I/O 없음):
  `JobType`,`Phase`,`EtaNote`,`Source` enum + `ModelInfo`,`JobState`,`MemoryStats`,`PowerStats`,
  `ClockStats`,`Flags`,`Snapshot`. phase는 **키/enum**(번역 아님, O10).
- **`status_schema.py`** — O4 머신리더블 상태줄 계약(ADR-0002). 예외안전 `emit_status()` +
  소비자 `iter_status_lines`/`parse_last_status`. **stdlib 단일파일**(ML 스크립트 벤더링 가능).
- **`config.py`** — 계층 설정(E). 기본값 + `HALO_*` 환경변수(bash와 100% 하위호환). 라벨맵 외부화(O13,
  `base_label_for`). TOML은 후속 Phase(O2).
- **`jobs/`** — 잡 파싱(C):
  - `base.py`: `UnitRef`, `JobParser` 프로토콜, 레지스트리, `parse_job` 파사드.
  - `_scrape.py`: monitor.sh grep/awk 패턴 **정확 이식**(정규식 fallback) + JSON 값 관용 coercion.
  - `modelinfo.py`: `command` 라인 파싱 → `ModelInfo`. 라벨맵은 Config에서(O13).
  - `eta.py`: phase별 ETA 전략. **구조화 반환**(초 + note 키), 포맷/번역은 렌더러.
  - `train.py`/`score.py`: **JSON 우선 + regex fallback** 파서. monitor.sh phase 로직 정확 이식.
- **테스트 픽스처** `tests/fixtures/logs/`(합성·마스킹) + `unittest` 39케이스:
  modelinfo/train/score/status_schema/eta. **전부 통과**(3.14·3.12).

### 핵심 설계 결정
1. **JSON 우선 · regex fallback (ADR-0002).** JSON 있으면 phase/진행 우선, 없으면 현행 로그 regex.
   지금은 로그에 JSON이 없어 **regex로 동작**(안전). `JobState.source`로 데이터 출처 노출(감시 agent용).
2. **파서별 phase 우선순위를 monitor.sh와 정확히 파리티** — 두 잡의 bash 분기 순서가 다름을 보존:
   - train: eval_save(step≥total & active) → training → first_step → quantizing → finished(무마커·비활성).
     진행 마커가 있으면 유닛이 죽어도 마지막 학습라인 표시(bash quirk)까지 유지.
   - score: **finished 우선**(비활성=종료) → prep(양자화만) → scoring(선형 외삽 ETA).
3. **i18n·시간을 파서에서 제거(O10).** phase=enum, eta=(초, note키). 번역·HH:MM 포맷은 Phase 3 렌더러.
4. **read-only(C2).** 파서·수집기 계약 어디에도 write 경로 없음. systemd는 Phase 2에서 읽기 전용만.

### 인플라이트 파이프라인 안전 (준수 사항)
- **모니터 쪽(JSON 소비 파서 + regex fallback)만 구현.** 현행 로그로 fallback 동작하므로 **지금 안전**.
- **ML 스크립트에 O4 emit 추가는 보류.** 123B 채점·72B 학습 파이프라인 인플라이트 →
  파이프라인 종료 후 또는 별도 브랜치에서 충분 테스트 후, 예외안전 print 한 줄로만 반영(ADR-0002).

### 다음(Phase 2 예정)
- `collectors/`(memory/power/clocks, amdgpu 백엔드) + `loop.py`(rate·RAPL 델타·랩어라운드·신호).
- `jobs/detect.py`(systemd 유닛 감지, read-only) — Phase 1에서는 `UnitRef` 직접 주입으로 파서만 테스트.
- sysfs 픽스처(`tests/fixtures/sysfs/`)로 수집기 결정적 테스트.

---

## Phase 2 — 수집기 + 갱신 루프

브랜치: `feature/phase-2-collectors` (off `develop` → PR to `develop`)

리드(개선생)가 인터페이스·루프를, 개동생이 concrete 수집기·픽스처를 분담.

### 한 일 (리드 — 인터페이스 + 루프)
- **`model.py`**: `RawPower` dataclass 추가(RAPL energy 카운터 pkg/core·max_energy_range·amdgpu 순간전력).
  수집기는 **stateless raw만** 반환하고 watts 변환·델타·랩어라운드는 루프가 소유하도록 계약 분리.
- **`collectors/base.py`**: `Collector` 프로토콜 + `CollectContext(cfg, backend, root)`. **델타상태 없음**(루프 소유),
  주입 `root`로 테스트 결정성 확보.
- **`collectors/backends/base.py`**: `GpuBackend` 프로토콜(`detect/mem_info/power/clocks`). read-only·non-raising·stateless 계약 명시(C2).
- **`loop.py`**: 틱 엔진. GTT rate·RAPL watts(음수 델타=카운터 랩어라운드 → 해당 틱 skip, **bash 파리티**)·
  SIGINT(정상종료)/SIGWINCH(재레이아웃 플래그)·복원력(`_safe`: 한 수집기가 던져도 루프 유지). 클럭·시각 주입으로 `tick()` 순수화 → HW 없이 테스트.
- **`test_loop.py`**: fake 수집기+주입 클럭으로 델타(2샘플)·watts·랩어라운드 skip·복원력·플래그 검증(4케이스).

### 한 일 (개동생 — concrete + 픽스처)
- **`collectors/backends/amdgpu.py`**: `AmdgpuBackend`. card/hwmon을 **번호 아닌 내용(name/glob)으로 매칭**
  (박스 실측 hwmon12=amdgpu — 번호 하드코딩이 실제로 깨짐을 확인). sclk는 **`pp_dpm_sclk` 우선**(박스에 존재·readable),
  rocm-smi 미호출(0회, 안전예산 준수). 파일 부재/권한없음 → 해당 필드 None.
- **`collectors/backends/nvidia.py`**: 스텁(`detect()->False`). 이슈 #7에서 shas가 4060으로 구현.
- **`collectors/{memory,power,clocks}.py`**: MemoryCollector(backend GTT/VRAM + `/proc/meminfo` RAM/swap),
  PowerCollector(RAPL pkg/core energy+max + amdgpu_w, **watts 계산 안 함**), ClockCollector(backend 위임).
- **`backends/__init__.py`**: `select_backend(ctx)` — amdgpu 감지 시 amdgpu, 아니면 nvidia 스텁. import 사이클 회피 위해 ctx는 duck-typed.
- **픽스처**: `tests/fixtures/sysfs/`(가짜 트리, hwmon0 decoy로 번호매칭 회귀 방지), `sysfs_no_rapl/`(우아한 빈값),
  **실로그 마스킹 캡처** `real_score_123b.log`·`real_train_123b.log`(read-only 캡처 → `/home/user`→`/home/USER`,
  경로/adapter 일반화, 호스트명·rocm-smi 블록 제거; 공개 모델명 유지). provenance는 fixtures README에 기록.
- **테스트 28개 추가**: memory/power/clocks/backends 수집기 + 실로그 파서 검증. 기존 43 미변경.

### 리드 리뷰 결과 (개선생)
- **전체 71/71 통과.** C2 read-only 준수(코드에 write/systemd조작/subprocess 없음). 인터페이스 계약 정확 준수, 델타는 전부 루프에만.
- **실로그 마스킹 누출 감사 통과**: `/home/user`·`새 볼륨`·호스트명(`ub26-sgshs`)·이메일·사용자명 커밋본에 없음(grep 확인).
- **라이브 박스 end-to-end 스모크(read-only, 1틱)**: loop+concrete 수집기+amdgpu 백엔드+파서 조립 →
  GTT 48.1/56GiB·`ram_free 2.45GB`(→ **ram_low 플래그 정상 발동**)·sclk 2860Mhz·amdgpu_w 85W·job=scoring 5/7 Mistral-Large 123B.
  RAPL watts는 1틱째 None(2샘플 필요, 설계대로).
- `select_backend`의 duck-typed ctx: import 사이클(`collectors.base`↔`collectors.backends`)의 정당한 회피로 수용. (후속 polish: `TYPE_CHECKING` 가드 주석 정도 — 비차단.)
- `_amdgpu_card_dir` 틱당 3회 glob 호출(detect/mem_info/clocks): 스왑박스에서도 저비용이라 비차단. 필요 시 Phase 3에서 백엔드 조립부 캐시 검토.

### 다음 (Phase 3 — 렌더러 파리티, QA 게이트 대상)
- `ui/`(render/widgets/theme/i18n) — bash 레이아웃 문자단위 파리티(ko/en). `app.py` DI 조립.
- `jobs/detect.py`(systemd 유닛 감지, read-only) 실배선.
- bash 병행 RSS 실측(C1 게이트) + 큐선생(QA) 독립검증 후 머지.

---

<!-- 다음 Phase 기록은 해당 feature 브랜치에서 이 아래에 추가된다. -->
