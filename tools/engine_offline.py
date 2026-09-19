"""Roda o SyncEngine inteiro (spotify_sync.py) sem placa de som, fita nem Spotify:
audio sintetico em tempo real, fita falsa que registra os envios.
Uso:  .venv\\Scripts\\python.exe tools\\engine_offline.py [bpm] [segundos]
"""
import os
import sys
import tempfile
import time
import threading

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spotify_sync as ss  # noqa: E402
import diaglog
import modes  # noqa: E402
import test_dsp as T  # noqa: E402

BPM = float(sys.argv[1]) if len(sys.argv) > 1 else 128.0
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 14.0
SR = 48000


class FakeDevice:
    MUSIC_TRANSITION_JUMP = 0
    MUSIC_TRANSITION_FADE = 1

    def __init__(self):
        self.sends = []

    def set_music_colour(self, tr, r, g, b):
        self.sends.append((ss.clock(), tr == self.MUSIC_TRANSITION_JUMP, (r, g, b)))
        return {}

    def set_colour(self, r, g, b):
        self.sends.append((ss.clock(), False, (r, g, b)))

    def set_mode(self, m):
        pass


STREAM_T0 = [None]


class FakeStream:
    def __init__(self, x, read_size):
        self.x = (x * 32767).astype(np.int16)
        self.i = 0
        self.rs = read_size
        self.t0 = ss.clock()
        STREAM_T0[0] = self.t0

    def get_read_available(self):
        due = int((ss.clock() - self.t0) * SR)
        return max(0, min(due, len(self.x)) - self.i)

    def read(self, n, exception_on_overflow=False):
        ch = self.x[self.i:self.i + n]
        self.i += n
        return ch.tobytes()

    def stop_stream(self):
        pass

    def close(self):
        pass


class FakePyAudio:
    def __init__(self, x, read_size):
        self.x, self.rs = x, read_size

    def get_loopback_device_info_generator(self):
        return iter([])

    def get_default_wasapi_loopback(self):
        return {"name": "FAKE loopback", "defaultSampleRate": SR, "maxInputChannels": 1, "index": 0}

    def open(self, **kw):
        return FakeStream(self.x, self.rs)

    def terminate(self):
        pass


class FakeNowPlaying(threading.Thread):
    def __init__(self, on_change=None, allow_youtube=None):
        super().__init__(daemon=True)
        self.t0 = ss.clock()

    def run(self):
        pass

    def get(self):
        return {"title": "Sintetico", "artist": "-", "album": "-", "duration": SECS}

    def position(self):
        return None, False

    def stop(self):
        pass


def main():
    np.random.seed(7)
    x, beats = T.drum_pattern(BPM, T=SECS)
    dev = FakeDevice()
    beat_sched = []
    _orig_sched = ss.OutputScheduler.schedule

    def spy(self, send_time, r, g, b, jump):
        # agendado no futuro e claro = beat previsto (o piso do modo seco tambem e futuro, mas escuro)
        if send_time - ss.clock() > 0.03 and max(r, g, b) >= 77:
            beat_sched.append(send_time)
        return _orig_sched(self, send_time, r, g, b, jump)
    ss.OutputScheduler.schedule = spy
    ss.load_device_info = lambda path=None: ("id", "ip", "key", "3.3")
    ss.connect_bulb = lambda *a: (dev, True)
    ss.pyaudio.PyAudio = lambda: FakePyAudio(x, int(ss.DEFAULTS["READ_SIZE"]))
    ss.metadata.NowPlaying = FakeNowPlaying
    cfg = dict(ss.DEFAULTS)
    cfg.update({"OUTPUT_DELAY": 0.0, "BULB_LATENCY": 0.0, "LEARN_ENABLE": False,
                "REPLAY_ENABLE": False, "LOOPBACK_DEVICE_MATCH": "",
                "SECTIONS_ENABLE": False})       # nao poluir structures.json com a faixa sintetica
    for kv in sys.argv[3:]:                      # ex.: ALIEN_ENABLE=1 AUTO_ENABLE=0
        k, v = kv.split("=", 1)
        cfg[k] = type(cfg[k])(int(v)) if isinstance(cfg[k], (bool, int)) else type(cfg[k])(v)
    hist = []
    eng = ss.SyncEngine(cfg, status_cb=lambda st: hist.append((ss.clock(), dict(st))),
                        log_cb=lambda m: print("  ", m, flush=True))
    diag_dir = tempfile.mkdtemp(prefix="fita-diag-")
    eng.diag_dir = diag_dir                      # log de diagnostico fora de logs/
    t0 = ss.clock()
    eng.start()
    time.sleep(SECS + 0.5)
    eng.stop()
    err = eng.status.get("error")
    print(f"\nerro do motor: {err!r}")
    bpms = [(t - t0, st["bpm"], st["locked"], st.get("conf", 0)) for t, st in hist]
    for tt, b, l, cf in bpms[::60]:
        print(f"  t={tt:5.1f}s bpm={b:6.1f} locked={l} conf={cf:.2f}")
    st = eng.status
    print(f"final: bpm={st['bpm']:.1f} locked={st['locked']} key={st['key']} warmth={st['warmth']:.2f} "
          f"energy={st['energy']:.2f} vol={st['vol']:.2f} rgb={st['rgb']}")
    # beats enviados (jump=True) x beats reais
    ts0 = STREAM_T0[0] or t0          # o audio comeca quando o stream abre, nao no start()
    beat_sends = sorted(set(round(t - ts0, 4) for t in beat_sched))
    b = np.array(beats)
    errs = [1000 * float(np.min(np.abs(b - t))) for t in beat_sends if t >= 5.0]
    jumps = sum(1 for _, j, _ in dev.sends if j)
    n_real = int(np.sum(b >= 5.0))
    n_pred = len(errs)
    dup = (n_pred / n_real) if n_real else 0.0
    stat = (f"mediana {np.median(errs):.0f} ms, p90 {np.percentile(errs, 90):.0f} ms, n={len(errs)}"
            if errs else "n=0")
    print(f"envios: {len(dev.sends)} total ({jumps} jump), {len(beat_sends)} beats previstos; "
          f"erro p/ beat real (t>=5s): {stat}; previstos/reais {dup:.2f}")
    # pico->piso medido nos envios da fita falsa (mesma regra do diaglog)
    ts = diaglog.TrackStats()
    for t, j, (r, g, bb) in dev.sends:
        ts.send(t, t, 0.0, max(r, g, bb) / 255.0, j)
    for t in beat_sched:
        ts.beat(t, BPM)
    fall = ts.summary()["fall_ms_med"]
    print(f"pico->piso mediana: {fall:.0f} ms")
    # log de diagnostico: 1 arquivo motor-*.jsonl com faixa, beats e envios
    logs = [f for f in os.listdir(diag_dir) if f.startswith("motor-")]
    rep = diaglog.report(os.path.join(diag_dir, logs[0])) if logs else []
    if rep:
        for tr in rep:
            print(f"diag [{tr['title']}]: {diaglog.format_summary(tr['summary'])}; "
                  f"paradas={len(tr['stuck'])}")
    else:
        print("diag: NENHUM log gerado")
    if cfg.get("MEDITATION_ENABLE"):     # sem batida: sucesso = rodou sem erro e sem jump
        return 0 if not err and jumps == 0 and rep else 1
    was_locked = any(s["locked"] for _, s in hist)
    ok = not err and was_locked and bool(rep) and rep[0]["summary"]["beats"] > 0
    ok = ok and dup <= 1.2          # 1 soco por batida (grade realinhada nao pode duplicar)
    if cfg["FAST_BPM"] > 0 and st["bpm"] >= cfg["FAST_BPM"] and not modes.ColorFX(cfg).calm:   # PICANTE/REGGAE nunca seco
        ok = ok and fall <= cfg["FAST_HOLD"] * 1000 + 40
    if cfg.get("REGGAE_ENABLE"):    # paleta rasta (azul nunca domina), piso alto, so o soco salta
        late = [(j, rgb) for t, j, rgb in dev.sends if t - ts0 >= 5.0]
        rasta = all(bb <= g + 2 and bb <= r + 2 for _, (r, g, bb) in late)
        vmin = min(max(rgb) / 255.0 for _, rgb in late) if late else 0.0
        n_beats = rep[0]["summary"]["beats"] if rep else 0     # socos agendados (o spy so conta os fortes)
        print(f"reggae: rasta={rasta} V min={vmin:.2f} (piso {cfg['REGGAE_FLOOR']}*vol {st['vol']:.2f}) "
              f"jumps={jumps} beats={n_beats}")
        ok = ok and rasta and vmin >= 0.8 * cfg["REGGAE_FLOOR"] * st["vol"] and jumps <= n_beats + 1
    if cfg.get("SPICY_ENABLE"):     # faixa vermelha (verde nunca domina), piso >= 0,4*vol, so o soco salta
        late = [(j, rgb) for t, j, rgb in dev.sends if t - ts0 >= 5.0]
        red = all(r >= g and r >= bb for _, (r, g, bb) in late)
        vmin = min(max(rgb) / 255.0 for _, rgb in late) if late else 0.0
        vmax = max(max(rgb) / 255.0 for _, rgb in late) if late else 0.0
        n_beats = rep[0]["summary"]["beats"] if rep else 0
        print(f"spicy: red={red} V min={vmin:.2f} max={vmax:.2f} (vol {st['vol']:.2f}) jumps={jumps} beats={n_beats}")
        ok = ok and red and vmin >= 0.4 * st["vol"] and jumps <= n_beats + 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
