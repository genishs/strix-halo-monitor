#!/usr/bin/env bash
# monitor.sh — Strix Halo (AMD Ryzen AI Max, gfx1151 APU) 학습/채점 통합 모니터링 대시보드
#
# 무엇을 하나:
#   systemd --user로 도는 GPU 학습/채점 유닛(기본 이름 패턴: gpujob-*)을 자동 감지해
#   - 진행상황: 양자화 진행률 / 학습 step·loss·ETA / 평가·저장 중 / 채점 heldout N개 생성
#   - 현재 학습·채점 중인 모델 정보 (base 모델·양자화 비트·seq 또는 max-new·LoRA/어댑터 등,
#     각 잡의 command 라인을 파싱해서 뽑아냄)
#   - 통합메모리 (GTT 사용량 + 전용 VRAM + host RAM 여유 + swap)
#   - 전력 3분할 (RAPL 기준 CPU / GPU / 전체)
#   을 2초 주기로 갱신해 보여주는 터미널 대시보드.
#
# 필요 환경:
#   - AMD APU + amdgpu 드라이버 (Strix Halo/gfx1151 기준으로 작성됐으나, 동일한 sysfs 경로
#     (/sys/class/drm/card*/device/mem_info_*, /sys/class/hwmon/hwmon*)가 있는 다른 amdgpu
#     APU/dGPU에서도 대체로 동작할 수 있음)
#   - ROCm (rocm-smi) — 없으면 sclk(클럭) 표시만 "?"로 빠지고 나머지는 정상 동작
#   - 학습/채점 잡을 systemd --user 유닛으로 실행하고, 잡 로그를 파일로 남길 것
#     (유닛 이름 패턴 = UNIT_GLOB, 로그 경로 = LOG_DIR/<유닛명>*.log)
#   - 전력(RAPL) 표시를 보려면 최초 1회 권한 부여 필요:
#       sudo chmod a+r /sys/class/powercap/intel-rapl:0/energy_uj \
#                       /sys/class/powercap/intel-rapl:0:0/energy_uj
#     (재부팅하면 권한이 초기화되므로, 상시 필요하면 udev rule로 영구화 권장)
#
# 사용법:
#   bash monitor.sh
#   HALO_LOG_DIR=/path/to/logs HALO_TITLE="My GPU Box" bash monitor.sh   # 환경변수로 커스터마이즈
#   (별도 터미널에서 띄워두고 Ctrl-C로 종료)

# ── 사용자 설정 (환경변수로 덮어쓰기 가능) ───────────────────────────────────
LOG_DIR="${HALO_LOG_DIR:-$HOME/gpu_jobs/logs}"          # 학습/채점 잡 로그 디렉토리
UNIT_GLOB="${HALO_UNIT_GLOB:-gpujob-*}"                 # systemd --user 유닛 이름 패턴
TITLE="${HALO_TITLE:-Strix Halo Train/Score Monitor}"   # 대시보드 제목
POOL_GB="${HALO_POOL_GB:-60}"                           # 통합메모리 풀 안내치(GB, 경고 문구용 — 실제 총량은 sysfs에서 읽음)
HELDOUT_TOTAL="${HALO_HELDOUT_TOTAL:-7}"                # 채점 heldout 태스크 총 개수
# 디스크 위젯(Phase 5): 표시할 마운트 목록과 여유공간 경고 임계.
#   HALO_DISK_MOUNTS = "라벨=경로;라벨=경로;..." (`;` 구분, 경로에 공백 허용). 빈 값이면 디스크섹션 끔.
#   여유가 <경고GB 또는 <경고% 이면 ⚠️ 마커. df(=statvfs)만 사용 — du/디렉토리 스캔 없음(학습 I/O 무간섭).
DISK_WARN_GB="${HALO_DISK_WARN_GB:-10}"                 # 여유공간 경고 임계(GiB)
DISK_WARN_PCT="${HALO_DISK_WARN_PCT:-5}"                # 여유공간 경고 임계(%)
# ─────────────────────────────────────────────────────────────────────────

# ── 언어 모드: 기본 한국어. HALO_LANG=en 환경변수 또는 --english/-e 인자로 영어 전환.
#    (인자가 환경변수보다 우선) ──────────────────────────────────────────
LANG_MODE="ko"
[ "${HALO_LANG:-ko}" = "en" ] && LANG_MODE="en"
for _arg in "$@"; do
  case "$_arg" in
    --english|-e) LANG_MODE="en";;
  esac
done
# t "한국어 문자열" "English string" — LANG_MODE에 따라 둘 중 하나를 골라 출력하는 국제화 헬퍼
t(){ if [ "$LANG_MODE" = "en" ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
# ─────────────────────────────────────────────────────────────────────────

# base 모델 디렉토리명 → 표시용 라벨 매핑.
# 여기에 매핑 추가: 아래 case에 `패턴) "라벨";;` 한 줄만 추가하면 됨.
# 매핑이 없으면 디렉토리명(basename) 그대로 표시하니, 매핑을 안 넣어도 동작은 함.
base_label_for(){
  case "$1" in
    mistral-large-2411)     echo "Mistral-Large 123B";;
    qwen2.5-72b-instruct)   echo "Qwen2.5 72B";;
    qwen2.5-coder-32b)      echo "Qwen2.5-Coder 32B";;
    qwen2.5-coder-14b)      echo "Qwen2.5-Coder 14B";;
    "")                     echo "";;
    *)                      echo "$1";;
  esac
}

# 디스크 마운트 목록 파싱(1회). HALO_DISK_MOUNTS 미설정 시 기본 3개(데이터·외장모델·루트).
disk_labels=(); disk_paths=()
if [ -n "${HALO_DISK_MOUNTS+set}" ]; then DISK_SPEC="$HALO_DISK_MOUNTS"; else
  DISK_SPEC="/mnt/data=/mnt/data;외장모델=/run/media/user/새 볼륨;/=/"; fi
_trim(){ printf '%s' "$1" | sed 's/^ *//; s/ *$//'; }
IFS=';' read -ra _dents <<< "$DISK_SPEC"
for _e in "${_dents[@]}"; do
  [ -z "$(_trim "$_e")" ] && continue
  if [[ "$_e" == *"="* ]]; then _lbl="$(_trim "${_e%%=*}")"; _pth="$(_trim "${_e#*=}")";
  else _lbl="$(_trim "$_e")"; _pth="$_lbl"; fi
  disk_labels+=("$_lbl"); disk_paths+=("$_pth")
done
disk_maxw=0; for _l in "${disk_labels[@]}"; do [ "${#_l}" -gt "$disk_maxw" ] && disk_maxw=${#_l}; done

# 네트워크 위젯(Phase 5): 표시할 인터페이스와 자동감지 모드.
#   HALO_NET_IFACES = "라벨=이름;이름;..." (`;` 구분). 미설정=자동감지, 빈 값이면 네트워크섹션 끔.
#   HALO_NET_AUTO = default(기본경로 인터페이스, 없으면 모든 비-loopback) | all(모든 비-loopback).
#   /sys/class/net/*/statistics/{rx,tx}_bytes 커널 카운터만 읽음 — tcpdump·패킷캡처 없음(다운로드/학습 I/O 무간섭).
NET_AUTO="${HALO_NET_AUTO:-default}"; [ "$NET_AUTO" = "all" ] || NET_AUTO="default"
net_names=(); net_labels=(); net_explicit=0
if [ -n "${HALO_NET_IFACES+set}" ]; then
  net_explicit=1
  IFS=';' read -ra _nents <<< "$HALO_NET_IFACES"
  for _e in "${_nents[@]}"; do
    [ -z "$(_trim "$_e")" ] && continue
    if [[ "$_e" == *"="* ]]; then _lbl="$(_trim "${_e%%=*}")"; _nm="$(_trim "${_e#*=}")";
    else _nm="$(_trim "$_e")"; _lbl="$_nm"; fi
    net_names+=("$_nm"); net_labels+=("$_lbl")
  done
fi
# 인터페이스별 직전 카운터(속도용)와 최초 카운터(세션 누적용). 이름으로 키.
declare -A net_prev_rx net_prev_tx net_base_rx net_base_tx

gttmax=$(cat /sys/class/drm/card*/device/mem_info_gtt_total 2>/dev/null | head -1)
gttmaxg=$(awk "BEGIN{printf \"%.0f\", $gttmax/1073741824}")
prev_gtt=0; prev_t=$(date +%s)
# eval/채점 생성단계 관측용(로그에 per-line 타임스탬프 없음 → 반복 간 관측). gen 시작시각과
# "0태스크에서 관측 시작했는가"(안 그러면 못 본 구간 tok/s를 과대추정하므로 tok/s 숨김).
eval_gen_start=""; eval_from_zero=0
# RAPL 전력 도메인 (sudo chmod a+r 후 읽기 가능). package-0=패키지전체(=PPT), 0:0=CPU코어
PKG=/sys/class/powercap/intel-rapl:0/energy_uj
CORE=/sys/class/powercap/intel-rapl:0:0/energy_uj
prev_pkg=$(cat "$PKG" 2>/dev/null || echo -1)
prev_core=$(cat "$CORE" 2>/dev/null || echo -1)
hms(){ local s=$1; printf "%dh%02dm%02ds" $((s/3600)) $(((s%3600)/60)) $((s%60)); }
while true; do
  gtt=$(cat /sys/class/drm/card*/device/mem_info_gtt_used 2>/dev/null | head -1)
  vram=$(cat /sys/class/drm/card*/device/mem_info_vram_used 2>/dev/null | head -1)
  # GPU 사용율(amdgpu gpu_busy_percent 커널 카운터, 읽기전용·순간값·간섭0). GTT 읽는 그 카드에서 같이 읽음.
  gpubusy=$(cat /sys/class/drm/card*/device/gpu_busy_percent 2>/dev/null | head -1)
  gttg=$(awk "BEGIN{printf \"%.1f\", $gtt/1073741824}")
  vramg=$(awk "BEGIN{printf \"%.1f\", $vram/1073741824}")
  pct=$(awk "BEGIN{printf \"%.0f\", $gtt/$gttmax*100}")
  ram=$(free -m | awk '/Mem:/{printf "%.1f", $7/1024}')
  swap=$(free -m | awk '/Swap:/{printf "%.1f", $3/1024}')
  pow=$(for h in /sys/class/hwmon/hwmon*; do [ "$(cat $h/name 2>/dev/null)" = amdgpu ] && awk '{printf "%.0f",$1/1000000}' "$h/power1_input" 2>/dev/null && break; done)
  sclk=$(rocm-smi --showclocks 2>/dev/null | grep -oiE '\(([0-9]+)Mhz\)' | grep -oE '[0-9]+' | tail -1)
  # 현재 실행 중인 gpujob 유닛(학습 or 채점) 우선, 없으면 최신 로그
  rununit=$(systemctl --user list-units "$UNIT_GLOB" --no-legend 2>/dev/null | awk '/running/{print $1}' | head -1)
  if [ -n "$rununit" ]; then unit="${rununit%.service}"; else unit=$(basename "$(ls -t "$LOG_DIR"/$UNIT_GLOB.log 2>/dev/null | head -1)" .log 2>/dev/null); fi
  LOG=$(ls -t "$LOG_DIR"/${unit}*.log 2>/dev/null | head -1)
  active=$(systemctl --user is-active "$unit.service" 2>/dev/null)
  # eval/채점 유닛 판별: score/grade/eval 이름 모두 채점(eval_hard_tsc)로 라우팅.
  # (과거엔 *score*만 봐서 gpujob-grade141b-* 가 학습잡으로 오인돼 채점 진행이 안 보였음.)
  case "$unit" in *score*|*grade*|*eval*) is_score=1;; *) is_score=0;; esac
  [ "$is_score" = "1" ] || eval_gen_start=""   # 비채점 잡이면 생성 타이머 초기화(다음 채점 재관측)
  # 현재 학습/채점 중인 모델 정보 (로그의 command : 라인을 파싱; 실패해도 우아하게 빈값)
  cmdline=$(grep -m1 '^command' "$LOG" 2>/dev/null)
  # --base 값은 공백 포함 경로일 수 있음(/run/media/user/새 볼륨/<model>). 순진한 [^ ]+ 는
  # 공백에서 잘려 basename이 "새"가 됐음 → --base 뒤부터 다음 " --플래그"(또는 줄끝)까지 통째로.
  base_raw=$(echo "$cmdline" | sed -n 's/.*--base \(.*\)/\1/p' | sed 's/ --.*//')
  base_bn=""; [ -n "$base_raw" ] && base_bn=$(basename "$base_raw")
  base_label=$(base_label_for "$base_bn")
  eval_label=$(echo "$cmdline" | grep -oE -e '--label +[^ ]+' | awk '{print $2}')
  # 채점 표시 라벨: --label(eval 실행명) 우선 → base_label → 유닛명.
  smodel="${eval_label:-${base_label:-${unit:-?}}}"
  nbits=$(echo "$cmdline" | grep -oE -e '--hqq-nbits +[0-9]+' | awk '{print $2}')
  seqv=$(echo "$cmdline" | grep -oE -e '--seq +[0-9]+' | awk '{print $2}')
  maxnew=$(echo "$cmdline" | grep -oE -e '--max-new +[0-9]+' | awk '{print $2}')
  lorar=$(echo "$cmdline" | grep -oE -e '--lora-r +[0-9]+' | awk '{print $2}')
  lomlp=$(echo "$cmdline" | grep -oE -e '--lora-mlp')
  epochsv=$(echo "$cmdline" | grep -oE -e '--epochs +[0-9]+' | awk '{print $2}')
  adapter_raw=$(echo "$cmdline" | sed -n 's/.*--adapter \(.*\)/\1/p' | sed 's/ --.*//')
  adapter_bn=""; [ -n "$adapter_raw" ] && adapter_bn=$(basename "$adapter_raw")
  heldoutv=$(echo "$cmdline" | grep -oE -e '--heldout')
  model_line=""
  addpart(){ [ -z "$1" ] && return; if [ -z "$model_line" ]; then model_line="$1"; else model_line="$model_line · $1"; fi; }
  addpart "$base_label"
  [ -n "$nbits" ] && addpart "HQQ ${nbits}bit"
  if [ "$is_score" = "1" ]; then
    [ -n "$adapter_bn" ] && addpart "$(t "어댑터" "adapter") ${adapter_bn}"
    if [ -n "$heldoutv" ]; then
      if [ -n "$maxnew" ]; then addpart "heldout mn${maxnew}"; else addpart "heldout"; fi
    fi
  else
    [ -n "$seqv" ] && addpart "seq${seqv}"
    if [ -n "$lorar" ]; then
      lora_str="LoRA r${lorar}"; [ -n "$lomlp" ] && lora_str="${lora_str}+mlp"
      addpart "$lora_str"
    fi
    [ -n "$epochsv" ] && addpart "${epochsv}ep"
  fi
  # 경과시간 (유닛 시작 기준)
  start=$(date -d "$(systemctl --user show "$unit.service" -p ActiveEnterTimestamp --value 2>/dev/null)" +%s 2>/dev/null)
  now=$(date +%s); elapsed="?"; [ -n "$start" ] && [ "$start" -gt 0 ] 2>/dev/null && elapsed=$(hms $((now-start)))
  # 진행/ETA
  quant=$(grep -oE '[0-9]+/[0-9]+ Linear 양자화' "$LOG" 2>/dev/null | tail -1)
  total=$(grep -oE 'optim_steps≈[0-9]+' "$LOG" 2>/dev/null | tail -1 | grep -oE '[0-9]+')
  cur=$(grep -oE 'step [0-9]+ \|' "$LOG" 2>/dev/null | tail -1 | grep -oE '[0-9]+')
  sstep=$(grep -oE '[0-9.]+s/step' "$LOG" 2>/dev/null | tail -1 | grep -oE '[0-9.]+')
  loss=$(grep -oE 'loss\(avg8\) [0-9.]+' "$LOG" 2>/dev/null | tail -1 | grep -oE '[0-9.]+')
  phase="$(t "대기 중" "idle")"; eta="—"; donetime="—"
  if [ "$is_score" = "1" ]; then
    # 채점: 양자화(재사용 quant) → heldout N개 생성 → (재채점 파이프라인은 이 유닛 밖에서 진행될 수 있음)
    replaced=$(grep -c 'HQQ 스트리밍 치환 완료' "$LOG" 2>/dev/null); replaced=${replaced:-0}
    gen_done=$(grep -c 'generated \[' "$LOG" 2>/dev/null); gen_done=${gen_done:-0}
    # 현재/최근 태스크명(신 eval_hard_tsc 포맷 "generated [name   ] N chars ..."에서 이름만)
    cur_task=$(grep -oE 'generated \[[^]]+' "$LOG" 2>/dev/null | tail -1 | sed 's/generated \[//; s/ *$//')
    if [ "$active" != "active" ]; then
      eval_gen_start=""
      res=$(systemctl --user show "$unit.service" -p Result --value 2>/dev/null)
      score_line=$(grep -oE 'SCORE [0-9.]+/[0-9]+ = [0-9.]+%' "$LOG" 2>/dev/null | tail -1)
      if [ -n "$score_line" ]; then
        phase="$(t "⏸ 채점 종료 ($active/$res) — $score_line" "⏸ Eval finished ($active/$res) — $score_line")"
      else
        phase="$(t "⏸ 채점 종료 ($active/$res)" "⏸ Eval finished ($active/$res)")"
      fi
    elif [ "$replaced" -eq 0 ] && [ "$gen_done" -eq 0 ] && [ -n "$quant" ]; then
      eval_gen_start=""
      phase="$(t "🔧 채점준비: 양자화 $quant" "🔧 Eval prep: quantizing $quant")"
      eta="$(t "채점 전 준비단계" "pre-eval prep")"
    else
      # 관측 tok/s: 생성단계 첫 관측 시각 기록(0태스크에서 시작했을 때만 신뢰).
      if [ -z "$eval_gen_start" ]; then eval_gen_start=$(date +%s); [ "$gen_done" -eq 0 ] && eval_from_zero=1 || eval_from_zero=0; fi
      gen_toks=$(grep -oE 'new=[0-9]+' "$LOG" 2>/dev/null | grep -oE '[0-9]+' | awk '{s+=$1}END{print s+0}')
      gel=$(( $(date +%s) - eval_gen_start ))
      toks_s="—"; { [ "$eval_from_zero" = "1" ] && [ "$gel" -gt 1 ] && [ "${gen_toks:-0}" -gt 0 ]; } && toks_s=$(awk "BEGIN{printf \"%.1f\", $gen_toks/$gel}")
      phase="$(t "🧮 채점 ${smodel} — task ${gen_done}/${HELDOUT_TOTAL} (${toks_s} tok/s), 현재: ${cur_task:-대기중}" "🧮 Eval ${smodel} — task ${gen_done}/${HELDOUT_TOTAL} (${toks_s} tok/s), current: ${cur_task:-waiting}")"
      if [ "$gen_done" -ge 1 ] && [ -n "$start" ] && [ "$start" -gt 0 ] 2>/dev/null; then
        el=$(( $(date +%s) - start ))
        rem=$(( el * (HELDOUT_TOTAL - gen_done) / gen_done ))   # 남은태스크 × (경과/완료태스크) — 선형 외삽, 대략값
        eta="$(hms $rem)  $(t "대략(태스크 편차 큼)" "rough (high task variance)")"
        donetime=$(date -d "+$rem seconds" '+%H:%M' 2>/dev/null)
      else
        eta="$(t "첫 태스크 생성 중 — 산정 대기" "generating first task — estimating")"; donetime="—"
      fi
    fi
  elif [ -n "$cur" ] && [ -n "$total" ] && [ "$cur" -ge "$total" ] && [ "$active" = "active" ]; then
    # 마지막 스텝 끝났는데 유닛 살아있음 = 평가(val loss)/저장 중 (무로깅, GPU 100%)
    phase="$(t "💾 평가·저장 중  (step $cur/$total 완료, val loss 계산 후 어댑터 저장)" "💾 Eval·save (step $cur/$total done, val loss then adapter save)")"
    eta="$(t "스텝 카운트 밖 (몇 분 소요)" "beyond step count (a few min)")"
  elif [ -n "$cur" ] && [ -n "$total" ]; then
    phase="$(t "🏃 학습  step $cur/$total   loss $loss   ${sstep}s/step" "🏃 Training step $cur/$total loss $loss ${sstep}s/step")"
    if [ -n "$sstep" ]; then
      rem=$(awk "BEGIN{printf \"%d\", ($total-$cur)*$sstep}")
      eta=$(hms $rem); donetime=$(date -d "+$rem seconds" '+%H:%M')
    fi
  elif [ -n "$total" ]; then
    # optim_steps 마커는 떴는데 step 라인이 아직 없음 = 양자화 끝나고 첫 스텝 연산 중
    phase="$(t "⚙️ 첫 스텝 연산 중  (양자화 완료, step 1 forward/backward)" "⚙️ Computing first step (quant done, step 1 fwd/bwd)")"
    eta="$(t "첫 스텝은 워밍업 포함 ~8분" "first step incl. warmup ~8min")"
  elif [ -n "$quant" ]; then
    qc=${quant%%/*}; qt=$(echo "$quant"|grep -oE '/[0-9]+'|tr -d '/')
    qpct=$(awk "BEGIN{printf \"%.0f\", $qc/$qt*100}")
    phase="$(t "🔧 양자화  $quant  (${qpct}%)" "🔧 Quantizing $quant (${qpct}%)")"
    eta="$(t "학습 전 준비단계" "pre-training prep")"
  elif [ "$active" != "active" ]; then
    res=$(systemctl --user show "$unit.service" -p Result --value 2>/dev/null)
    phase="$(t "⏸ 종료 ($active/$res)" "⏸ Finished ($active/$res)")"
  fi
  err=$(grep -icE 'Traceback|hipError|unspecified launch|out of memory|Killed|device wedged' "$LOG" 2>/dev/null)
  now2=$(date +%s); dt=$((now2-prev_t)); [ "$dt" -lt 1 ] && dt=1
  rate=$(awk "BEGIN{printf \"%+.0f\", ($gtt-$prev_gtt)/1048576/$dt}"); prev_gtt=$gtt; prev_t=$now2
  # 전력 3분할: CPU=RAPL core, 전체=RAPL package(=PPT), GPU=전체−CPU (2초 델타)
  cpuW="?"; gpuW="?"; totW="${pow:-?}"
  if [ -r "$PKG" ] && [ -r "$CORE" ] && [ "$prev_pkg" -ge 0 ] 2>/dev/null; then
    cur_pkg=$(cat "$PKG" 2>/dev/null); cur_core=$(cat "$CORE" 2>/dev/null)
    dpkg=$((cur_pkg-prev_pkg)); dcore=$((cur_core-prev_core))
    if [ "$dpkg" -ge 0 ] && [ "$dcore" -ge 0 ]; then   # 음수=카운터 랩어라운드 → 이번회차 스킵
      totW=$(awk "BEGIN{printf \"%.0f\", $dpkg/1e6/$dt}")
      cpuW=$(awk "BEGIN{printf \"%.0f\", $dcore/1e6/$dt}")
      gpuW=$(awk "BEGIN{v=($dpkg-$dcore)/1e6/$dt; printf \"%.0f\", (v<0)?0:v}")
    fi
    prev_pkg=$cur_pkg; prev_core=$cur_core
  fi
  filled=$((pct/5)); [ "$filled" -gt 20 ] && filled=20
  bar=$(printf '█%.0s' $(seq 1 $filled 2>/dev/null))$(printf '░%.0s' $(seq 1 $((20-filled)) 2>/dev/null))
  ramlow=$(t "위험" "LOW")
  ramflag=$(awk -v low="⚠️$ramlow" "BEGIN{print ($ram<3)?low:\"✓\"}")
  clear
  echo "╔══════════════════ $TITLE (gfx1151) ══════════════════╗"
  printf "  %s:  %s\n" "$(t "진행" "Progress")" "$phase"
  printf "  %s:  %-14s   %s: %s (%s %s)     %s: %s\n" "$(t "경과" "Elapsed")" "$elapsed" "$(t "완료예상" "ETA")" "$donetime" "$(t "남은" "remaining")" "$eta" "$(t "오류" "errors")" "$err"
  printf "  %s:  %s\n" "$(t "모델" "Model")" "${model_line:-?}"
  echo   "  ──────────────────────────────── $(t "통합메모리" "Unified Memory") ────────────────────────────────"
  # GPU 사용율은 값이 있을 때만 GTT 줄 끝에 덧붙임(가산적; 파일 없으면 기존 줄 그대로).
  gpubusy_seg=""; [ -n "$gpubusy" ] && gpubusy_seg="   $(t "GPU 사용" "GPU busy") ${gpubusy}%"
  printf "  ★%s:  %5s / %sGB  [%s] %s%%   %s %s MB/s%s\n" "$(t "GTT(모델)" "GTT(model)")" "$gttg" "$gttmaxg" "$bar" "$pct" "$(t "증가" "rate")" "$rate" "$gpubusy_seg"
  printf "   %s:   %5sGB %s     %s\n" "$(t "전용VRAM" "VRAM(ded.)")" "$vramg" "$(t "(nvtop이 보는 값)" "(what nvtop sees)")" "$(t "통합풀 ~${POOL_GB}GB (GTT+host 이 안이어야 안전)" "unified pool ~${POOL_GB}GB (GTT+host must fit)")"
  printf "   %s: %sGB %s     swap: %sGB\n" "$(t "host RAM여유" "host RAM free")" "$ram" "$ramflag" "$swap"
  echo   "  ─────────────────────────────── $(t "전력 · GPU" "Power · GPU") ─────────────────────────────────"
  printf "   ★%s:  CPU %3sW  │  GPU %3sW  │  %s %3sW    %s\n" "$(t "전력" "Power")" "$cpuW" "$gpuW" "$(t "전체" "Total")" "$totW" "$(t "(GPU=전체−CPU 근사, RAPL)" "(GPU=Total−CPU approx, RAPL)")"
  printf "   sclk: %sMhz      %s: %s\n" "${sclk:-?}" "$(t "유닛" "unit")" "$active"
  # 디스크: 마운트별 사용율/여유공간/부족경고 (df=statvfs만; du·재귀스캔 없음 → 학습 I/O 무간섭)
  if [ "${#disk_paths[@]}" -gt 0 ]; then
    echo "  ────────────────────────────────── $(t "디스크" "Disk") ──────────────────────────────────"
    _i=0
    while [ "$_i" -lt "${#disk_paths[@]}" ]; do
      _lbl="${disk_labels[$_i]}"; _pth="${disk_paths[$_i]}"
      _lblpad=$(printf "%-${disk_maxw}s" "$_lbl")
      _df=$(df -B1 --output=size,used,avail "$_pth" 2>/dev/null | tail -1)
      if [ -z "$_df" ] || ! printf '%s' "$_df" | grep -qE '^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]*$'; then
        printf "   ★%s:   %s\n" "$_lblpad" "$(t "사용불가" "unavailable")"
      else
        set -- $_df; _sz=$1; _us=$2; _av=$3
        _pct=$(awk "BEGIN{printf \"%.0f\", $_us/$_sz*100}")
        _usg=$(awk "BEGIN{printf \"%.1f\", $_us/1073741824}")
        _szg=$(awk "BEGIN{printf \"%.0f\", $_sz/1073741824}")
        _avg=$(awk "BEGIN{printf \"%.1f\", $_av/1073741824}")
        _fl=$((_pct/5)); [ "$_fl" -gt 20 ] && _fl=20; [ "$_fl" -lt 0 ] && _fl=0
        _bar=$(printf '█%.0s' $(seq 1 $_fl 2>/dev/null))$(printf '░%.0s' $(seq 1 $((20-_fl)) 2>/dev/null))
        _low=$(awk "BEGIN{ag=$_av/1073741824; ap=$_av/$_sz*100; print (ag<$DISK_WARN_GB||ap<$DISK_WARN_PCT)?1:0}")
        if [ "$_low" = "1" ]; then _flag="⚠️$(t "위험" "LOW")"; else _flag="✓"; fi
        printf "   ★%s:   %5s / %sGB  [%s] %s%%   %s %sGB %s\n" "$_lblpad" "$_usg" "$_szg" "$_bar" "$_pct" "$(t "여유" "free")" "$_avg" "$_flag"
      fi
      _i=$((_i+1))
    done
  fi
  # 네트워크: 인터페이스별 다운로드(RX)/업로드(TX) 속도 (커널 카운터 델타만; 패킷캡처·tcpdump 없음 → 다운로드/학습 무간섭)
  cur_net_names=(); cur_net_labels=()
  if [ "$net_explicit" = "1" ]; then
    cur_net_names=("${net_names[@]}"); cur_net_labels=("${net_labels[@]}")
  else
    if [ "$NET_AUTO" = "all" ]; then _nl=""; else
      _nl=$(awk 'NR>1 && $2=="00000000" && $1!="lo"{print $1}' /proc/net/route 2>/dev/null | awk '!s[$0]++'); fi
    [ -z "$_nl" ] && _nl=$(for _d in /sys/class/net/*; do _n=$(basename "$_d"); [ "$_n" = lo ] && continue; echo "$_n"; done)
    while IFS= read -r _n; do [ -z "$_n" ] && continue; cur_net_names+=("$_n"); cur_net_labels+=("$_n"); done <<< "$_nl"
  fi
  if [ "${#cur_net_names[@]}" -gt 0 ]; then
    net_maxw=0; for _l in "${cur_net_labels[@]}"; do [ "${#_l}" -gt "$net_maxw" ] && net_maxw=${#_l}; done
    echo "  ───────────────────────────────── $(t "네트워크" "Network") ─────────────────────────────────"
    _i=0
    while [ "$_i" -lt "${#cur_net_names[@]}" ]; do
      _nm="${cur_net_names[$_i]}"; _lbl="${cur_net_labels[$_i]}"; _lblpad=$(printf "%-${net_maxw}s" "$_lbl")
      _rx=$(cat "/sys/class/net/$_nm/statistics/rx_bytes" 2>/dev/null)
      _tx=$(cat "/sys/class/net/$_nm/statistics/tx_bytes" 2>/dev/null)
      if [ -z "$_rx" ] || [ -z "$_tx" ]; then
        printf "   ★%s:   %s\n" "$_lblpad" "$(t "사용불가" "unavailable")"
      else
        [ -z "${net_base_rx[$_nm]:-}" ] && net_base_rx[$_nm]=$_rx && net_base_tx[$_nm]=$_tx
        _prx="${net_prev_rx[$_nm]:-}"; _ptx="${net_prev_tx[$_nm]:-}"
        if [ -n "$_prx" ]; then
          _rxr=$(awk "BEGIN{d=$_rx-$_prx; if(d<0)printf\"?\";else printf\"%.1f\",d/1048576/$dt}")
          _txr=$(awk "BEGIN{d=$_tx-$_ptx; if(d<0)printf\"?\";else printf\"%.1f\",d/1048576/$dt}")
        else _rxr="?"; _txr="?"; fi
        _rxs=$(awk "BEGIN{printf\"%.1f\",($_rx-${net_base_rx[$_nm]})/1073741824}")
        _txs=$(awk "BEGIN{printf\"%.1f\",($_tx-${net_base_tx[$_nm]})/1073741824}")
        printf "   ★%s:   ↓ %7s MB/s   ↑ %7s MB/s   (%s ↓ %sGB ↑ %sGB)\n" "$_lblpad" "$_rxr" "$_txr" "$(t "누적" "total")" "$_rxs" "$_txs"
        net_prev_rx[$_nm]=$_rx; net_prev_tx[$_nm]=$_tx
      fi
      _i=$((_i+1))
    done
  fi
  echo "╚═══════════════════════════════ %s · $(t "Ctrl-C 종료" "Ctrl-C to quit") ═══════════════════════════════╝" | sed "s/%s/$(date '+%H:%M:%S')/"
  sleep 2
done
