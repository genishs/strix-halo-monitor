# ADR-0002 — 머신리더블 상태줄(HALOJSON) 스키마 (O4 하이브리드)

- 상태: Accepted
- 날짜: 2026-07-18
- 관련: DESIGN.md §2.2(C)·O4, `src/halo_monitor/status_schema.py`

## 배경

bash 파서는 한국어 로그 문구(`Linear 양자화`, `loss(avg8)`, `generated [...]` 등)에 **강결합**돼 있어
로그 포맷이 조금만 바뀌어도 깨진다(DESIGN O4). 이를 견고화하기 위해 학습/채점 스크립트가 매 스텝/태스크마다
**한 줄 JSON 상태**를 로그에 함께 emit하고, 파서는 **JSON 있으면 우선, 없으면 기존 regex fallback**을 쓴다.

> ⚠️ **이 상태줄은 대시보드뿐 아니라 무인 감시 오선생(agent)들도 소비**한다. 따라서 스키마는
> **안정적 외부 계약**으로 취급한다 — 필드 추가는 자유, 파괴적 변경은 `SCHEMA_VERSION` 증가.

## 결정

### 와이어 포맷 (한 줄, 개행 종료)

```
HALOJSON {"v":1,"job":"train","phase":"training","step":18,"total":39,"loss":0.6,"sstep":12.5}
```

- **센티널 `HALOJSON `**: 소비자가 노이즈 많은 ML stdout에서 상태줄만 골라내되(다른 JSON 오탐 방지),
  로그 타임스탬프/레벨 접두가 앞에 붙어도 되도록 라인 내 **검색**으로 찾는다.
- **컴팩트 JSON 객체 1개.** `ensure_ascii=False`(한글 라벨 허용).

### 필드 (v1 — 양노드 공통 확정본, 2026-07-18)

> **컨벤션**: snake_case, 단위 접미사 통일(`_s`=초, `_gb`=기가바이트). `sstep`→**`s_step`** 확정.
> emit 이전 단계라 **버전 bump 없이 v1을 확정**(아직 어떤 ML 스크립트도 emit하지 않음).

| 필드 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `v` | int | ✔ | 스키마 버전(==1). 미래/미지 버전은 소비자가 무시하고 regex fallback |
| `phase` | str | ✔ | Phase **키**(아래 열거값 그대로). train: `quantizing`/`first_step`/`training`/`eval_save`/`finished`. score: `score_prep`/`scoring`/`finished`. `idle` 공통 |
| `ts` | number | ✔(권장) | emitter 벽시계 epoch초 |
| `job` | str |   | `train` \| `score` (없으면 `train` 가정) |
| `label` | str |   | 런 식별용 자유 라벨(모델+구성 등). 감시 agent가 런 구분에 사용 |
| `step`,`total` | int |   | 옵티마 스텝/총 스텝 (training/eval_save) |
| `loss` | number |   | 학습 loss (training) |
| `val_loss` | number\|null |   | 검증 loss (eval_save 후) |
| `s_step` | number |   | 초/스텝 (training) |
| `eta_s` | int |   | emitter 자체 ETA 추정(초). 있으면 소비자가 자체 추정보다 우선 사용 가능 |
| `quant_done`,`quant_total` | int |   | 양자화 진행 (quantizing/score_prep) |
| `gen_done`,`heldout_total` | int |   | heldout 생성/총 (scoring) |
| `last_gen` | str |   | 마지막 생성 태스크 라벨 (scoring) |
| `gpu_gb` | number |   | emitter 자체 보고 GPU 메모리(프레임워크/프로세스 관점). ⚠️ 대시보드가 보여주는 **sysfs GTT와는 다름**. agent용 힌트 |
| `host_avail_gb` | number |   | emitter 자체 보고 host RAM 여유(GB). agent용 힌트 |

소비자는 **누락/추가 키를 관용**하고 malformed 라인에 **절대 크래시하지 않는다**. `gpu_gb`/`host_avail_gb`/
`val_loss`/`label`/`eta_s`는 **선택**이며 대시보드 렌더러는 현재 sysfs 값을 우선한다(이 힌트들은 주로 sysfs 접근이
없는 무인 감시 agent용).

#### phase 값 매핑 (shas 제안 → 공통 확정)

shas 초안의 `train|eval|quant` 3값은 공통 열거값으로 표준화한다: `train`→**`training`**, `eval`→**`eval_save`**,
`quant`→**`quantizing`**. (`job`=`train`과 `phase`=`train`의 의미 충돌을 피하려 phase는 항상 전체 키를 쓴다.)
양노드 스크립트(strix `train_directml.py` / 4060 `train_qlora.py`)는 구현이 달라도 **emit 스키마만 공통**이면 된다.

### Emit 계약 (⚠️ 인플라이트 파이프라인 안전)

`emit_status()`는:
- **절대 예외를 던지지 않는다** (직렬화 실패·broken pipe·닫힌 stdout 모두 삼킴).
- **부분/깨진 라인을 쓰지 않는다** (직렬화 성공 후에만 write).
- **stdlib만 의존, 단일 파일.** 다른 repo의 ML 스크립트가 `status_schema.py` **이 파일 하나만 벤더링**
  (스크립트 옆에 복사)하거나 import해서 쓸 수 있다.

살아있는 학습 런에 이 한 줄을 넣어도 런을 깨뜨릴 수 없어야 한다.

## Phase-1 적용 범위 (지금)

- **소비자(파서)만 구현·머지.** 현행 로그에는 HALOJSON이 없으므로 **regex fallback으로 동작**(안전).
- **ML 스크립트(train_directml.py / eval_hard_tsc.py)에 emit 추가는 보류.** 123B 채점·72B 학습
  파이프라인이 인플라이트. emit 추가는 (a) 파이프라인 종료 후 또는 (b) 별도 브랜치에서 충분히 테스트 후,
  **단순 print 한 줄·예외안전**으로만 반영한다(피샘 안전수칙).

## 파서 우선순위 규칙 (JSON vs regex, systemd 오버레이)

- **train**: JSON 있으면 phase·진행 JSON 우선. 없으면 regex(monitor.sh if/elif 정확 이식 — 진행 마커가
  있으면 유닛이 죽어도 마지막 학습라인 표시하는 quirk까지 파리티). finished 강제 오버레이 없음(유닛 상태는
  `unit_active` 필드로 항상 노출).
- **score**: monitor.sh처럼 **finished 검사 우선**(비활성 유닛=종료, 마커 있어도) → 이후 JSON/regex로
  prep/scoring 구분. JSON+비활성이면 provenance=`mixed`.
- `JobState.source`(`json`/`regex`/`mixed`/`none`)로 **감시 agent가 데이터 출처를 알 수 있다.**

## 대안·기각

- **JSON 안에 마커 키(`{"halo":1,...}`)** 만 두고 센티널 없음 → ML 프레임워크가 뱉는 다른 JSON과 오탐 위험.
  센티널 접두 채택.
- **stdout 대신 별도 상태파일 write** → C2(read-only)·스왑 박스 I/O 부담. 로그 stdout에 실어보내는 방식 채택.
