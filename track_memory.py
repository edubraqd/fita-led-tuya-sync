#!/usr/bin/env python3
"""
track_memory.py — Memoria de faixa: aprende a musica pelo PC, toca no Echo
==========================================================================
Quando o Spotify toca NESTE PC o motor ouve o loopback e sabe onde cada beat
cai. Quando toca na Alexa (Connect) o PC nao ouve nada — mas o Spotify desktop
continua publicando titulo + posicao da faixa (SMTC), tambem no Connect.

Entao: na 1a vez que a musica toca pelo PC, gravamos a timeline (onsets e as
curvas de cor/energia/volume ja suavizadas) indexada pela POSICAO da faixa.
Na proxima vez, tocando onde for, o motor le a posicao e reproduz a timeline
— sem microfone (que ouviria a sala e a voz) e sem API do Spotify.

Arquivo por faixa em learned/<artista - titulo>.json. Tocar de novo pelo PC
preenche trechos que faltavam (a curva guarda None onde nunca ouviu).
"""

import os
import re
import json
import bisect
import threading

LEARN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learned")
CURVE_DT = 0.05                 # 20 amostras/s das curvas
FIELDS = ("hue", "sat", "energy", "vol", "warmth")


def track_key(info):
    """Nome de arquivo estavel a partir de artista + titulo."""
    raw = f"{info.get('artist', '')} - {info.get('title', '')}".strip(" -").lower()
    raw = re.sub(r"[^\w\s\-]", "", raw, flags=re.UNICODE)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:120] or "sem-titulo"


def track_path(info):
    return os.path.join(LEARN_DIR, track_key(info) + ".json")


def has_memory(info):
    return os.path.exists(track_path(info))


class Recorder:
    """Grava onsets e curvas de uma faixa enquanto o loopback esta tocando."""

    def __init__(self, info, duration):
        self.info = {k: info.get(k, "") for k in ("title", "artist", "album")}
        self.path = track_path(info)
        self._lock = threading.Lock()
        self.dirty = False
        n = int(max(duration, 1.0) / CURVE_DT) + 2
        self.data = {"artist": self.info["artist"], "title": self.info["title"],
                     "album": self.info["album"], "duration": float(duration),
                     "dt": CURVE_DT, "onsets": [],
                     "curve": {f: [None] * n for f in FIELDS}}
        self._old_onsets = []       # do arquivo; os do trecho re-ouvido sao trocados
        self._new_onsets = []       # desta sessao
        old = load_json(self.path)
        if old and old.get("dt") == CURVE_DT:
            self._old_onsets = list(old.get("onsets", []))
            for f in FIELDS:
                c = old.get("curve", {}).get(f)
                if c:
                    self.data["curve"][f] = (c + [None] * n)[:n]
        # trecho coberto nesta sessao: onsets antigos aqui dentro sao trocados pelos novos
        self._covered = []          # lista de [ini, fim]
        self._cur_range = None

    def _cover(self, pos):
        r = self._cur_range
        if r is not None and pos - r[1] < 1.5 and pos >= r[0]:
            r[1] = pos
        else:
            self._cur_range = [pos, pos]
            self._covered.append(self._cur_range)

    def sample(self, pos, **feats):
        if pos is None or pos < 0:
            return
        i = int(pos / CURVE_DT)
        with self._lock:
            curve = self.data["curve"]
            if i >= len(curve["hue"]):
                return
            for f in FIELDS:
                curve[f][i] = round(float(feats[f]), 3)
            self._cover(pos)
            self.dirty = True

    def onset(self, pos, strength):
        if pos is None or pos < 0:
            return
        with self._lock:
            self._new_onsets.append([round(float(pos), 3), round(float(strength), 2)])
            self.dirty = True

    def save(self):
        with self._lock:
            if not self.dirty:
                return False
            # onsets antigos dentro do que ouvimos agora saem; ficam os de fora
            kept = [o for o in self._old_onsets
                    if not any(a - 0.2 <= o[0] <= b + 0.2 for a, b in self._covered)]
            self.data["onsets"] = sorted(kept + self._new_onsets)
            os.makedirs(LEARN_DIR, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.dirty = False
            return True

    def coverage(self):
        """Fracao da faixa com curva gravada."""
        c = self.data["curve"]["hue"]
        return sum(1 for v in c if v is not None) / max(1, len(c))


class Player:
    """Le a timeline aprendida por posicao."""

    def __init__(self, path):
        self.path = path
        d = load_json(path) or {}
        self.ok = bool(d) and "curve" in d
        self.dt = float(d.get("dt", CURVE_DT))
        self.duration = float(d.get("duration", 0.0))
        self.onsets = sorted(d.get("onsets", []))
        self._onset_t = [o[0] for o in self.onsets]
        self.curve = d.get("curve", {f: [] for f in FIELDS})
        self.n_onsets = len(self.onsets)

    def onsets_between(self, a, b):
        """Onsets com a < t <= b, como [(t, forca), ...]."""
        i = bisect.bisect_right(self._onset_t, a)
        j = bisect.bisect_right(self._onset_t, b)
        return [(o[0], o[1]) for o in self.onsets[i:j]]

    def features_at(self, pos):
        """Curvas interpoladas na posicao; None se esse trecho nao foi aprendido."""
        if pos is None or pos < 0:
            return None
        x = pos / self.dt
        i = int(x)
        fr = x - i
        out = {}
        for f in FIELDS:
            c = self.curve.get(f) or []
            if i >= len(c):
                return None
            a = c[i]
            b = c[i + 1] if i + 1 < len(c) else None
            if a is None and b is None:
                return None
            if a is None:
                v = b
            elif b is None:
                v = a
            elif f == "hue" and abs(b - a) > 0.5:      # volta do circulo de matiz
                v = a                                   # sem interpolar pelo lado errado
            else:
                v = a + (b - a) * fr
            out[f] = float(v)
        return out

    def coverage(self):
        c = self.curve.get("hue") or []
        return sum(1 for v in c if v is not None) / max(1, len(c))

    def bpm_near(self, pos, window=4.0):
        """BPM local pela mediana dos intervalos entre onsets em volta da posicao."""
        ts = [t for t, _ in self.onsets_between(pos - window, pos + window)]
        ibis = sorted(b - a for a, b in zip(ts, ts[1:]) if 0.25 <= b - a <= 1.0)
        if len(ibis) < 3:
            return 0.0
        return 60.0 / ibis[len(ibis) // 2]


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_learned():
    if not os.path.isdir(LEARN_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(LEARN_DIR)):
        if fn.endswith(".json"):
            d = load_json(os.path.join(LEARN_DIR, fn)) or {}
            out.append({"file": fn, "artist": d.get("artist", ""), "title": d.get("title", ""),
                        "onsets": len(d.get("onsets", [])),
                        "coverage": Player(os.path.join(LEARN_DIR, fn)).coverage()})
    return out
