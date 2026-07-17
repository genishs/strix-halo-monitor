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

gttmax=$(cat /sys/class/drm/card*/device/mem_info_gtt_total 2>/dev/null | head -1)
gttmaxg=$(awk "BEGIN{printf \"%.0f\", $gttmax/1073741824}")
prev_gtt=0; prev_t=$(date +%s)
# RAPL 전력 도메인 (sudo chmod a+r 후 읽기 가능). package-0=패키지전체(=PPT), 0:0=CPU코어
PKG=/sys/class/powercap/intel-rapl:0/energy_uj
CORE=/sys/class/powercap/intel-rapl:0:0/energy_uj
prev_pkg=$(cat "$PKG" 2>/dev/null || echo -1)
prev_core=$(cat "$CORE" 2>/dev/null || echo -1)
hms(){ local s=$1; printf "%dh%02dm%02ds" $((s/3600)) $(((s%3600)/60)) $((s%60)); }
while true; do
  gtt=$(cat /sys/class/drm/card*/device/mem_info_gtt_used 2>/dev/null | head -1)
  vram=$(cat /sys/class/drm/card*/device/mem_info_vram_used 2>/dev/null | head -1)
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
  case "$unit" in *score*) is_score=1;; *) is_score=0;; esac
  # 현재 학습/채점 중인 모델 정보 (로그의 command : 라인을 파싱; 실패해도 우아하게 빈값)
  cmdline=$(grep -m1 '^command' "$LOG" 2>/dev/null)
  base_raw=$(echo "$cmdline" | grep -oE -e '--base +[^ ]+' | awk '{print $2}')
  base_bn=""; [ -n "$base_raw" ] && base_bn=$(basename "$base_raw")
  base_label=$(base_label_for "$base_bn")
  smodel="${base_label:-${unit:-?}}"
  nbits=$(echo "$cmdline" | grep -oE -e '--hqq-nbits +[0-9]+' | awk '{print $2}')
  seqv=$(echo "$cmdline" | grep -oE -e '--seq +[0-9]+' | awk '{print $2}')
  maxnew=$(echo "$cmdline" | grep -oE -e '--max-new +[0-9]+' | awk '{print $2}')
  lorar=$(echo "$cmdline" | grep -oE -e '--lora-r +[0-9]+' | awk '{print $2}')
  lomlp=$(echo "$cmdline" | grep -oE -e '--lora-mlp')
  epochsv=$(echo "$cmdline" | grep -oE -e '--epochs +[0-9]+' | awk '{print $2}')
  adapter_raw=$(echo "$cmdline" | grep -oE -e '--adapter +[^ ]+' | awk '{print $2}')
  adapter_bn=""; [ -n "$adapter_raw" ] && adapter_bn=$(basename "$adapter_raw")
  heldoutv=$(echo "$cmdline" | grep -oE -e '--heldout')
  model_line=""
  addpart(){ [ -z "$1" ] && return; if [ -z "$model_line" ]; then model_line="$1"; else model_line="$model_line · $1"; fi; }
  addpart "$base_label"
  [ -n "$nbits" ] && addpart "HQQ ${nbits}bit"
  if [ "$is_score" = "1" ]; then
    [ -n "$adapter_bn" ] && addpart "어댑터 ${adapter_bn}"
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
  phase="대기 중"; eta="—"; donetime="—"
  if [ "$is_score" = "1" ]; then
    # 채점: 양자화(재사용 quant) → heldout N개 생성 → (재채점 파이프라인은 이 유닛 밖에서 진행될 수 있음)
    replaced=$(grep -c 'HQQ 스트리밍 치환 완료' "$LOG" 2>/dev/null); replaced=${replaced:-0}
    gen_done=$(grep -c 'generated \[' "$LOG" 2>/dev/null); gen_done=${gen_done:-0}
    last_gen=$(grep -oE 'generated \[.*' "$LOG" 2>/dev/null | tail -1)
    if [ "$active" != "active" ]; then
      res=$(systemctl --user show "$unit.service" -p Result --value 2>/dev/null)
      phase="⏸ 채점 종료 ($active/$res)"
    elif [ "$replaced" -eq 0 ] && [ "$gen_done" -eq 0 ] && [ -n "$quant" ]; then
      phase="🔧 채점준비: 양자화 $quant"
      eta="채점 전 준비단계"
    else
      phase="🧮 채점 ${smodel} — 생성 ${gen_done}/${HELDOUT_TOTAL}, 최근: ${last_gen:-대기중}"
      if [ "$gen_done" -ge 1 ] && [ -n "$start" ] && [ "$start" -gt 0 ] 2>/dev/null; then
        el=$(( $(date +%s) - start ))
        rem=$(( el * (HELDOUT_TOTAL - gen_done) / gen_done ))   # 남은태스크 × (경과/완료태스크) — 선형 외삽, 대략값
        eta="$(hms $rem)  대략(태스크 편차 큼)"
        donetime=$(date -d "+$rem seconds" '+%H:%M' 2>/dev/null)
      else
        eta="첫 태스크 생성 중 — 산정 대기"; donetime="—"
      fi
    fi
  elif [ -n "$cur" ] && [ -n "$total" ] && [ "$cur" -ge "$total" ] && [ "$active" = "active" ]; then
    # 마지막 스텝 끝났는데 유닛 살아있음 = 평가(val loss)/저장 중 (무로깅, GPU 100%)
    phase="💾 평가·저장 중  (step $cur/$total 완료, val loss 계산 후 어댑터 저장)"
    eta="스텝 카운트 밖 (몇 분 소요)"
  elif [ -n "$cur" ] && [ -n "$total" ]; then
    phase="🏃 학습  step $cur/$total   loss $loss   ${sstep}s/step"
    if [ -n "$sstep" ]; then
      rem=$(awk "BEGIN{printf \"%d\", ($total-$cur)*$sstep}")
      eta=$(hms $rem); donetime=$(date -d "+$rem seconds" '+%H:%M')
    fi
  elif [ -n "$total" ]; then
    # optim_steps 마커는 떴는데 step 라인이 아직 없음 = 양자화 끝나고 첫 스텝 연산 중
    phase="⚙️ 첫 스텝 연산 중  (양자화 완료, step 1 forward/backward)"
    eta="첫 스텝은 워밍업 포함 ~8분"
  elif [ -n "$quant" ]; then
    qc=${quant%%/*}; qt=$(echo "$quant"|grep -oE '/[0-9]+'|tr -d '/')
    qpct=$(awk "BEGIN{printf \"%.0f\", $qc/$qt*100}")
    phase="🔧 양자화  $quant  (${qpct}%)"
    eta="학습 전 준비단계"
  elif [ "$active" != "active" ]; then
    res=$(systemctl --user show "$unit.service" -p Result --value 2>/dev/null)
    phase="⏸ 종료 ($active/$res)"
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
  ramflag=$(awk "BEGIN{print ($ram<3)?\"⚠️위험\":\"✓\"}")
  clear
  echo "╔══════════════════ $TITLE (gfx1151) ══════════════════╗"
  printf "  진행:  %s\n" "$phase"
  printf "  경과:  %-14s   완료예상: %s (남은 %s)     오류: %s\n" "$elapsed" "$donetime" "$eta" "$err"
  printf "  모델:  %s\n" "${model_line:-?}"
  echo   "  ──────────────────────────────── 통합메모리 ────────────────────────────────"
  printf "  ★GTT(모델):  %5s / %sGB  [%s] %s%%   증가 %s MB/s\n" "$gttg" "$gttmaxg" "$bar" "$pct" "$rate"
  printf "   전용VRAM:   %5sGB (nvtop이 보는 값)     통합풀 ~%sGB (GTT+host 이 안이어야 안전)\n" "$vramg" "$POOL_GB"
  printf "   host RAM여유: %sGB %s     swap: %sGB\n" "$ram" "$ramflag" "$swap"
  echo   "  ─────────────────────────────── 전력 · GPU ─────────────────────────────────"
  printf "   ★전력:  CPU %3sW  │  GPU %3sW  │  전체 %3sW    (GPU=전체−CPU 근사, RAPL)\n" "$cpuW" "$gpuW" "$totW"
  printf "   sclk: %sMhz      유닛: %s\n" "${sclk:-?}" "$active"
  echo "╚═══════════════════════════════ %s · Ctrl-C 종료 ═══════════════════════════════╝" | sed "s/%s/$(date '+%H:%M:%S')/"
  sleep 2
done
