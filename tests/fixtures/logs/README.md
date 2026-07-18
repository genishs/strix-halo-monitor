# Log fixtures

Captured/synthetic log snippets used to pin the job parsers' behaviour (phase
transitions, progress, model-info, ETA). Each file is a small slice of what a
training/scoring unit writes to `LOG_DIR/<unit>*.log`.

**Masking / provenance (DESIGN O11):** these fixtures must never contain secrets,
absolute host paths, usernames, or private model/adapter names. The current files are
**synthetic**, hand-written to the exact log formats documented in the README
("Limitations / caveats") and `legacy/monitor.sh`. When real box logs are captured
(read-only) for higher-fidelity parity testing, mask them the same way before adding
them here — request the masked snippets from the PM (피샘) rather than pulling from a
live box yourself.

Files:

| file | scenario |
|---|---|
| `train_running.log` | training mid-run: quant done, optim_steps known, several steps with loss + s/step |
| `train_running_json.log` | same run, but the script also emits `HALOJSON` status lines (O4) |
| `train_quant.log` | quantization phase only (pre-first-step) |
| `train_first_step.log` | `optim_steps≈` present but no `step N |` line yet (first-step warmup) |
| `score_running.log` | scoring mid-run: HQQ replacement done, some heldout tasks generated |
| `score_prep.log` | scoring prep: quantizing, nothing generated yet |
| `score_running_json.log` | scoring with `HALOJSON` status lines |
