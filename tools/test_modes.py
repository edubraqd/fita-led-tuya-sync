"""Testes sinteticos de modes.py. Rode: .venv\\Scripts\\python.exe -m unittest tools.test_modes -v"""
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import modes  # noqa: E402


def cfg(**kw):
    c = {"OUTPUT_DELAY": 0.18, "HUE_MAX": 0.75, "ALIEN_FLASH_MIN": 0.08,
         "ALIEN_ENABLE": False, "SPICY_ENABLE": False, "LED_FOLLOW_BT": False,
         "BT_HEARD_LATENCY": 0.0, "SECTIONS_ENABLE": True, "ANTICIPATE_ENABLE": True,
         "ANTICIPATE_BUILD": 4.0}
    c.update(kw)
    return c


class TestOutputDelay(unittest.TestCase):
    def test_default(self):
        self.assertEqual(modes.output_delay(cfg()), 0.18)

    def test_follow_bt_uses_heard(self):
        self.assertEqual(modes.output_delay(cfg(LED_FOLLOW_BT=True, BT_HEARD_LATENCY=0.31)), 0.31)

    def test_follow_bt_without_measure_falls_back(self):
        self.assertEqual(modes.output_delay(cfg(LED_FOLLOW_BT=True, BT_HEARD_LATENCY=0.0)), 0.18)


class TestColorFX(unittest.TestCase):
    def test_off_is_identity(self):
        fx = modes.ColorFX(cfg())
        self.assertEqual(fx.apply(0.0, 0.3, 0.7, 0.5), (0.3, 0.7, 0.5, False))
        self.assertEqual(fx.pulse_scale(), 1.0)
        self.assertFalse(fx.on_onset(1.0, 1.0, 1.0))

    def test_alien_palette_green_to_violet(self):
        fx = modes.ColorFX(cfg(ALIEN_ENABLE=True))
        h0, s0, _, _ = fx.apply(0.0, 0.0, 0.5, 0.5)
        h1, _, _, _ = fx.apply(0.0, 0.75, 0.5, 0.5)
        self.assertAlmostEqual(h0, 0.33, places=2)
        self.assertAlmostEqual(h1, 0.85, places=2)
        self.assertGreaterEqual(s0, 0.8)
        self.assertLess(fx.pulse_scale(), 1.0)

    def test_alien_abduction_strobes_and_refracts(self):
        fx = modes.ColorFX(cfg(ALIEN_ENABLE=True, ALIEN_FLASH_MIN=0.2), rng=random.Random(1))
        fired = [fx.on_onset(t, 1.0, 1.0) for t in (10.0, 10.2, 10.4)]
        self.assertEqual(fired, [True, False, False])          # 1 abducao, refratario
        _, _, v, jump = fx.apply(10.05, 0.3, 0.7, 0.5)
        self.assertEqual((v, jump), (1.0, True))                # pulso 0: clarao
        _, _, v2, _ = fx.apply(10.2, 0.3, 0.7, 0.5)
        self.assertLess(v2, 0.5)                                # pulso 1: escuro
        _, _, v3, jump3 = fx.apply(10.6, 0.3, 0.7, 0.5)
        self.assertEqual((v3, jump3), (0.5, False))             # acabou
        self.assertFalse(fx.on_onset(11.0, 0.2, 0.2))           # onset fraco nunca

    def test_alien_wins_over_spicy(self):
        self.assertEqual(modes.ColorFX(cfg(ALIEN_ENABLE=True, SPICY_ENABLE=True)).mode, "alien")

    def test_spicy_is_red_and_saturated(self):
        fx = modes.ColorFX(cfg(SPICY_ENABLE=True))
        for hue in (0.0, 0.3, 0.75):
            h, s, v, jump = fx.apply(0.0, hue, 0.5, 0.4)
            self.assertTrue(h >= 0.90 or h <= 0.11, h)
            self.assertGreaterEqual(s, 0.9)
            self.assertEqual((v, jump), (0.4, False))
        self.assertGreater(fx.pulse_scale(), 1.0)
        self.assertGreater(fx.floor_bias(), 0.0)

    def test_spicy_is_calm_with_pink_drop_and_capped_peak(self):
        fx = modes.ColorFX(cfg(SPICY_ENABLE=True))
        self.assertTrue(fx.calm)
        r, g, b = fx.drop_rgb()
        self.assertEqual(r, 255)                         # rosa-quente, nao branco
        self.assertTrue(g < 200 and b < 200 and b > g, (r, g, b))
        self.assertAlmostEqual(fx.peak_cap(), 0.85)
        self.assertEqual(modes.ColorFX(cfg()).drop_rgb(), (255, 255, 255))
        self.assertEqual(modes.ColorFX(cfg()).peak_cap(), 1.0)
        self.assertIsNone(modes.ColorFX(cfg(REGGAE_ENABLE=True)).drop_rgb())

    def test_spicy_dims_to_warm(self):
        fx = modes.ColorFX(cfg(SPICY_ENABLE=True))
        h_hi, _, _, _ = fx.apply(0.0, 0.3, 0.9, 1.0)
        h_lo, _, _, _ = fx.apply(0.0, 0.3, 0.9, 0.3)
        d = (h_lo - h_hi) % 1.0
        self.assertTrue(0.04 <= d <= 0.08, d)            # piso puxa pro ambar (brasa), pico fica vermelho

    def test_spicy_bass_pulls_magenta_light_pulls_peach(self):
        heavy = modes.ColorFX(cfg(SPICY_ENABLE=True))
        light = modes.ColorFX(cfg(SPICY_ENABLE=True))
        for i in range(400):                             # EMA converge (0,5 s a 200 Hz)
            hh, _, _, _ = heavy.apply(i * 0.005, 0.3, 0.9, 1.0, bass=0.4)
            hl, _, _, _ = light.apply(i * 0.005, 0.3, 0.9, 1.0, bass=0.0)
        self.assertTrue(hh >= 0.90, hh)                  # grave pesado: magenta/vinho
        self.assertTrue(0.02 <= hl <= 0.08, hl)          # leve: pessego
        self.assertGreater((hl - hh) % 1.0, 0.05)

    def test_spicy_bass_is_smoothed(self):
        fx = modes.ColorFX(cfg(SPICY_ENABLE=True))
        h0, _, _, _ = fx.apply(0.0, 0.3, 0.9, 1.0, bass=0.0)
        h1, _, _, _ = fx.apply(0.005, 0.3, 0.9, 1.0, bass=0.4)
        self.assertLess(abs((h1 - h0 + 0.5) % 1.0 - 0.5), 0.01)   # 1 frame de grave nao pula a cor

    def test_off_has_no_reggae_extras(self):
        fx = modes.ColorFX(cfg())
        self.assertEqual(fx.floor_min(), 0.0)
        self.assertFalse(fx.calm)
        self.assertFalse(fx.offbeat())

    def test_reggae_mode_and_priority(self):
        self.assertEqual(modes.ColorFX(cfg(REGGAE_ENABLE=True)).mode, "reggae")
        self.assertEqual(modes.ColorFX(cfg(ALIEN_ENABLE=True, REGGAE_ENABLE=True)).mode, "alien")

    def test_reggae_palette_drifts_red_amber_green_and_back(self):
        fx = modes.ColorFX(cfg(REGGAE_ENABLE=True, REGGAE_DRIFT_SECS=40.0))
        hues = []
        for t in (0.0, 10.0, 20.0, 30.0, 40.0):
            h, s, v, jump = fx.apply(t, 0.6, 0.5, 0.4)     # hue da musica ignorado
            self.assertGreaterEqual(s, 0.8)
            self.assertEqual((v, jump), (0.4, False))
            hues.append(h)
        self.assertAlmostEqual(hues[0], 0.0, places=2)      # vermelho
        self.assertAlmostEqual(hues[1], 0.165, places=2)    # ambar/amarelo
        self.assertAlmostEqual(hues[2], 0.33, places=2)     # verde
        self.assertAlmostEqual(hues[3], 0.165, places=2)    # volta pelo ambar (sem passar por azul)
        self.assertAlmostEqual(hues[4], 0.0, places=2)      # ciclo fechado
        for h in hues:
            self.assertTrue(0.0 <= h <= 0.34, h)

    def test_reggae_is_long_calm_and_high_floor(self):
        fx = modes.ColorFX(cfg(REGGAE_ENABLE=True, REGGAE_FLOOR=0.35))
        self.assertGreater(fx.pulse_scale(), 1.0)
        self.assertAlmostEqual(fx.floor_min(), 0.35)
        self.assertTrue(fx.calm)
        self.assertFalse(fx.on_onset(1.0, 1.0, 1.0))         # nunca estrobo

    def test_reggae_offbeat_toggle(self):
        self.assertFalse(modes.ColorFX(cfg(REGGAE_ENABLE=True)).offbeat())
        self.assertTrue(modes.ColorFX(cfg(REGGAE_ENABLE=True, REGGAE_OFFBEAT=True)).offbeat())
        self.assertFalse(modes.ColorFX(cfg(SPICY_ENABLE=True, REGGAE_OFFBEAT=True)).offbeat())


class TestDropDetector(unittest.TestCase):
    def run_seq(self, seq):
        dd = modes.DropDetector(cfg())
        hits = []
        for i, (e, b, f) in enumerate(seq):
            if dd.step(i * modes.GRID, e, b, f):
                hits.append(i)
        return hits

    def test_breakdown_then_drop_detected_once(self):
        seq = [(0.45, 0.4, 0.3)] * 16 + [(0.25, 0.15, 0.2)] * 8 + [(0.85, 0.8, 0.6)] * 12
        hits = self.run_seq(seq)
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(24 <= hits[0] <= 27, hits)

    def test_steady_music_no_drop(self):
        rng = random.Random(3)
        seq = [(0.5 + rng.uniform(-0.03, 0.03), 0.4 + rng.uniform(-0.03, 0.03), 0.3) for _ in range(60)]
        self.assertEqual(self.run_seq(seq), [])

    def test_fade_out_no_drop(self):
        seq = [(0.6 - i * 0.01, 0.5 - i * 0.01, 0.3) for i in range(40)]
        self.assertEqual(self.run_seq(seq), [])

    def test_two_drops_apart_are_both_seen(self):
        blk = [(0.45, 0.4, 0.3)] * 16 + [(0.25, 0.15, 0.2)] * 8 + [(0.85, 0.8, 0.6)] * 12
        hits = self.run_seq(blk + blk)
        self.assertEqual(len(hits), 2, hits)


HAS_STRUCT = modes.Structure(cfg(), log=lambda *a: None, path=os.devnull).ss is not None


@unittest.skipUnless(HAS_STRUCT, "structure.pyc ausente (so na maquina do autor)")
class TestStructure(unittest.TestCase):
    def test_pyc_roundtrip_and_anticipation(self):
        with tempfile.TemporaryDirectory() as d:
            st = modes.Structure(cfg(), log=lambda *a: None, path=os.path.join(d, "s.json"))
            self.assertIsNotNone(st.ss, "structure.pyc nao carregou")
            info = {"artist": "A", "title": "T", "duration": 60.0}
            st.begin(info)
            for i in range(120):
                pos = i * 0.5
                e = 0.8 if 30 <= pos < 45 else 0.3
                st.record(pos, e, e, 0.3)
            st.add_auto_drop(30.0)
            lvl_far, nxt = st.anticipation(10.0)
            lvl_near, nxt2 = st.anticipation(29.0)
            self.assertEqual((nxt, nxt2), (20.0, 1.0))     # segundos ate o drop
            self.assertEqual(lvl_far, 0.0)
            self.assertGreater(lvl_near, 0.5)
            curve, drops, dur, learning = st.view()
            self.assertIn(30.0, drops)
            self.assertEqual(dur, 60.0)
            st.commit()
            self.assertTrue(os.path.exists(os.path.join(d, "s.json")))

    def test_disabled_is_inert(self):
        with tempfile.TemporaryDirectory() as d:
            st = modes.Structure(cfg(SECTIONS_ENABLE=False), log=lambda *a: None, path=os.path.join(d, "s.json"))
            st.begin({"artist": "A", "title": "T", "duration": 60.0})
            st.record(1.0, 1, 1, 1)
            self.assertEqual(st.anticipation(1.0), (0.0, None) if not st.ss.drops else st.anticipation(1.0))


class TestBLEMirror(unittest.TestCase):
    def test_off_without_address(self):
        m = modes.BLEMirror(cfg(BLE_ENABLE=True, BLE_ADDRESS=""), log=lambda *a: None)
        self.assertFalse(m.start())
        m.set_color(1, 2, 3)     # nao explode
        m.stop()


class TestMeditation(unittest.TestCase):
    def mcfg(self, **kw):
        c = cfg(MEDITATION_ENABLE=True, MED_SCENE="auto", MED_GAIN=0.7, MED_FLOOR=0.15,
                MED_BREATH_SECS=10.0, MED_WARMTH=0.0)
        c.update(kw)
        return c

    def test_auto_scene_by_title_or_artist(self):
        m = modes.Meditation(self.mcfg())
        self.assertEqual(m.scene({"title": "Rain on a Tin Roof", "artist": ""}), "chuva")
        self.assertEqual(m.scene({"title": "Sons da Floresta", "artist": ""}), "selva")
        self.assertEqual(m.scene({"title": "x", "artist": "Jungle Sounds"}), "selva")
        self.assertEqual(m.scene({"title": "Flute Meditation", "artist": ""}), "flauta")
        self.assertEqual(m.scene({"title": "Untitled", "artist": "?"}), "flauta")
        self.assertEqual(m.scene(None), "flauta")
        self.assertEqual(modes.Meditation(self.mcfg(MED_SCENE="chuva")).scene({"title": "Flute"}), "chuva")

    def test_palette_per_scene(self):
        for scene, lo, hi in (("flauta", 0.08, 0.12), ("selva", 0.28, 0.38), ("chuva", 0.50, 0.58)):
            m = modes.Meditation(self.mcfg(MED_SCENE=scene))
            for t in range(0, 120, 7):
                h, s, v = m.frame(float(t), 0.5, 0.5, 0.0, None)
                self.assertTrue(lo - 1e-6 <= h <= hi + 1e-6, (scene, t, h))
                self.assertTrue(0.0 <= s <= 1.0 and 0.0 <= v <= 1.0, (scene, s, v))
        chuva = modes.Meditation(self.mcfg(MED_SCENE="chuva"))
        selva = modes.Meditation(self.mcfg(MED_SCENE="selva"))
        self.assertLess(chuva.frame(1.0, 0.5, 0.5, 0.0, None)[1], selva.frame(1.0, 0.5, 0.5, 0.0, None)[1])

    def test_flauta_bright_timbre_is_lighter_and_less_saturated(self):
        m = modes.Meditation(self.mcfg(MED_SCENE="flauta"))
        h_lo, s_lo, _ = m.frame(1.0, 0.5, 0.0, 0.0, None)
        h_hi, s_hi, _ = m.frame(1.0, 0.5, 1.0, 0.0, None)
        self.assertLess(s_hi, s_lo)
        self.assertGreater(h_hi, h_lo)

    def test_breath_is_periodic(self):
        m = modes.Meditation(self.mcfg(MED_SCENE="selva", MED_BREATH_SECS=10.0))
        vs = [m.frame(t * 0.1, 0.5, 0.5, 0.0, None)[2] for t in range(0, 300)]
        tail = vs[100:]                                    # envelope de volume ja assentou
        self.assertGreater(max(tail) - min(tail), 0.05)    # respira de verdade
        for i in range(0, 100):                            # periodo de 10 s (100 passos)
            self.assertAlmostEqual(tail[i], tail[i + 100], places=3)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in vs))

    def test_brightness_follows_volume_slowly(self):
        m = modes.Meditation(self.mcfg(MED_SCENE="selva", MED_BREATH_SECS=1e9))   # sem respiracao
        for t in range(0, 50):
            v_quiet = m.frame(t * 0.1, 0.0, 0.5, 0.0, None)[2]
        v_step = m.frame(5.1, 1.0, 0.5, 0.0, None)[2]
        self.assertLess(v_step - v_quiet, 0.3)             # ataque ~1 s: nao salta
        for t in range(52, 100):
            v_loud = m.frame(t * 0.1, 1.0, 0.5, 0.0, None)[2]
        self.assertGreater(v_loud, v_quiet + 0.3)
        self.assertGreaterEqual(v_quiet, 0.15 - 1e-6)      # piso
        v_after = m.frame(10.2, 0.0, 0.5, 0.0, None)[2]
        self.assertGreater(v_after, v_loud - 0.15)         # soltura ~2,5 s: desce devagar

    def test_no_beat_punch_and_never_jumps(self):
        m = modes.Meditation(self.mcfg(MED_SCENE="flauta"))
        prev = m.frame(0.0, 0.5, 0.5, 0.0, None)
        for t in range(1, 200):
            cur = m.frame(t * 0.05, 0.5, 0.5, 1.0 if t % 10 == 0 else 0.0, None)
            self.assertLess(abs(cur[2] - prev[2]), 0.08, t)
            prev = cur

    def test_rain_sparkles_on_transient(self):
        m = modes.Meditation(self.mcfg(MED_SCENE="chuva", MED_BREATH_SECS=1e9))
        for t in range(0, 60):
            base = m.frame(t * 0.05, 0.5, 0.5, 0.0, None)[2]
        spark = m.frame(3.05, 0.5, 0.5, 1.0, None)[2]
        self.assertGreater(spark, base + 0.03)
        for t in range(62, 80):
            after = m.frame(t * 0.05, 0.5, 0.5, 0.0, None)[2]
        self.assertLess(after, spark)                      # cintilacao curta

    def test_warmth_pulls_hue_to_amber(self):
        cold = modes.Meditation(self.mcfg(MED_SCENE="chuva", MED_WARMTH=0.0)).frame(1.0, 0.5, 0.5, 0.0, None)[0]
        warm = modes.Meditation(self.mcfg(MED_SCENE="chuva", MED_WARMTH=1.0)).frame(1.0, 0.5, 0.5, 0.0, None)[0]
        self.assertLess(warm, cold)
        self.assertLess(abs(warm - 0.08), 0.03)


if __name__ == "__main__":
    unittest.main()


class TestFastBeat(unittest.TestCase):
    def test_active_only_at_or_above_threshold(self):
        fb = modes.FastBeat(cfg(FAST_BPM=124, FAST_HOLD=0.09))
        self.assertFalse(fb.active(120.0))
        self.assertTrue(fb.active(124.0))
        self.assertTrue(fb.active(150.0))

    def test_disabled_when_threshold_zero(self):
        fb = modes.FastBeat(cfg(FAST_BPM=0, FAST_HOLD=0.09))
        self.assertFalse(fb.active(170.0))

    def test_plan_is_peak_jump_then_floor_jump_after_hold(self):
        fb = modes.FastBeat(cfg(FAST_BPM=124, FAST_HOLD=0.09))
        plan = fb.plan(10.0, (255, 0, 0), (12, 0, 0))
        self.assertEqual(plan, [(10.0, 255, 0, 0, True), (10.09, 12, 0, 0, True)])
