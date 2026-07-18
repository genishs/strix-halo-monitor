# Log fixtures

Captured/synthetic log snippets used to pin the job parsers' behaviour (phase
transitions, progress, model-info, ETA). Each file is a small slice of what a
training/scoring unit writes to `LOG_DIR/<unit>*.log`.

**Masking / provenance (DESIGN O11):** these fixtures must never contain secrets,
absolute host paths, usernames, or private model/adapter names. The synthetic files
below are hand-written to the exact log formats documented in the README
("Limitations / caveats") and `legacy/monitor.sh`. The `real_*.log` files (Phase 2)
are read-only-captured slices of actual job logs from the live Strix Halo box, masked
before commit per the rules below.

Files:

| file | scenario | provenance |
|---|---|---|
| `train_running.log` | training mid-run: quant done, optim_steps known, several steps with loss + s/step | synthetic |
| `train_running_json.log` | same run, but the script also emits `HALOJSON` status lines (O4) | synthetic |
| `train_quant.log` | quantization phase only (pre-first-step) | synthetic |
| `train_first_step.log` | `optim_steps≈` present but no `step N |` line yet (first-step warmup) | synthetic |
| `score_running.log` | scoring mid-run: HQQ replacement done, some heldout tasks generated | synthetic |
| `score_prep.log` | scoring prep: quantizing, nothing generated yet | synthetic |
| `score_running_json.log` | scoring with `HALOJSON` status lines | synthetic |
| `real_score_123b.log` | real scoring run slice: command line + quant progress + 5 `generated [...]` lines. No HALOJSON (live scripts don't emit it yet). Captured read-only 2026-07-18 from `gpujob-score-123b-hqq2-seq512-20260717-182454-1438106.log` on the live box. **마스킹 완료**: `/home/user`→`/home/USER`, `/run/media/user/새 볼륨/...`→`/WORKSPACE/...`, `--adapter`/`--out` values→`./models/adapter-A`; public base-model name (`mistral-large-2411`) kept intact for label-map testing; ROCm-SMI GPU-state blocks and kernel-log tail (which carried the box hostname) dropped rather than masked. | real (masked) |
| `real_train_123b.log` | real training run slice: command line + quant progress + steps 1–5 (loss/s-step). Same masking as above. Captured read-only 2026-07-18 from `gpujob-train123bfull-20260717-122446-555987.log` (latest `gpujob-train*.log` on the box at capture time; the full run had already finished, but the excerpt below step 39 represents a normal mid-run slice). | real (masked) |
