"""Log de diagnostico do motor: o que a fita recebeu, quando, e o que ficou pelo caminho.

- DiagLog: 1 evento JSON por linha em logs/motor-AAAAMMDD.jsonl (buffer em memoria,
  flush a cada ~1 s; seguro entre a thread do motor e a do scheduler).
- TrackStats: acumula por faixa (batidas, envios, gates, latencias, pico/piso) e
  aponta janelas em que a luz "ficou parada" (varias batidas sem voltar ao piso).
- report(path): le um .jsonl e devolve resumo + janelas paradas por faixa.
"""
import json
import os
import threading
import time
from datetime import datetime

import numpy as np

FLUSH_SECS = 1.0
MIN_CONTRAST = 0.35   # amplitude de V dentro de uma batida abaixo disso = luz "parada"
FALL_FRAC = 0.2       # pico->piso: tempo ate V descer a min + 20% da amplitude


class DiagLog:
    def __init__(self, dir_path, enabled=True):
        self.enabled = enabled
        self.path = None
        self._buf = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        if enabled:
            os.makedirs(dir_path, exist_ok=True)
            self.path = os.path.join(dir_path, "motor-%s.jsonl" % datetime.now().strftime("%Y%m%d"))

    def event(self, kind, t=None, **fields):
        if not self.enabled:
            return
        ev = {"k": kind, "t": round(t, 4) if t is not None else None}
        ev.update(fields)
        line = json.dumps(ev, ensure_ascii=False, default=_json_default)
        with self._lock:
            self._buf.append(line)
            due = time.monotonic() - self._last_flush >= FLUSH_SECS
        if due:
            self.flush()

    def flush(self):
        with self._lock:
            lines, self._buf = self._buf, []
            self._last_flush = time.monotonic()
        if lines and self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

    def track_summary(self, info, stats, t=None):
        """Fecha uma faixa: evento 'summary' no log do dia + 1 linha em faixas.jsonl. Devolve o resumo."""
        s = stats.summary()
        s["stuck"] = len(stats.stuck_windows())
        if not self.enabled:
            return s
        info = info or {}
        rec = {"quando": datetime.now().isoformat(timespec="seconds"),
               "title": info.get("title", ""), "artist": info.get("artist", "")}
        rec.update(s)
        self.event("summary", t=t, **rec)
        self.flush()
        with open(os.path.join(os.path.dirname(self.path), "faixas.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=_json_default) + "\n")
        return s

    def close(self):
        self.flush()


def _json_default(o):
    if isinstance(o, (np.floating, np.integer, np.bool_)):
        return o.item()
    return str(o)


class TrackStats:
    """Contadores de uma faixa. Tempos em s (mesmo relogio dos eventos)."""

    def __init__(self):
        self.beats = []        # (t, bpm)
        self.sends = []        # (t, target, dev_ms, v, jump)
        self.gates = 0
        self.errors = 0

    def beat(self, t, bpm=0.0):
        self.beats.append((t, bpm))

    def send(self, t, target, dev_ms, v, jump):
        self.sends.append((t, target, dev_ms, v, jump))

    def gate(self):
        self.gates += 1

    def error(self):
        self.errors += 1

    def _intervals(self):
        """Por batida: (t, bpm, vmax, vmin, fall_ms) dos envios em [batida, proxima batida).
        A ultima batida usa o periodo mediano como fim. Batida sem envio -> None."""
        if not self.beats:
            return []
        bts = sorted(self.beats)
        ts = np.array([t for t, _, _, _, _ in self.sends])
        vs = np.array([v for _, _, _, v, _ in self.sends])
        order = np.argsort(ts)
        ts, vs = ts[order], vs[order]
        gaps = [b[0] - a[0] for a, b in zip(bts, bts[1:])]
        period = float(np.median(gaps)) if gaps else 1.0
        out = []
        for i, (t, bpm) in enumerate(bts):
            t_end = bts[i + 1][0] if i + 1 < len(bts) else t + period
            m = (ts >= t) & (ts < t_end)
            if not np.any(m):
                out.append(None)
                continue
            seg_t, seg_v = ts[m], vs[m]
            k = int(np.argmax(seg_v))
            vmax, vmin = float(seg_v[k]), float(np.min(seg_v))
            fall = None
            thr = vmin + FALL_FRAC * (vmax - vmin)
            for tt, vv in zip(seg_t[k:], seg_v[k:]):
                if vv <= thr:
                    fall = 1000.0 * (tt - seg_t[k])
                    break
            out.append((t, bpm, vmax, vmin, fall))
        return out

    def summary(self):
        def med(xs):
            return float(np.median(xs)) if len(xs) else 0.0
        bpms = [b for _, b in self.beats if b > 0]
        iv = [x for x in self._intervals() if x]
        span = (self.sends[-1][0] - self.sends[0][0]) if len(self.sends) > 1 else 0.0
        n_frames = len(self.sends) + self.gates
        return {
            "beats": len(self.beats),
            "bpm_med": med(bpms),
            "sends": len(self.sends),
            "sends_per_s": (len(self.sends) / span) if span > 0 else 0.0,
            "jumps": sum(1 for s in self.sends if s[4]),
            "gates": self.gates,
            "gate_pct": (100.0 * self.gates / n_frames) if n_frames else 0.0,
            "late_ms_med": med([1000.0 * (t - tg) for t, tg, _, _, _ in self.sends]),
            "dev_ms_med": med([d for _, _, d, _, _ in self.sends]),
            "v_peak_med": med([x[2] for x in iv]),
            "v_floor_med": med([x[3] for x in iv]),
            "contrast": med([x[2] - x[3] for x in iv]),
            "fall_ms_med": med([x[4] for x in iv if x[4] is not None]),
            "errors": self.errors,
        }

    def stuck_windows(self, min_beats=4, min_contrast=MIN_CONTRAST):
        """Sequencias de >= min_beats batidas seguidas com amplitude de V < min_contrast."""
        wins, run = [], []
        for x in self._intervals():
            if x is not None and (x[2] - x[3]) < min_contrast:
                run.append(x)
                continue
            if len(run) >= min_beats:
                wins.append(self._win(run))
            run = []
        if len(run) >= min_beats:
            wins.append(self._win(run))
        return wins

    @staticmethod
    def _win(run):
        return {"t0": run[0][0], "t1": run[-1][0], "beats": len(run),
                "bpm": float(np.median([x[1] for x in run])),
                "contrast": float(np.median([x[2] - x[3] for x in run]))}


def format_summary(s):
    return ("bpm %.0f | %d batidas | %d envios (%.1f/s, %d jump) | gates %.0f%% | "
            "atraso %.0f ms | fita %.0f ms | V pico %.2f piso %.2f (contraste %.2f) | "
            "pico->piso %.0f ms | erros %d" % (
                s["bpm_med"], s["beats"], s["sends"], s["sends_per_s"], s["jumps"], s["gate_pct"],
                s["late_ms_med"], s["dev_ms_med"], s["v_peak_med"], s["v_floor_med"], s["contrast"],
                s["fall_ms_med"], s["errors"]) +
            (" | PARADA em %d trecho(s)" % s["stuck"] if s.get("stuck") else ""))


def report(path, min_beats=4, min_contrast=MIN_CONTRAST):
    """Agrupa um motor-*.jsonl por faixa: [{title, artist, t0, summary, stuck}]."""
    tracks = []
    cur = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            k = ev.get("k")
            if k == "track":
                cur = {"title": ev.get("title", "?"), "artist": ev.get("artist", ""),
                       "t0": ev.get("t"), "stats": TrackStats()}
                tracks.append(cur)
                continue
            if cur is None:
                cur = {"title": "(sem faixa)", "artist": "", "t0": ev.get("t"), "stats": TrackStats()}
                tracks.append(cur)
            st = cur["stats"]
            if k == "beat":
                st.beat(ev["t"], ev.get("bpm", 0.0))
            elif k == "send":
                st.send(ev["t"], ev.get("target", ev["t"]), ev.get("dev_ms", 0.0),
                        ev.get("v", 0.0), bool(ev.get("jump")))
            elif k == "gate":
                st.gate()
            elif k == "error":
                st.error()
    out = []
    for tr in tracks:
        st = tr.pop("stats")
        tr["summary"] = st.summary()
        tr["stuck"] = st.stuck_windows(min_beats=min_beats, min_contrast=min_contrast)
        out.append(tr)
    return out
