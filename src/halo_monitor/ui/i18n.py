"""Message catalog + phase/ETA string assembly (ko/en) — DESIGN §2.2 A, O10.

All translation lives here; the parser returns keys/enums and this module renders
them. Strings are transcribed **verbatim** from ``legacy/monitor.sh`` (including its
quirks — e.g. the English quantization phase still embeds the Korean "Linear 양자화",
because the bash tool greps that literal and prints it in both languages).
"""

from __future__ import annotations

from ..model import EtaNote, JobState, JobType, Phase

Lang = str  # "ko" | "en"


def _pick(lang: Lang, ko: str, en: str) -> str:
    return en if lang == "en" else ko


# --- simple labels: key -> (ko, en) --------------------------------------- #
_LABELS: dict[str, tuple[str, str]] = {
    "progress": ("진행", "Progress"),
    "elapsed": ("경과", "Elapsed"),
    "eta": ("완료예상", "ETA"),
    "remaining": ("남은", "remaining"),
    "errors": ("오류", "errors"),
    "model": ("모델", "Model"),
    "unified_memory": ("통합메모리", "Unified Memory"),
    "gtt": ("GTT(모델)", "GTT(model)"),
    "rate": ("증가", "rate"),
    "vram": ("전용VRAM", "VRAM(ded.)"),
    "nvtop_note": ("(nvtop이 보는 값)", "(what nvtop sees)"),
    "host_ram": ("host RAM여유", "host RAM free"),
    "power_gpu": ("전력 · GPU", "Power · GPU"),
    "power": ("전력", "Power"),
    "total": ("전체", "Total"),
    "power_note": ("(GPU=전체−CPU 근사, RAPL)", "(GPU=Total−CPU approx, RAPL)"),
    "unit": ("유닛", "unit"),
    "quit": ("Ctrl-C 종료", "Ctrl-C to quit"),
    "adapter": ("어댑터", "adapter"),
    "ram_low": ("위험", "LOW"),
    "idle": ("대기 중", "idle"),
    "waiting": ("대기중", "waiting"),
    "disk": ("디스크", "Disk"),
    "free": ("여유", "free"),
    "disk_na": ("사용불가", "unavailable"),
    "network": ("네트워크", "Network"),
    "net_total": ("누적", "total"),
    "net_na": ("사용불가", "unavailable"),
    "eval": ("평가", "Eval"),
    "eval_task": ("태스크", "task"),
    "eval_cur": ("현재", "current"),
    "eval_compiling": ("컴파일·채점 중", "compiling & scoring"),
    "eval_observed": ("관측", "observed"),
    "eval_estimating": ("산정 대기", "estimating"),
    "eval_score": ("점수", "score"),
    "eval_done": ("완료", "done"),
}

_NOTE: dict[EtaNote, tuple[str, str]] = {
    EtaNote.PRE_TRAINING_PREP: ("학습 전 준비단계", "pre-training prep"),
    EtaNote.PRE_SCORING_PREP: ("채점 전 준비단계", "pre-scoring prep"),
    EtaNote.FIRST_STEP_WARMUP: ("첫 스텝은 워밍업 포함 ~8분", "first step incl. warmup ~8min"),
    EtaNote.BEYOND_STEP_COUNT: ("스텝 카운트 밖 (몇 분 소요)", "beyond step count (a few min)"),
    EtaNote.ROUGH_HIGH_VARIANCE: ("대략(태스크 편차 큼)", "rough (high task variance)"),
    EtaNote.ESTIMATING_FIRST_TASK: ("첫 태스크 생성 중 — 산정 대기", "generating first task — estimating"),
}


def t(lang: Lang, key: str) -> str:
    ko, en = _LABELS[key]
    return _pick(lang, ko, en)


def note_text(lang: Lang, note: EtaNote) -> str:
    ko, en = _NOTE[note]
    return _pick(lang, ko, en)


def pool_note(lang: Lang, pool_gb: int) -> str:
    return _pick(
        lang,
        f"통합풀 ~{pool_gb}GB (GTT+host 이 안이어야 안전)",
        f"unified pool ~{pool_gb}GB (GTT+host must fit)",
    )


def _quant_str(done, total) -> str:
    # bash greps the literal "N/M Linear 양자화" and prints it verbatim in both langs.
    return f"{done}/{total} Linear 양자화"


def smodel(job: JobState) -> str:
    """Eval/scoring run label: --label -> base_label -> unit name -> '?'.

    ``eval_label`` (the eval run's own ``--label``) is preferred: it is the clean,
    standard identifier for the run. base_label is a fallback and can be a truncated
    path basename when ``--base`` points at a directory whose name contains a space.
    """
    return (
        job.model_info.eval_label
        or job.model_info.base_label
        or job.unit_name
        or "?"
    )


def phase_text(lang: Lang, job: JobState) -> str:
    """Render a JobState's phase as the exact bash progress string."""
    p = job.phase
    if p is Phase.IDLE:
        return _pick(lang, "대기 중", "idle")

    if p is Phase.QUANTIZING:
        q = _quant_str(job.quant_done, job.quant_total)
        pct = _pct(job.quant_done, job.quant_total)
        return _pick(lang, f"🔧 양자화  {q}  ({pct}%)", f"🔧 Quantizing {q} ({pct}%)")

    if p is Phase.FIRST_STEP:
        return _pick(
            lang,
            "⚙️ 첫 스텝 연산 중  (양자화 완료, step 1 forward/backward)",
            "⚙️ Computing first step (quant done, step 1 fwd/bwd)",
        )

    if p is Phase.TRAINING:
        loss = job.loss_disp if job.loss_disp is not None else _fmt_num(job.loss)
        sstep = job.sstep_disp if job.sstep_disp is not None else _fmt_num(job.sstep)
        return _pick(
            lang,
            f"🏃 학습  step {job.step}/{job.total}   loss {loss}   {sstep}s/step",
            f"🏃 Training step {job.step}/{job.total} loss {loss} {sstep}s/step",
        )

    if p is Phase.EVAL_SAVE:
        return _pick(
            lang,
            f"💾 평가·저장 중  (step {job.step}/{job.total} 완료, val loss 계산 후 어댑터 저장)",
            f"💾 Eval·save (step {job.step}/{job.total} done, val loss then adapter save)",
        )

    if p is Phase.SCORE_PREP:
        q = _quant_str(job.quant_done, job.quant_total)
        return _pick(lang, f"🔧 채점준비: 양자화 {q}", f"🔧 Scoring prep: quantizing {q}")

    if p is Phase.SCORING:
        # Prefer the cleaned task name (new eval_hard_tsc format) over the verbose
        # verbatim "generated [...]" line; fall back to that, then to "waiting".
        last = job.cur_task or job.last_gen or t(lang, "waiting")
        return _pick(
            lang,
            f"🧮 채점 {smodel(job)} — 생성 {job.gen_done}/{job.heldout_total}, 최근: {last}",
            f"🧮 Scoring {smodel(job)} — generated {job.gen_done}/{job.heldout_total}, last: {last}",
        )

    if p is Phase.FINISHED:
        active = job.unit_active or ""
        res = job.unit_result or ""
        if job.job_type is JobType.SCORE:
            return _pick(lang, f"⏸ 채점 종료 ({active}/{res})", f"⏸ Scoring finished ({active}/{res})")
        return _pick(lang, f"⏸ 종료 ({active}/{res})", f"⏸ Finished ({active}/{res})")

    return _pick(lang, "대기 중", "idle")


# --- small numeric helpers shared with widgets ---------------------------- #
def _pct(done, total) -> str:
    if not done or not total:
        return "0"
    return f"{done / total * 100:.0f}"


def _fmt_num(x) -> str:
    """Fallback float formatting for the JSON path (no verbatim string available)."""
    if x is None:
        return "?"
    if float(x).is_integer():
        return str(int(x))
    return f"{x:g}"
