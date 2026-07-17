# strix-halo-monitor

**AMD Strix Halo (gfx1151, 통합메모리 APU)에서 LLM 학습·채점을 실시간으로 보는 터미널 대시보드.**

`nvtop`은 전용 VRAM만 본다. 그런데 Strix Halo 같은 통합메모리(UMA) APU에서는 모델이 실제로 점유하는 메모리가
**GTT(GPU에 매핑된 시스템 메모리)** 쪽에 잡힌다 — 즉 `nvtop`을 아무리 들여다봐도 "지금 모델이 메모리를 얼마나
쓰고 있는지"는 보이지 않는다. `monitor.sh`는 이 GTT 사용량을 학습/채점 진행상황·모델 정보·전력(CPU/GPU/전체
RAPL 3분할)과 함께 한 화면에 보여줘서 이 사각지대를 없앤다.

## 왜 필요한가

- **GTT(통합메모리) 실사용량** — `nvtop`이 못 보는, 학습 중 실제로 모델·옵티마이저 상태가 차지하는 메모리
- **양자화 / 스텝 / 채점 진행률** — systemd 유닛 로그를 파싱해 지금 무슨 단계인지, ETA는 얼마인지 표시
- **전력 3분할** — RAPL 기준 CPU / GPU / 전체(PPT) 전력을 근사 계산 (GPU = 전체 − CPU)
- **모델 정보 자동 추출** — 로그의 `command` 라인을 파싱해 base 모델, 양자화 비트, LoRA 설정 등을 표시

## 필요 환경

| 항목 | 설명 |
|---|---|
| OS | Linux |
| GPU 드라이버 | amdgpu (sysfs 경로 `/sys/class/drm/card*/device/mem_info_*` 필요) |
| ROCm (선택) | `rocm-smi` — 없어도 동작하지만 클럭(sclk) 표시가 `?`로 빠짐 |
| 학습/채점 잡 | **systemd `--user` 유닛**으로 실행되고, 로그를 `LOG_DIR/<유닛명>*.log` 파일로 남길 것. 유닛 이름은 `UNIT_GLOB` 패턴과 일치해야 함 |
| 전력(RAPL) 표시 (선택) | 최초 1회 권한 부여 필요: `sudo chmod a+r /sys/class/powercap/intel-rapl:*/energy_uj` (재부팅 시 초기화되므로 상시 필요하면 udev rule로 영구화 권장) |

## 설치 / 사용

별도 설치 없이 스크립트 하나로 동작한다.

```bash
bash monitor.sh
```

별도 터미널에 띄워두고 학습/채점 잡을 systemd `--user`로 돌리면 자동으로 감지해 보여준다. 종료는 `Ctrl-C`.

## 설정 (환경변수)

`monitor.sh` 상단 CONFIG 블록에서 아래 환경변수로 기본값을 덮어쓸 수 있다.

| 환경변수 | 내부 변수 | 기본값 | 설명 |
|---|---|---|---|
| `HALO_LOG_DIR` | `LOG_DIR` | `$HOME/gpu_jobs/logs` | 학습/채점 잡 로그가 쌓이는 디렉토리 |
| `HALO_UNIT_GLOB` | `UNIT_GLOB` | `gpujob-*` | 감지할 systemd `--user` 유닛 이름 패턴 |
| `HALO_TITLE` | `TITLE` | `Strix Halo Train/Score Monitor` | 대시보드 제목줄에 표시할 이름 |
| `HALO_POOL_GB` | `POOL_GB` | `60` | 통합메모리 풀 안내치(GB) — 경고 문구 계산용. 실제 총량은 sysfs에서 읽음 |
| `HALO_HELDOUT_TOTAL` | `HELDOUT_TOTAL` | `7` | 채점 시 heldout 태스크 총 개수 (진행률 표시용) |

예:

```bash
HALO_LOG_DIR=/data/logs HALO_UNIT_GLOB="myjob-*" HALO_TITLE="My GPU Box" bash monitor.sh
```

## 예시 출력

```
╔══════════════════ Strix Halo Train/Score Monitor (gfx1151) ══════════════════╗
  진행:  🧮 채점 Qwen2.5-Coder 32B — 생성 4/7, 최근: generated [...]
  경과:  2h13m07s        완료예상: — (남은 일부 태스크는 생성에 수시간 걸릴 수 있음, 다음 태스크 생성 중)     오류: 0
  모델:  Qwen2.5-Coder 32B · HQQ 4bit · 어댑터 my-lora-adapter · heldout mn512
  ──────────────────────────────── 통합메모리 ────────────────────────────────
  ★GTT(모델):   38.2 / 96GB  [████████░░░░░░░░░░░░] 40%   증가 +12 MB/s
   전용VRAM:    0.4GB (nvtop이 보는 값)     통합풀 ~60GB (GTT+host 이 안이어야 안전)
   host RAM여유: 18.6GB ✓     swap: 0.0GB
  ─────────────────────────────── 전력 · GPU ─────────────────────────────────
   ★전력:  CPU  22W  │  GPU  87W  │  전체 109W    (GPU=전체−CPU 근사, RAPL)
   sclk: 2100Mhz      유닛: active
╚═══════════════════════════════ 14:32:07 · Ctrl-C 종료 ═══════════════════════════════╝
```

## 한계 / 주의

- 특정 로그 문구에 **강하게 의존**한다. 학습/채점 스크립트가 아래 형식으로 로그를 남기지 않으면 진행률·모델
  정보가 비어 보인다:
  - 양자화 진행: `N/616 Linear 양자화` 형태
  - 학습 스텝: `step N |` 형태 (+ `optim_steps≈N`, `loss(avg8) N`, `N.Ns/step`)
  - 채점 생성: `generated [...]` 형태
  - 모델/설정 정보: 로그 첫 부분의 `command ...` 라인 (`--base`, `--hqq-nbits`, `--seq`, `--lora-r`,
    `--adapter`, `--heldout` 등 옵션을 파싱)
- sysfs 경로(`/sys/class/drm/card*/device/mem_info_*`, `/sys/class/hwmon/hwmon*`)는 **amdgpu 드라이버 전제**다.
  다른 GPU/드라이버에서는 동작하지 않는다.
- `base_label_for()`의 모델 라벨 매핑은 하드코딩돼 있다. 새 모델을 쓰면 `monitor.sh` 안의 `case` 문에
  한 줄만 추가하면 된다 (매핑이 없어도 디렉토리명 그대로 표시되어 동작에는 문제없음).
- RAPL 카운터는 랩어라운드 시 해당 회차 전력 표시를 건너뛴다(음수 방지).

---

# strix-halo-monitor (English)

**A real-time terminal dashboard for monitoring LLM training/scoring on AMD Strix Halo (gfx1151) unified-memory
APUs.**

`nvtop` only reports dedicated VRAM. On unified-memory (UMA) APUs like Strix Halo, the memory a model actually
occupies shows up as **GTT (system memory mapped to the GPU)** instead — so `nvtop` alone can't tell you how much
memory your model is really using. `monitor.sh` closes that gap by showing GTT usage alongside training/scoring
progress, model info, and a 3-way power split (CPU/GPU/total via RAPL) in a single screen.

## Why

- **Real GTT (unified memory) usage** — what `nvtop` can't see: the memory the model and optimizer state actually
  occupy during training
- **Quantization / step / scoring progress** — parses systemd unit logs to show the current phase and ETA
- **3-way power split** — CPU / GPU / total (PPT) power approximated from RAPL counters (GPU = total − CPU)
- **Automatic model info extraction** — parses the `command` line in the log to show base model, quantization
  bits, LoRA config, etc.

## Requirements

| Item | Notes |
|---|---|
| OS | Linux |
| GPU driver | amdgpu (needs `/sys/class/drm/card*/device/mem_info_*` sysfs paths) |
| ROCm (optional) | `rocm-smi` — without it the dashboard still works, only the clock (sclk) field shows `?` |
| Training/scoring job | Must run as a **systemd `--user` unit** and write logs to `LOG_DIR/<unit-name>*.log`. Unit name must match the `UNIT_GLOB` pattern |
| Power (RAPL) display (optional) | One-time permission grant: `sudo chmod a+r /sys/class/powercap/intel-rapl:*/energy_uj` (resets on reboot — use a udev rule to persist if needed) |

## Install / Usage

No installation required — it's a single script.

```bash
bash monitor.sh
```

Run it in its own terminal alongside a training/scoring job running as a systemd `--user` unit; it auto-detects
the running unit. Exit with `Ctrl-C`.

## Configuration (environment variables)

These override the defaults in the CONFIG block at the top of `monitor.sh`.

| Env var | Internal var | Default | Description |
|---|---|---|---|
| `HALO_LOG_DIR` | `LOG_DIR` | `$HOME/gpu_jobs/logs` | Directory where training/scoring job logs are written |
| `HALO_UNIT_GLOB` | `UNIT_GLOB` | `gpujob-*` | Name pattern of the systemd `--user` unit(s) to detect |
| `HALO_TITLE` | `TITLE` | `Strix Halo Train/Score Monitor` | Title shown in the dashboard header |
| `HALO_POOL_GB` | `POOL_GB` | `60` | Advisory unified-memory pool size (GB) used for the warning text; actual total is read from sysfs |
| `HALO_HELDOUT_TOTAL` | `HELDOUT_TOTAL` | `7` | Total number of heldout scoring tasks (used for progress display) |

Example:

```bash
HALO_LOG_DIR=/data/logs HALO_UNIT_GLOB="myjob-*" HALO_TITLE="My GPU Box" bash monitor.sh
```

## Example output

```
╔══════════════════ Strix Halo Train/Score Monitor (gfx1151) ══════════════════╗
  진행:  🧮 채점 Qwen2.5-Coder 32B — 생성 4/7, 최근: generated [...]
  경과:  2h13m07s        완료예상: — (남은 일부 태스크는 생성에 수시간 걸릴 수 있음, 다음 태스크 생성 중)     오류: 0
  모델:  Qwen2.5-Coder 32B · HQQ 4bit · 어댑터 my-lora-adapter · heldout mn512
  ──────────────────────────────── 통합메모리 ────────────────────────────────
  ★GTT(모델):   38.2 / 96GB  [████████░░░░░░░░░░░░] 40%   증가 +12 MB/s
   전용VRAM:    0.4GB (nvtop이 보는 값)     통합풀 ~60GB (GTT+host 이 안이어야 안전)
   host RAM여유: 18.6GB ✓     swap: 0.0GB
  ─────────────────────────────── 전력 · GPU ─────────────────────────────────
   ★전력:  CPU  22W  │  GPU  87W  │  전체 109W    (GPU=전체−CPU 근사, RAPL)
   sclk: 2100Mhz      유닛: active
╚═══════════════════════════════ 14:32:07 · Ctrl-C 종료 ═══════════════════════════════╝
```

(Dashboard text itself is Korean; the layout/values are what matters for non-Korean readers.)

## Limitations / caveats

- **Heavily dependent on specific log phrases.** If your training/scoring script doesn't emit logs in these
  formats, progress and model info will show blank:
  - Quantization progress: `N/616 Linear 양자화`
  - Training step: `step N |` (plus `optim_steps≈N`, `loss(avg8) N`, `N.Ns/step`)
  - Scoring generation: `generated [...]`
  - Model/config info: a `command ...` line near the top of the log (parses `--base`, `--hqq-nbits`, `--seq`,
    `--lora-r`, `--adapter`, `--heldout`, etc.)
- The sysfs paths (`/sys/class/drm/card*/device/mem_info_*`, `/sys/class/hwmon/hwmon*`) **assume the amdgpu
  driver**. Won't work on other GPUs/drivers.
- Model label mapping in `base_label_for()` is hardcoded. Add a new model by adding one line to the `case`
  statement in `monitor.sh` (unmapped names just fall back to the raw directory name, so it still works without
  editing).
- RAPL counters skip a cycle's power reading on wraparound (to avoid negative values).

## License

MIT — see [LICENSE](LICENSE).
