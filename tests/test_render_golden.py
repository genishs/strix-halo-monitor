"""Golden-file render parity tests (DESIGN §4.1 test_render_golden).

The two full-frame tests reproduce real ``legacy/monitor.sh`` output captured live
from the box (byte-for-byte). The rest pin individual phases / edge cases. A fixed
``localtime`` is injected so wall-clock slots are timezone-independent.
"""

import time
import unittest

import _util  # noqa: F401

from halo_monitor.config import config_from_env
from halo_monitor.model import (
    ClockStats, EtaNote, JobState, JobType, MemoryStats, ModelInfo, Phase,
    PowerStats, Snapshot,
)
from halo_monitor.ui.render import render_frame

GIB = 1073741824
CFG_KO = config_from_env(env={})
CFG_EN = config_from_env(env={"HALO_LANG": "en"})


def fixed_lt(h, m, s):
    return lambda t: time.struct_time((2026, 7, 18, h, m, s, 4, 199, -1))


def frame(snap, cfg, lt):
    return render_frame(snap, cfg, localtime=lt).split("\n")


class TestFullFrameParity(unittest.TestCase):
    """Byte-exact reproduction of live-captured bash frames (quantization phase)."""

    def _quant_snap(self, gtt_gib, pct_unused, vram, ram, swap, rate, cpu, gpu, tot, sclk, elapsed):
        mi = ModelInfo(base_label="Qwen2.5 72B", nbits=4, seq=1024, lora_r=16,
                       lora_mlp=True, epochs=1)
        job = JobState(job_type=JobType.TRAIN, phase=Phase.QUANTIZING, model_info=mi,
                       quant_done=60, quant_total=560, eta_note=EtaNote.PRE_TRAINING_PREP,
                       elapsed_seconds=elapsed, error_count=0, unit_active="active")
        return Snapshot(ts=0.0, title="Strix Halo Train/Score Monitor", gfx="gfx1151", job=job,
                        memory=MemoryStats(gtt_used_bytes=int(gtt_gib * GIB),
                                           gtt_total_bytes=60129542144,
                                           vram_used_bytes=int(vram * GIB),
                                           ram_free_gb=ram, swap_used_gb=swap,
                                           gtt_rate_mb_s=rate),
                        power=PowerStats(cpu_w=cpu, gpu_w=gpu, total_w=tot),
                        clocks=ClockStats(sclk_mhz=sclk))

    def test_ko_frame(self):
        snap = self._quant_snap(15.1, 27, 0.8, 34.7, 1.7, 1540.0, 1.0, 37.0, 38.0, 2002, 174)
        expected = [
            '╔══════════════════ Strix Halo Train/Score Monitor (gfx1151) ══════════════════╗',
            '  진행:  🔧 양자화  60/560 Linear 양자화  (11%)',
            '  경과:  0h02m54s         완료예상: — (남은 학습 전 준비단계)     오류: 0',
            '  모델:  Qwen2.5 72B · HQQ 4bit · seq1024 · LoRA r16+mlp · 1ep',
            '  ──────────────────────────────── 통합메모리 ────────────────────────────────',
            '  ★GTT(모델):   15.1 / 56GB  [█████░░░░░░░░░░░░░░░] 27%   증가 +1540 MB/s',
            '   전용VRAM:     0.8GB (nvtop이 보는 값)     통합풀 ~60GB (GTT+host 이 안이어야 안전)',
            '   host RAM여유: 34.7GB ✓     swap: 1.7GB',
            '  ─────────────────────────────── 전력 · GPU ─────────────────────────────────',
            '   ★전력:  CPU   1W  │  GPU  37W  │  전체  38W    (GPU=전체−CPU 근사, RAPL)',
            '   sclk: 2002Mhz      유닛: active',
            '╚═══════════════════════════════ 14:28:54 · Ctrl-C 종료 ═══════════════════════════════╝',
        ]
        self.assertEqual(frame(snap, CFG_KO, fixed_lt(14, 28, 54)), expected)

    def test_en_frame(self):
        snap = self._quant_snap(10.7, 19, 0.8, 39.2, 1.7, 39.0, 1.0, 45.0, 47.0, 1857, 179)
        expected = [
            '╔══════════════════ Strix Halo Train/Score Monitor (gfx1151) ══════════════════╗',
            '  Progress:  🔧 Quantizing 60/560 Linear 양자화 (11%)',
            '  Elapsed:  0h02m59s         ETA: — (remaining pre-training prep)     errors: 0',
            '  Model:  Qwen2.5 72B · HQQ 4bit · seq1024 · LoRA r16+mlp · 1ep',
            '  ──────────────────────────────── Unified Memory ────────────────────────────────',
            '  ★GTT(model):   10.7 / 56GB  [███░░░░░░░░░░░░░░░░░] 19%   rate +39 MB/s',
            '   VRAM(ded.):     0.8GB (what nvtop sees)     unified pool ~60GB (GTT+host must fit)',
            '   host RAM free: 39.2GB ✓     swap: 1.7GB',
            '  ─────────────────────────────── Power · GPU ─────────────────────────────────',
            '   ★Power:  CPU   1W  │  GPU  45W  │  Total  47W    (GPU=Total−CPU approx, RAPL)',
            '   sclk: 1857Mhz      unit: active',
            '╚═══════════════════════════════ 14:28:59 · Ctrl-C to quit ═══════════════════════════════╝',
        ]
        self.assertEqual(frame(snap, CFG_EN, fixed_lt(14, 28, 59)), expected)


def _base_snap(job):
    return Snapshot(ts=1000.0, title="Strix Halo Train/Score Monitor", gfx="gfx1151",
                    job=job, memory=MemoryStats(), power=PowerStats(), clocks=ClockStats())


class TestPhaseLines(unittest.TestCase):
    def test_training_preserves_loss_disp_precision(self):
        # verbatim "1.9119" must survive — a float round-trip would show "1.9119"->ok
        # but "0.60" must NOT collapse to "0.6".
        job = JobState(job_type=JobType.TRAIN, phase=Phase.TRAINING, step=18, total=39,
                       loss=0.6, loss_disp="0.60", sstep=12.5, sstep_disp="12.5",
                       eta_seconds=262, elapsed_seconds=600, unit_active="active")
        line = frame(_base_snap(job), CFG_KO, fixed_lt(14, 0, 0))[1]
        self.assertEqual(line, "  진행:  🏃 학습  step 18/39   loss 0.60   12.5s/step")

    def test_training_eta_hms_and_donetime(self):
        job = JobState(job_type=JobType.TRAIN, phase=Phase.TRAINING, step=18, total=39,
                       loss_disp="0.60", sstep_disp="12.5", eta_seconds=262,
                       elapsed_seconds=600, unit_active="active")
        lt = fixed_lt(14, 4, 22)  # arbitrary; donetime uses it
        eline = frame(_base_snap(job), CFG_KO, lt)[2]
        # eta = hms(262) = 0h04m22s ; donetime = 14:04 (from injected localtime)
        self.assertIn("(남은 0h04m22s)", eline)
        self.assertIn("완료예상: 14:04 ", eline)

    def test_scoring_smodel_from_label(self):
        job = JobState(job_type=JobType.SCORE, phase=Phase.SCORING, gen_done=5,
                       heldout_total=7, last_gen="generated [t3]",
                       model_info=ModelInfo(base_label="Mistral-Large 123B"),
                       eta_seconds=2880, eta_note=EtaNote.ROUGH_HIGH_VARIANCE,
                       unit_active="active", unit_name="gpujob-score-123b")
        line = frame(_base_snap(job), CFG_KO, fixed_lt(0, 0, 0))[1]
        self.assertEqual(
            line,
            "  진행:  🧮 채점 Mistral-Large 123B — 생성 5/7, 최근: generated [t3]",
        )

    def test_scoring_smodel_falls_back_to_unit_name(self):
        job = JobState(job_type=JobType.SCORE, phase=Phase.SCORING, gen_done=0,
                       heldout_total=7, model_info=ModelInfo(),
                       eta_note=EtaNote.ESTIMATING_FIRST_TASK,
                       unit_active="active", unit_name="gpujob-score-x")
        line = frame(_base_snap(job), CFG_EN, fixed_lt(0, 0, 0))[1]
        self.assertEqual(
            line,
            "  Progress:  🧮 Scoring gpujob-score-x — generated 0/7, last: waiting",
        )

    def test_eval_save(self):
        job = JobState(job_type=JobType.TRAIN, phase=Phase.EVAL_SAVE, step=39, total=39,
                       eta_note=EtaNote.BEYOND_STEP_COUNT, unit_active="active")
        line = frame(_base_snap(job), CFG_KO, fixed_lt(0, 0, 0))[1]
        self.assertEqual(
            line,
            "  진행:  💾 평가·저장 중  (step 39/39 완료, val loss 계산 후 어댑터 저장)",
        )

    def test_finished_train_vs_score(self):
        jt = JobState(job_type=JobType.TRAIN, phase=Phase.FINISHED,
                      unit_active="inactive", unit_result="success")
        js = JobState(job_type=JobType.SCORE, phase=Phase.FINISHED,
                      unit_active="failed", unit_result="exit-code")
        self.assertEqual(frame(_base_snap(jt), CFG_KO, fixed_lt(0, 0, 0))[1],
                         "  진행:  ⏸ 종료 (inactive/success)")
        self.assertEqual(frame(_base_snap(js), CFG_EN, fixed_lt(0, 0, 0))[1],
                         "  Progress:  ⏸ Scoring finished (failed/exit-code)")

    def test_first_step_and_idle(self):
        fs = JobState(job_type=JobType.TRAIN, phase=Phase.FIRST_STEP, total=120,
                      eta_note=EtaNote.FIRST_STEP_WARMUP, unit_active="active")
        self.assertEqual(frame(_base_snap(fs), CFG_KO, fixed_lt(0, 0, 0))[1],
                         "  진행:  ⚙️ 첫 스텝 연산 중  (양자화 완료, step 1 forward/backward)")
        self.assertEqual(frame(_base_snap(None), CFG_KO, fixed_lt(0, 0, 0))[1],
                         "  진행:  대기 중")


class TestEdgeCases(unittest.TestCase):
    def test_unknown_values_render_question_mark(self):
        lines = frame(_base_snap(None), CFG_KO, fixed_lt(12, 0, 0))
        self.assertIn("? / ?GB", lines[5])                    # gtt unknown
        self.assertIn("CPU   ?W", lines[9])                   # power unknown
        self.assertIn("sclk: ?Mhz", lines[10])                # sclk unknown
        self.assertIn("유닛: ?", lines[10])                    # active unknown

    def test_ram_low_flag(self):
        snap = _base_snap(JobState(job_type=JobType.TRAIN))
        snap.memory.ram_free_gb = 2.4
        self.assertIn("2.4GB ⚠️위험", frame(snap, CFG_KO, fixed_lt(0, 0, 0))[7])
        snap.memory.ram_free_gb = 8.0
        self.assertIn("8.0GB ✓", frame(snap, CFG_KO, fixed_lt(0, 0, 0))[7])

    def test_power_separator_is_asymmetric(self):
        line = frame(_base_snap(None), CFG_KO, fixed_lt(0, 0, 0))[8]
        self.assertEqual(line.count("─"), 31 + 33)            # bash source: 31 left, 33 right

    def test_gpu_busy_appended_to_gtt_line_when_present(self):
        snap = _base_snap(JobState(job_type=JobType.TRAIN))
        snap.memory.gpu_busy_pct = 62
        gtt = frame(snap, CFG_KO, fixed_lt(0, 0, 0))[5]        # GTT is line index 5
        self.assertTrue(gtt.rstrip().endswith("GPU 사용 62%"))
        self.assertIn("GPU busy 62%", frame(snap, CFG_EN, fixed_lt(0, 0, 0))[5])

    def test_gpu_busy_absent_leaves_gtt_line_and_frame_unchanged(self):
        # gpu_busy_pct None (default) -> GTT line has no GPU-busy suffix and the frame
        # stays the legacy 12 lines (additive: the byte-parity goldens are unaffected).
        lines = frame(_base_snap(None), CFG_KO, fixed_lt(12, 0, 0))
        self.assertEqual(len(lines), 12)
        self.assertNotIn("GPU 사용", lines[5])


if __name__ == "__main__":
    unittest.main()
