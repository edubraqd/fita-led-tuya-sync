"""Testes do log de diagnostico. Rode: .venv\\Scripts\\python.exe -m unittest tools.test_diaglog -v"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import diaglog  # noqa: E402


class TestDiagLog(unittest.TestCase):
    def test_event_written_as_json_line(self):
        with tempfile.TemporaryDirectory() as d:
            lg = diaglog.DiagLog(d)
            lg.event("onset", t=1.5, strength=0.8)
            lg.close()
            files = [f for f in os.listdir(d) if f.startswith("motor-") and f.endswith(".jsonl")]
            self.assertEqual(len(files), 1)
            lines = open(os.path.join(d, files[0]), encoding="utf-8").read().splitlines()
            self.assertEqual(len(lines), 1)
            ev = json.loads(lines[0])
            self.assertEqual(ev["k"], "onset")
            self.assertEqual(ev["t"], 1.5)
            self.assertEqual(ev["strength"], 0.8)

    def test_track_summary_appends_to_faixas(self):
        with tempfile.TemporaryDirectory() as d:
            lg = diaglog.DiagLog(d)
            st = diaglog.TrackStats()
            st.beat(1.0, 128.0)
            lg.track_summary({"title": "T", "artist": "A"}, st, t=2.0)
            lg.close()
            rows = [json.loads(x) for x in open(os.path.join(d, "faixas.jsonl"), encoding="utf-8")]
            self.assertEqual(rows[0]["title"], "T")
            self.assertEqual(rows[0]["beats"], 1)
            self.assertEqual(rows[0]["stuck"], 0)
            self.assertIn("quando", rows[0])
            ev = [json.loads(x) for x in open(lg.path, encoding="utf-8")]
            self.assertEqual(ev[-1]["k"], "summary")

    def test_disabled_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            lg = diaglog.DiagLog(d, enabled=False)
            lg.event("onset", t=1.0)
            lg.close()
            self.assertEqual(os.listdir(d), [])


class TestTrackStats(unittest.TestCase):
    def _fast_track(self, fall):
        """3 socos a 140 BPM; apos cada pico, piso chega `fall` s depois."""
        st = diaglog.TrackStats()
        period = 60.0 / 140
        for i in range(3):
            t = 10 + i * period
            st.beat(t, bpm=140.0)
            st.send(t, target=t - 0.002, dev_ms=8.0, v=1.0, jump=True)
            st.send(t + fall, target=t + fall - 0.002, dev_ms=8.0, v=0.05, jump=True)
        return st

    def test_summary_contrast_and_fall(self):
        s = self._fast_track(0.09).summary()
        self.assertEqual(s["beats"], 3)
        self.assertEqual(s["sends"], 6)
        self.assertAlmostEqual(s["bpm_med"], 140.0)
        self.assertAlmostEqual(s["v_peak_med"], 1.0)
        self.assertAlmostEqual(s["v_floor_med"], 0.05)
        self.assertAlmostEqual(s["fall_ms_med"], 90.0, places=0)
        self.assertAlmostEqual(s["dev_ms_med"], 8.0)
        self.assertAlmostEqual(s["late_ms_med"], 2.0, places=0)

    def test_gate_pct(self):
        st = self._fast_track(0.09)
        st.gate()
        st.gate()
        s = st.summary()
        self.assertEqual(s["gates"], 2)
        self.assertAlmostEqual(s["gate_pct"], 25.0)   # 2 de (6 envios + 2 gates)

    def test_stuck_windows_when_contrast_too_low(self):
        """Luz 'parada': 6 batidas seguidas em que V so oscila 1.0 <-> 0.7 (amplitude < 0.35)."""
        st = diaglog.TrackStats()
        period = 60.0 / 150
        for i in range(6):
            t = 20 + i * period
            st.beat(t, bpm=150.0)
            st.send(t, target=t, dev_ms=5.0, v=1.0, jump=True)
            st.send(t + 0.1, target=t + 0.1, dev_ms=5.0, v=0.7, jump=True)   # nunca cai
        win = st.stuck_windows(min_beats=4)
        self.assertEqual(len(win), 1)
        self.assertAlmostEqual(win[0]["t0"], 20.0, places=1)
        self.assertEqual(win[0]["beats"], 6)
        self.assertAlmostEqual(win[0]["bpm"], 150.0)
        self.assertAlmostEqual(win[0]["contrast"], 0.3, places=2)

    def test_no_stuck_windows_when_contrast_ok(self):
        st = self._fast_track(0.09)
        self.assertEqual(st.stuck_windows(min_beats=2), [])

    def test_calm_track_with_low_absolute_levels_is_not_stuck(self):
        """Piso 0.28 e pico 0.75: niveis baixos mas amplitude 0.47 -> pisca, nao esta parada."""
        st = diaglog.TrackStats()
        for i in range(6):
            t = 5 + i * 0.6
            st.beat(t, bpm=100.0)
            st.send(t, target=t, dev_ms=5.0, v=0.75, jump=True)
            st.send(t + 0.2, target=t + 0.2, dev_ms=5.0, v=0.28, jump=False)
        self.assertEqual(st.stuck_windows(min_beats=4), [])
        s = st.summary()
        self.assertAlmostEqual(s["v_peak_med"], 0.75)
        self.assertAlmostEqual(s["v_floor_med"], 0.28)
        self.assertAlmostEqual(s["fall_ms_med"], 200.0, places=0)

    def test_empty_summary_does_not_crash(self):
        s = diaglog.TrackStats().summary()
        self.assertEqual(s["beats"], 0)
        self.assertEqual(s["fall_ms_med"], 0.0)


class TestReport(unittest.TestCase):
    def test_report_groups_by_track(self):
        with tempfile.TemporaryDirectory() as d:
            lg = diaglog.DiagLog(d)
            lg.event("track", t=0.0, title="A", artist="x")
            for i in range(5):
                t = 1 + i * 0.4
                lg.event("beat", t=t, bpm=150.0)
                lg.event("send", t=t, target=t, dev_ms=5.0, v=1.0, jump=True)
                lg.event("send", t=t + 0.1, target=t + 0.1, dev_ms=5.0, v=0.7, jump=True)
            lg.event("track", t=5.0, title="B", artist="y")
            lg.event("beat", t=6.0, bpm=90.0)
            lg.event("send", t=6.0, target=6.0, dev_ms=5.0, v=1.0, jump=True)
            lg.event("send", t=6.2, target=6.2, dev_ms=5.0, v=0.1, jump=False)
            lg.close()
            rep = diaglog.report(lg.path)
            self.assertEqual([r["title"] for r in rep], ["A", "B"])
            self.assertEqual(rep[0]["summary"]["beats"], 5)
            self.assertEqual(len(rep[0]["stuck"]), 1)
            self.assertEqual(rep[1]["stuck"], [])


if __name__ == "__main__":
    unittest.main()
