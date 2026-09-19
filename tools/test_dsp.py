"""Testes do nucleo matematico (dsp.py) com sinal sintetico.
Roda:  .venv\\Scripts\\python.exe -m unittest tools.test_dsp -v
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dsp  # noqa: E402

SR = 48000
N = 1024
R = 256
FR = SR / R
CFG = {"IBI_WINDOW": 8, "MIN_IBI": 0.28, "MAX_IBI": 1.0, "LOCK_MIN_SAMPLES": 4,
       "LOCK_TOL": 0.12, "ENABLE_PREDICTION": True, "PHASE_GAIN": 0.2, "TEMPO_LOCK_CONF": 0.25}


def hit(x, t0, f, dur, amp, decay, noise=False):
    s = int(t0 * SR)
    L = int(dur * SR)
    if s + L > len(x):
        return
    n = np.arange(L)
    env = np.exp(-n / (decay * SR))
    if noise:
        x[s:s + L] += amp * env * np.random.randn(L).astype(np.float32)
    else:
        x[s:s + L] += amp * env * np.sin(2 * np.pi * f * n / SR)


def click_track(bpm, T=12.0, offset=0.0):
    P = 60.0 / bpm
    x = 0.01 * np.random.randn(int(T * SR)).astype(np.float32)
    beats = []
    t = offset
    while t < T:
        hit(x, t, 70, 0.08, 0.6, 0.02)
        beats.append(t)
        t += P
    return x, beats


def drum_pattern(bpm, T=14.0, offset=0.1):
    """bumbo 1 e 3, caixa 2 e 4, chimbal em colcheias, pad harmonico continuo."""
    P = 60.0 / bpm
    x = 0.005 * np.random.randn(int(T * SR)).astype(np.float32)
    beats = []
    k = 0
    t = offset
    while t < T:
        if k % 2 == 0:
            hit(x, t, 60, 0.12, 0.7, 0.03)
        else:
            hit(x, t, 200, 0.10, 0.35, 0.02, noise=True)
        hit(x, t, 8000, 0.03, 0.15, 0.005, noise=True)
        hit(x, t + P / 2, 8000, 0.03, 0.12, 0.005, noise=True)
        beats.append(t)
        t += P
        k += 1
    n = np.arange(len(x))
    for f in (220.0, 277.18, 329.63):
        x += (0.05 * np.sin(2 * np.pi * f * n / SR)).astype(np.float32)
    return x, beats


def run_engine(x, eval_every=0.25):
    freqs = np.fft.rfftfreq(N, 1 / SR)
    od = dsp.OnsetDetector(freqs, mu=dsp.OnsetDetector.mu_for(N, R))
    te = dsp.TempoEstimator(FR)
    tr = dsp.BeatTracker(CFG)
    buf = np.zeros(N, np.float32)
    w = np.hanning(N).astype(np.float32)
    onsets, preds, last = [], [], 0.0
    last_pred = None
    for i in range(0, len(x) - R, R):
        ch = x[i:i + R]
        buf = np.concatenate((buf[R:], ch))
        now = (i + R) / SR
        on, st = od.process(np.abs(np.fft.rfft(buf * w)), float(np.sqrt(np.mean(ch * ch))), now)
        te.push(od.odf_full, now)
        if on:
            onsets.append(now)
            tr.update(now, st)
        if now - last >= eval_every:
            last = now
            tr.set_tempo(*te.estimate(tr.next_beat if tr.acf_locked else None))
        if tr.locked:
            fb = tr.next_future_beat(now)
            if fb is not None and fb != last_pred:
                last_pred = fb
                preds.append((now, fb))
    return od, te, tr, onsets, preds


def phase_errors(preds, beats, t_from):
    """erro (ms) de cada beat previsto (previsto >= t_from) ao beat real mais proximo."""
    errs = []
    b = np.array(beats)
    for t_pred_at, fb in preds:
        if fb < t_from:
            continue
        errs.append(1000.0 * float(np.min(np.abs(b - fb))))
    return np.array(errs)


class TestTempo(unittest.TestCase):
    def _check(self, bpm, gen=click_track, tol_bpm=1.5, tol_ms=25.0, expect=None):
        """Faixa do tempograma e 80-160 BPM (BTrack): fora dela o andamento dobra p/ dentro."""
        np.random.seed(1)
        x, beats = gen(bpm)
        od, te, tr, onsets, preds = run_engine(x)
        self.assertTrue(tr.acf_locked, f"{bpm}: nao travou (conf={te.confidence:.2f})")
        self.assertAlmostEqual(tr.bpm(), expect or bpm, delta=tol_bpm, msg=f"{bpm}: bpm={tr.bpm():.1f}")
        if expect and expect > bpm:          # dobrou p/ cima: a grade tem beats no meio (legitimos)
            P = 60.0 / bpm
            beats = sorted(beats + [t + P / 2 for t in beats])
        errs = phase_errors(preds, beats, 4.0)
        self.assertGreater(len(errs), 5)
        self.assertLess(float(np.median(errs)), tol_ms, f"{bpm}: fase mediana {np.median(errs):.0f} ms")

    def test_click_70_folds_to_140(self):
        self._check(70, expect=140)

    def test_click_90(self):
        self._check(90)

    def test_click_120(self):
        self._check(120)

    def test_click_140(self):
        self._check(140)

    def test_click_174_folds_to_87(self):
        self._check(174, expect=87)

    def test_drums_100(self):
        self._check(100, gen=drum_pattern)

    def test_drums_128(self):
        self._check(128, gen=drum_pattern)

    def test_tempo_change(self):
        np.random.seed(2)
        x1, b1 = click_track(100, T=10.0)
        x2, b2 = click_track(130, T=10.0)
        x = np.concatenate((x1, x2))
        od, te, tr, onsets, preds = run_engine(x)
        self.assertAlmostEqual(tr.bpm(), 130, delta=1.5, msg=f"bpm final={tr.bpm():.1f}")
        beats = b1 + [10.0 + t for t in b2]
        errs = phase_errors(preds, beats, 15.0)
        self.assertLess(float(np.median(errs)), 25.0, f"fase apos troca {np.median(errs):.0f} ms")

    def test_no_lock_on_noise(self):
        np.random.seed(3)
        x = 0.05 * np.random.randn(int(8 * SR)).astype(np.float32)
        od, te, tr, onsets, preds = run_engine(x)
        self.assertFalse(tr.acf_locked, f"travou em ruido (conf={te.confidence:.2f})")


class TestOnset(unittest.TestCase):
    def test_clicks_detected(self):
        np.random.seed(4)
        x, beats = click_track(120)
        od, te, tr, onsets, preds = run_engine(x)
        self.assertGreaterEqual(len(onsets), len(beats) - 2)
        self.assertLessEqual(len(onsets), len(beats) + 2)
        b = np.array(beats)
        lat = [1000.0 * (o - b[b <= o + 0.001].max()) for o in onsets if (b <= o + 0.001).any()]
        self.assertLess(float(np.median(lat)), 30.0, f"latencia mediana {np.median(lat):.0f} ms")

    def test_steady_tone_no_onsets(self):
        n = np.arange(int(6 * SR))
        x = (0.3 * np.sin(2 * np.pi * 110 * n / SR)).astype(np.float32)
        od, te, tr, onsets, preds = run_engine(x)
        self.assertLessEqual(len(onsets), 2)


class TestChroma(unittest.TestCase):
    def _chord(self, freqs, T=1.0):
        n = np.arange(int(T * SR))
        x = np.zeros(len(n), np.float32)
        for f in freqs:
            for h in (1, 2, 3):
                x += (0.3 / h * np.sin(2 * np.pi * f * h * n / SR)).astype(np.float32)
        return x

    def test_c_major(self):
        ce = dsp.ChromaExtractor(SR)
        x = self._chord([130.81, 164.81, 196.0, 261.63, 329.63, 392.0])   # C E G
        name, is_major, maj = dsp.detect_key(ce.compute(x))
        self.assertEqual(name, "C maior")
        self.assertGreater(maj, 0.0)

    def test_a_minor(self):
        ce = dsp.ChromaExtractor(SR)
        x = self._chord([110.0, 130.81, 164.81, 220.0, 261.63, 329.63])   # A C E
        name, is_major, maj = dsp.detect_key(ce.compute(x))
        self.assertEqual(name, "A menor")
        self.assertLess(maj, 0.0)

    def test_low_notes_resolved(self):
        """FFT 16384: G3 (196 Hz) e G#3 (208 Hz) caem em classes diferentes."""
        ce = dsp.ChromaExtractor(SR)
        c1 = ce.compute(self._chord([196.0]))
        c2 = ce.compute(self._chord([207.65]))
        self.assertEqual(int(np.argmax(c1)), 7)
        self.assertEqual(int(np.argmax(c2)), 8)


class TestLoudness(unittest.TestCase):
    def test_range(self):
        self.assertEqual(dsp.loudness_db(0.0), 0.0)
        self.assertEqual(dsp.loudness_db(1.0), 1.0)
        self.assertAlmostEqual(dsp.loudness_db(10 ** (-33 / 20)), 0.5, places=3)


if __name__ == "__main__":
    unittest.main()
