#!/usr/bin/env python3
"""
Spotify / PC Audio Sync para Fita LED Tuya  —  v6 (motor + hub)
===============================================================
Motor de sincronizacao reutilizavel (classe SyncEngine) + CLI.

Cromoterapia + beat preditivo + onset SuperFlux + scheduler de precisao.
Config vem de um dict (config.json), editavel AO VIVO pelo hub (hub.py).

Uso CLI:   python spotify_sync.py
Uso Hub:   python hub.py   (interface grafica)
"""

import os
import sys
import json
import time
import math
import heapq
import colorsys
import threading
from collections import deque

import numpy as np
import pyaudiowpatch as pyaudio
import tinytuya

import metadata
import track_memory

clock = time.perf_counter
EPS = 1e-9
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DEVICES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devices.json")

# =============================================================
#  CONFIG PADRAO  (o hub edita estes valores em config.json)
# =============================================================
DEFAULTS = {
    # Captura / timing
    "READ_SIZE": 256, "FFT_SIZE": 1024, "MIN_UPDATE_INTERVAL": 0.07,
    # Latencia
    "BULB_LATENCY": 0.06, "OUTPUT_DELAY": 0.18, "ENABLE_PREDICTION": True,
    # Onset (SuperFlux-lite)
    "ONSET_LOW_HZ": 20, "ONSET_HIGH_HZ": 200, "ODF_THRESH_K": 1.6,
    "ODF_HISTORY": 200, "ONSET_RMS_GATE": 0.005, "REFRACTORY": 0.12,
    # Beat tracker
    "MIN_IBI": 0.28, "MAX_IBI": 1.00, "LOCK_TOL": 0.12, "PHASE_GAIN": 0.20,
    "IBI_WINDOW": 8, "LOCK_MIN_SAMPLES": 4,
    # Cromoterapia
    "CENTROID_LOW_HZ": 150.0, "CENTROID_HIGH_HZ": 5000.0, "HUE_MAX": 0.75,
    "HUE_SMOOTH": 0.25, "CENTROID_MIN_HZ": 30.0, "CENTROID_MAX_HZ": 16000.0,
    # Dinamica
    "RMS_GAIN": 4.0, "RMS_REF": 0.15, "ENERGY_SMOOTH": 0.04, "ENERGY_FLASH_TH": 0.55,
    "FLOOR_CALM": 0.28, "FLOOR_ENERGETIC": 0.05, "PEAK_WEAK": 0.65, "PEAK_STRONG": 1.00,
    "PULSE_FRACTION": 0.60, "PULSE_MIN": 0.12, "PULSE_MAX": 0.70, "SAT_ENERGY_BOOST": 0.15,
    # Volume mestre (intensidade): silencio->apaga, alto->forte
    "VOL_GATE": 0.004, "VOL_REF": 0.18, "VOL_ATTACK": 0.5, "VOL_RELEASE": 0.08,
    "VOL_GAMMA": 0.7, "SAT_VOL_DUCK": 0.30,
    # Saturacao
    "SAT_MIN": 0.65, "SAT_MAX": 1.00, "SAT_SMOOTH": 0.25,
    # Envio
    "SEND_DH": 2, "SEND_DS": 3, "SEND_DV": 2,
    # Roteamento
    "LOOPBACK_DEVICE_MATCH": "Echo Dot", "SILENCE_WARN_SECS": 4.0,
    # Memoria de faixa: aprende pelo loopback, reproduz pela posicao (Spotify Connect na Alexa)
    "LEARN_ENABLE": True, "REPLAY_ENABLE": True,
    # Referencia musical: so o Spotify. True = navegador (YouTube) tambem conta
    "YOUTUBE_ENABLE": False,
    "REPLAY_OFFSET": 0.0,          # s; + atrasa a luz, - adianta (acerte no olho tocando no Echo)
    "REPLAY_SILENCE_SECS": 1.5,    # loopback mudo por tanto tempo -> tenta o replay
    # Mood (cromoterapia inteligente: maior/alegre=quente, menor/triste=frio)
    "MOOD_ENABLE": True, "MOOD_INFLUENCE": 0.45, "MOOD_SMOOTH": 0.012,
    "WARM_ANCHOR_HUE": 0.05, "COOL_ANCHOR_HUE": 0.60,
    "CHROMA_MIN_HZ": 55.0, "CHROMA_MAX_HZ": 5000.0, "CHROMA_SMOOTH": 0.05,
    # Calibracao por webcam
    "CAM_INDEX": 0, "CAL_TRIALS": 12, "CAM_W": 320, "CAM_H": 240, "CAM_FPS": 60,
    "CAMERA_LATENCY": 0.0, "CAMERA_TRANSPORT": 0.012,   # readout (sub-frame ja tira a quantizacao)
    # Calibracao do Bluetooth (mic)
    "PULSE_COUNT": 20, "MIC_LATENCY": 0.03, "MIC_INDEX": -1,   # -1 = mic padrao
    "CAL_SPACING": 0.45,   # espacamento dos beats no track de calibracao (s)
}

# Perfis de tonalidade Krumhansl-Schmuckler (deteccao maior/menor local)
PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_KMAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KMIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_KMAJ_c = _KMAJ - _KMAJ.mean(); _KMAJ_n = np.linalg.norm(_KMAJ_c) + 1e-9
_KMIN_c = _KMIN - _KMIN.mean(); _KMIN_n = np.linalg.norm(_KMIN_c) + 1e-9


def detect_key(chroma):
    """Correlaciona o chromagrama com perfis maior/menor (12 rotacoes).
    Retorna (nome_tom, is_major, majorness[-1..1])."""
    if float(np.sum(chroma)) < 1e-6:
        return "-", True, 0.0
    best_maj = (-9e9, 0)
    best_min = (-9e9, 0)
    for t in range(12):
        rot = np.roll(chroma, -t)
        rc = rot - rot.mean()
        rn = np.linalg.norm(rc) + 1e-9
        cmaj = float(np.dot(rc, _KMAJ_c) / (rn * _KMAJ_n))
        cmin = float(np.dot(rc, _KMIN_c) / (rn * _KMIN_n))
        if cmaj > best_maj[0]:
            best_maj = (cmaj, t)
        if cmin > best_min[0]:
            best_min = (cmin, t)
    if best_maj[0] >= best_min[0]:
        name = f"{PITCH_NAMES[best_maj[1]]} maior"
        is_major = True
    else:
        name = f"{PITCH_NAMES[best_min[1]]} menor"
        is_major = False
    majorness = max(-1.0, min(1.0, (best_maj[0] - best_min[0]) * 3.0))
    return name, is_major, majorness


def load_config(path=CONFIG_PATH):
    """Retorna DEFAULTS mesclado com config.json (se existir)."""
    cfg = dict(DEFAULTS)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if k in cfg:
                    cfg[k] = v
    except Exception as e:
        print(f"[!] config.json invalido ({e}); usando padrao.")
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def load_device_info(path=DEVICES_PATH):
    if not os.path.exists(path):
        raise FileNotFoundError("devices.json nao encontrado. Rode fetch_key.py.")
    with open(path, "r", encoding="utf-8") as f:
        devices = json.load(f)
    if not devices or not isinstance(devices, list):
        raise ValueError("formato do devices.json invalido.")
    dev = devices[0]
    return dev["id"], dev["ip"], dev["key"], dev.get("ver", "3.3")


def list_loopback_devices():
    """Lista os nomes das saidas loopback (p/ o dropdown do hub)."""
    names = []
    p = pyaudio.PyAudio()
    try:
        for d in p.get_loopback_device_info_generator():
            names.append(d["name"])
    except Exception:
        pass
    finally:
        p.terminate()
    return names


def connect_bulb(dev_id, ip, key, ver):
    device = tinytuya.BulbDevice(dev_id, ip, key, version=float(ver), persist=True)
    status = device.status()
    if isinstance(status, dict) and "Error" in status:
        raise RuntimeError(f"falha na conexao: {status['Error']}")
    use_music = True
    try:
        device.set_mode("music")
        device.status()
        if not device.bulb_has_capability("music"):
            use_music = False
    except Exception:
        use_music = False
    if not use_music:
        try:
            device.set_mode("colour")
        except Exception:
            pass
    device.set_socketPersistent(True)
    device.set_socketNODELAY(True)
    device.set_sendWait(None)
    device.set_retry(False)
    return device, use_music


def quick_command(action, rgb=None):
    """Comando manual one-shot (usado pelo hub quando o motor esta parado)."""
    dev_id, ip, key, ver = load_device_info()
    device = tinytuya.BulbDevice(dev_id, ip, key, version=float(ver), persist=False)
    device.set_socketTimeout(5)
    try:
        if action == "on":
            device.turn_on()
        elif action == "off":
            device.turn_off()
        elif action == "white":
            device.set_mode("white"); device.set_white_percentage(80, 0)
        elif action == "colour" and rgb:
            device.set_mode("colour"); device.set_colour(rgb[0], rgb[1], rgb[2])
    finally:
        device.close()


class BeatTracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ibis = deque(maxlen=int(cfg["IBI_WINDOW"]))
        self.last_onset = None
        self.period = None
        self.locked = False
        self.next_beat = None

    def bpm(self):
        return (60.0 / self.period) if self.period else 0.0

    def update(self, t):
        c = self.cfg
        if self.last_onset is not None:
            ibi = t - self.last_onset
            accept = True
            if self.period:
                ratio = ibi / self.period
                if ratio < 0.6:
                    accept = False
                else:
                    ibi = ibi / max(1, round(ratio))
            if accept and (c["MIN_IBI"] <= ibi <= c["MAX_IBI"]):
                self.ibis.append(ibi)
        self.last_onset = t
        self._recompute()
        if self.period:
            if self.next_beat is None:
                self.next_beat = t + self.period
            elif self.locked:
                err = t - (self.next_beat - self.period)
                if abs(err) < self.period * 0.35:
                    self.next_beat += c["PHASE_GAIN"] * err
                else:
                    self.next_beat = t + self.period
            else:
                self.next_beat = t + self.period

    def _recompute(self):
        c = self.cfg
        if len(self.ibis) >= int(c["LOCK_MIN_SAMPLES"]):
            arr = sorted(self.ibis)
            n = len(arr)
            period = arr[n // 2] if n % 2 else 0.5 * (arr[n // 2 - 1] + arr[n // 2])
            mean = sum(self.ibis) / n
            std = (sum((x - mean) ** 2 for x in self.ibis) / n) ** 0.5
            self.period = period
            self.locked = bool(c["ENABLE_PREDICTION"]) and period > 0 and (std / period) < c["LOCK_TOL"]
        else:
            self.locked = False

    def next_future_beat(self, now):
        if not self.locked or self.next_beat is None or not self.period:
            return None
        while now >= self.next_beat:
            self.next_beat += self.period
        return self.next_beat


class OutputScheduler(threading.Thread):
    """Envia cor no timestamp EXATO (perf_counter): sleep hi-res + spin final."""

    def __init__(self, send_fn):
        super().__init__(daemon=True)
        self.send_fn = send_fn
        self.cv = threading.Condition()
        self.heap = []
        self.seq = 0
        self.running = True

    def schedule(self, send_time, r, g, b, jump):
        with self.cv:
            heapq.heappush(self.heap, (send_time, self.seq, r, g, b, jump))
            self.seq += 1
            self.cv.notify()

    def stop(self):
        with self.cv:
            self.running = False
            self.cv.notify()

    def run(self):
        while True:
            with self.cv:
                while self.running and not self.heap:
                    self.cv.wait()
                if not self.running:
                    return
                target = self.heap[0][0]
                rem = target - clock()
                if rem > 0.05:
                    self.cv.wait(timeout=rem - 0.04)
                    continue
                item = heapq.heappop(self.heap)
            target = item[0]
            while True:
                rem = target - clock()
                if rem <= 0:
                    break
                if rem > 0.0012:
                    time.sleep(rem - 0.0010)
            try:
                self.send_fn(item[2], item[3], item[4], item[5])
            except Exception:
                pass


class SyncEngine:
    """Motor de sincronizacao. start()/stop(); status ao vivo via status_cb."""

    def __init__(self, cfg, status_cb=None, log_cb=print):
        self.cfg = cfg
        self.status_cb = status_cb
        self.log = log_cb
        self._stop = threading.Event()
        self._thread = None
        self._track_changed = False
        self.status = {"running": False, "connected": False, "mode": "-",
                       "device": "-", "locked": False, "bpm": 0.0, "energy": 0.0,
                       "rgb": (0, 0, 0), "centroid": 0.0, "silence": False, "error": "",
                       "song": "", "artist": "", "album": "", "key": "-", "warmth": 0.0, "vol": 0.0,
                       "source": "-", "learned": False, "pos": 0.0}
        self._track_info = None

    def _on_track(self, info):
        self._track_info = info
        self._track_changed = True
        self.log(f"[♪] Tocando: {info['artist']} - {info['title']}")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def is_running(self):
        return bool(self._thread and self._thread.is_alive())

    def _emit(self):
        if self.status_cb:
            try:
                self.status_cb(dict(self.status))
            except Exception:
                pass

    def _run(self):
        c = self.cfg
        try:
            dev_id, ip, key, ver = load_device_info()
        except Exception as e:
            self.status["error"] = str(e)
            self.log(f"[-] {e}")
            self._emit()
            return
        self.log("[*] Conectando a fita...")
        try:
            device, use_music = connect_bulb(dev_id, ip, key, ver)
        except Exception as e:
            self.status["error"] = str(e)
            self.log(f"[-] {e}")
            self._emit()
            return
        self.status.update(running=True, connected=True,
                           mode=("music" if use_music else "colour"), error="")
        self.log(f"[+] Conectado! Modo: {self.status['mode']}")

        JUMP = device.MUSIC_TRANSITION_JUMP
        FADE = device.MUSIC_TRANSITION_FADE
        sm = {"on": use_music}

        def device_send(r, g, b, jump):
            if sm["on"]:
                res = device.set_music_colour(JUMP if jump else FADE, r, g, b)
                if isinstance(res, dict) and "Error" in res:
                    sm["on"] = False
                    try:
                        device.set_mode("colour")
                    except Exception:
                        pass
            if not sm["on"]:
                device.set_colour(r, g, b)

        sched = OutputScheduler(device_send)
        sched.start()
        poller = metadata.NowPlaying(on_change=self._on_track,
                                     allow_youtube=lambda: bool(c.get("YOUTUBE_ENABLE")))
        poller.start()

        p = pyaudio.PyAudio()
        stream = None
        try:
            match = c["LOOPBACK_DEVICE_MATCH"].strip().lower()
            loop = None
            if match:
                try:
                    for d in p.get_loopback_device_info_generator():
                        if match in d["name"].lower():
                            loop = d
                            break
                except Exception:
                    loop = None
            if loop is None:
                loop = p.get_default_wasapi_loopback()
            self.status["device"] = loop["name"]
            self.log(f"[+] Captura: {loop['name']}")
            sample_rate = int(loop["defaultSampleRate"])
            channels = loop["maxInputChannels"]

            FFT_SIZE = int(c["FFT_SIZE"])
            READ_SIZE = int(c["READ_SIZE"])
            window = np.hanning(FFT_SIZE).astype(np.float32)
            freqs = np.fft.rfftfreq(FFT_SIZE, d=1.0 / sample_rate)
            onset_idx = np.where((freqs >= c["ONSET_LOW_HZ"]) & (freqs <= c["ONSET_HIGH_HZ"]))[0]
            cen_idx = np.where((freqs >= c["CENTROID_MIN_HZ"]) & (freqs <= c["CENTROID_MAX_HZ"]))[0]
            cen_freqs = freqs[cen_idx]
            _midi = 69.0 + 12.0 * np.log2(np.maximum(freqs, 1.0) / 440.0)
            _pc = np.mod(np.round(_midi).astype(int), 12)
            _cmask = (freqs >= c["CHROMA_MIN_HZ"]) & (freqs <= c["CHROMA_MAX_HZ"])
            chroma_bins = np.where(_cmask)[0]
            chroma_pc = _pc[chroma_bins]

            stream = p.open(format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                            input=True, input_device_index=loop["index"], frames_per_buffer=READ_SIZE)

            tracker = BeatTracker(c)
            audio_buf = np.zeros(FFT_SIZE, dtype=np.float32)
            prev_spec = np.zeros(len(freqs), dtype=np.float32)
            odf_hist = deque(maxlen=int(c["ODF_HISTORY"]))
            hue, sat, value, centroid = 0.0, 1.0, c["FLOOR_CALM"], 0.0
            energy = 0.3
            flux_mean = EPS
            prev_odf, prev_thr = 0.0, 0.0
            onset_strength = 0.0
            last_onset_accepted = 0.0
            last_sched_beat = None
            last_sent = (-99, -99, -99)
            last_idle = 0.0
            last_now = clock()
            last_audio_time = last_now
            last_emit = 0.0
            silence_warned = False
            chroma_ema = np.zeros(12, dtype=np.float64)
            warmth = 0.0
            majorness = 0.0
            key_name = "-"
            last_key_eval = 0.0
            vol = 0.0
            beat_env = 0.0

            # Memoria de faixa (aprende no loopback / reproduz pela posicao SMTC)
            recorder = None            # track_memory.Recorder da faixa atual
            player = None              # track_memory.Player, se a faixa ja foi aprendida
            replay_active = False
            replay_sched_until = None  # ate que posicao os punches ja foram agendados
            last_pos = None
            pos_hold_until = 0.0       # pos pulou p/ tras (troca de faixa): espera o titulo
            last_save = last_now
            last_curve_i = -1
            self.status["source"] = "loopback"

            def open_track(info):
                nonlocal recorder, player
                if recorder:
                    recorder.save()
                recorder = None
                player = None
                if not info or not info.get("title"):
                    return
                if c["REPLAY_ENABLE"] and track_memory.has_memory(info):
                    pl = track_memory.Player(track_memory.track_path(info))
                    if pl.ok:
                        player = pl
                if c["LEARN_ENABLE"]:
                    dur = float(info.get("duration") or 0.0)
                    recorder = track_memory.Recorder(info, dur if dur > 0 else 600.0)
                self.status["learned"] = player is not None

            while not self._stop.is_set():
                # Loopback mudo (Spotify na Alexa) para de entregar frames: nunca bloqueia no read
                have_audio = stream.get_read_available() >= READ_SIZE
                if have_audio:
                    raw = stream.read(READ_SIZE, exception_on_overflow=False)
                    chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    if channels > 1:
                        chunk = chunk.reshape(-1, channels).mean(axis=1)
                    if len(chunk) < READ_SIZE:
                        chunk = np.pad(chunk, (0, READ_SIZE - len(chunk)))
                    else:
                        chunk = chunk[:READ_SIZE]
                    audio_buf = np.concatenate((audio_buf[READ_SIZE:], chunk))
                    rms = float(np.sqrt(np.mean(chunk * chunk)))
                    spec = np.abs(np.fft.rfft(audio_buf * window))
                else:
                    time.sleep(0.004)
                    rms = 0.0

                now = clock()
                dt = now - last_now
                last_now = now

                if self._track_changed:        # nova musica -> readapta o mood rapido
                    self._track_changed = False
                    chroma_ema[:] = 0.0
                    warmth *= 0.4
                    replay_sched_until = None
                    last_pos = None
                    pos_hold_until = 0.0
                    open_track(self._track_info)

                if rms > 0.002:
                    last_audio_time = now
                    if silence_warned:
                        silence_warned = False
                        self.status["silence"] = False
                elif (now - last_audio_time) > c["SILENCE_WARN_SECS"] and not silence_warned:
                    silence_warned = True
                    self.status["silence"] = True
                    if player is None:
                        self.log("[!] Sem audio no PC. Spotify na Alexa (Connect)? Esta faixa ainda "
                                 "nao foi aprendida: toque-a uma vez neste PC (ou Echo como caixa BT).")

                pos, playing = poller.position()
                # Na troca de faixa o Spotify zera a posicao ~1.5 s antes de trocar o titulo:
                # segura ate o on_change chegar p/ nao gravar/tocar a faixa errada.
                if pos is not None and last_pos is not None and pos < last_pos - 1.0 and pos < 5.0:
                    pos_hold_until = now + 2.5
                last_pos = pos
                if now < pos_hold_until:
                    pos = None
                self.status["pos"] = pos or 0.0

                loop_silent = (now - last_audio_time) > c["REPLAY_SILENCE_SECS"]
                want_replay = bool(c["REPLAY_ENABLE"]) and loop_silent and playing and \
                    player is not None and pos is not None
                self.status["source"] = "replay" if replay_active else ("mudo" if loop_silent else "loopback")
                if want_replay != replay_active:
                    replay_active = want_replay
                    replay_sched_until = None
                    if replay_active:
                        self.log(f"[>] Replay da faixa aprendida ({player.n_onsets} batidas, "
                                 f"{player.coverage()*100:.0f}% coberta). Ajuste REPLAY_OFFSET se a luz "
                                 f"adiantar/atrasar.")
                    else:
                        self.log("[*] Loopback com audio: voltando a ouvir o PC.")

                if not have_audio and not replay_active and not loop_silent:
                    continue        # buraco curto entre pacotes: nao e silencio

                onset = False
                if replay_active:
                    # ---- Replay: onsets e curvas vem da memoria, nao do audio ----
                    feats = player.features_at(pos)
                    if feats:
                        hue, sat, energy, warmth = feats["hue"], feats["sat"], feats["energy"], feats["warmth"]
                        vol = feats["vol"]
                    else:
                        vol += c["VOL_RELEASE"] * (0.0 - vol)     # trecho nao aprendido: apaga
                    comp = c["REPLAY_OFFSET"] - c["BULB_LATENCY"]
                    if replay_sched_until is None or abs(pos - replay_sched_until) > 1.0:   # seek
                        replay_sched_until = pos
                    horizon = pos + 0.6
                    for t_on, strength in player.onsets_between(replay_sched_until, horizon):
                        f2 = player.features_at(t_on) or feats
                        if not f2:
                            continue
                        pk = c["PEAK_WEAK"] + (c["PEAK_STRONG"] - c["PEAK_WEAK"]) * \
                            min(1.0, 0.5 * strength + 0.5 * f2["energy"])
                        base2 = c["FLOOR_CALM"] + (c["FLOOR_ENERGETIC"] - c["FLOOR_CALM"]) * f2["energy"]
                        v2 = max(0.0, min(1.0, f2["vol"] * (base2 + (1.0 - base2) * pk)))
                        s2 = f2["sat"] * (1.0 - c["SAT_VOL_DUCK"] * (1.0 - f2["vol"]))
                        rf, gf, bf = colorsys.hsv_to_rgb(f2["hue"], s2, v2)
                        st = now + (t_on - pos) + comp
                        sched.schedule(max(now, st), int(rf * 255), int(gf * 255), int(bf * 255), True)
                        last_idle = now
                    replay_sched_until = horizon
                    # envelope/status: o onset "acontece" quando a posicao passa por ele
                    hit = player.onsets_between(pos - dt, pos)
                    if hit:
                        onset = True
                        onset_strength = hit[-1][1]
                        tracker.update(now)
                    if now - last_key_eval >= 0.5:
                        last_key_eval = now
                        key_name = "-"
                elif not have_audio:
                    # loopback mudo e nada p/ tocar: so deixa o volume cair (apaga)
                    vol += c["VOL_RELEASE"] * (0.0 - vol)
                    if now - last_emit >= 0.1:
                        last_emit = now
                        meta = poller.get()
                        self.status.update(locked=False, bpm=0.0, vol=vol, song=meta["title"],
                                           artist=meta["artist"], album=meta["album"],
                                           learned=player is not None)
                        self._emit()
                        if vol > 0.01:
                            rf, gf, bf = colorsys.hsv_to_rgb(hue, sat, vol * c["FLOOR_CALM"])
                            sched.schedule(now, int(rf * 255), int(gf * 255), int(bf * 255), False)
                    continue
                else:
                    comp = c["OUTPUT_DELAY"] - c["BULB_LATENCY"]
                    # ODF SuperFlux-lite
                    pm = prev_spec.copy()
                    if len(pm) > 2:
                        pm[1:-1] = np.maximum(prev_spec[1:-1], np.maximum(prev_spec[:-2], prev_spec[2:]))
                    diff = spec - pm
                    odf = float(np.sum(np.maximum(diff[onset_idx], 0.0)))
                    prev_spec = spec
                    odf_hist.append(odf)
                    thr = (sum(odf_hist) / len(odf_hist)) * c["ODF_THRESH_K"] + EPS
                    onset = (odf > thr) and (prev_odf <= prev_thr) and \
                            (now - last_onset_accepted > c["REFRACTORY"]) and (rms > c["ONSET_RMS_GATE"])
                    if onset:
                        onset_strength = min(1.0, max(0.0, (odf / (thr + EPS) - 1.0) / 2.0))
                        last_onset_accepted = now
                        tracker.update(now)
                        if recorder and playing and pos is not None:
                            recorder.onset(pos, onset_strength)
                    prev_odf, prev_thr = odf, thr

                    # Energia
                    activity_flux = float(np.sum(np.maximum(diff, 0.0)))
                    flux_mean += 0.02 * (activity_flux - flux_mean)
                    activity = min(1.0, activity_flux / (2.0 * flux_mean + EPS))
                    loud = min(1.0, rms / c["RMS_REF"])
                    tempo_n = min(1.0, max(0.0, (tracker.bpm() - 70.0) / 80.0)) if tracker.locked else 0.5
                    energy += c["ENERGY_SMOOTH"] * ((0.5 * loud + 0.3 * activity + 0.2 * tempo_n) - energy)

                    # Chromagrama -> tom (maior/menor) -> warmth (mood)
                    chroma_inst = np.zeros(12, dtype=np.float64)
                    np.add.at(chroma_inst, chroma_pc, spec[chroma_bins])
                    cs = float(chroma_inst.sum())
                    if cs > EPS:
                        chroma_inst /= cs
                        chroma_ema += c["CHROMA_SMOOTH"] * (chroma_inst - chroma_ema)
                    if now - last_key_eval >= 0.5:
                        last_key_eval = now
                        key_name, _ismaj, majorness = detect_key(chroma_ema)
                    tempo_n2 = min(1.0, max(0.0, (tracker.bpm() - 70.0) / 80.0)) if tracker.locked else 0.5
                    warmth_t = max(-1.0, min(1.0, 0.5 * majorness + 0.25 * (2 * energy - 1) + 0.25 * (2 * tempo_n2 - 1)))
                    warmth += c["MOOD_SMOOTH"] * (warmth_t - warmth)

                    # Centroide -> matiz (com vies quente/frio do mood) + saturacao
                    cen_mag = spec[cen_idx]
                    mag_sum = float(np.sum(cen_mag))
                    if mag_sum > EPS:
                        centroid = float(np.sum(cen_freqs * cen_mag) / mag_sum)
                        lo, hi = math.log(c["CENTROID_LOW_HZ"]), math.log(c["CENTROID_HIGH_HZ"])
                        norm = min(1.0, max(0.0, (math.log(max(centroid, 1.0)) - lo) / (hi - lo)))
                        hue_c = norm * c["HUE_MAX"]
                        if c["MOOD_ENABLE"]:
                            infl = c["MOOD_INFLUENCE"] * abs(warmth)
                            anchor = c["WARM_ANCHOR_HUE"] if warmth >= 0 else c["COOL_ANCHOR_HUE"]
                            hue_target = hue_c * (1.0 - infl) + anchor * infl
                        else:
                            hue_target = hue_c
                        hue += c["HUE_SMOOTH"] * (hue_target - hue)
                        flat = math.exp(float(np.mean(np.log(cen_mag + EPS)))) / (mag_sum / len(cen_mag) + EPS)
                        tsat = c["SAT_MAX"] - (c["SAT_MAX"] - c["SAT_MIN"]) * min(1.0, flat)
                        tsat = min(1.0, tsat + c["SAT_ENERGY_BOOST"] * energy)
                        sat += c["SAT_SMOOTH"] * (tsat - sat)

                    # VOLUME MESTRE (intensidade): segue o loudness; gate -> apaga no silencio
                    vt = (rms - c["VOL_GATE"]) / max(c["VOL_REF"] - c["VOL_GATE"], 1e-6)
                    vt = max(0.0, min(1.0, vt)) ** c["VOL_GAMMA"]
                    vol += (c["VOL_ATTACK"] if vt > vol else c["VOL_RELEASE"]) * (vt - vol)

                    if recorder and playing and pos is not None and rms > c["ONSET_RMS_GATE"]:
                        ci = int(pos / track_memory.CURVE_DT)
                        if ci != last_curve_i:
                            last_curve_i = ci
                            recorder.sample(pos, hue=hue, sat=sat, energy=energy, vol=vol, warmth=warmth)
                            if now - last_save > 20.0:
                                last_save = now
                                recorder.save()

                base = c["FLOOR_CALM"] + (c["FLOOR_ENERGETIC"] - c["FLOOR_CALM"]) * energy
                sat_eff = sat * (1.0 - c["SAT_VOL_DUCK"] * (1.0 - vol))   # baixo volume = cor menos forte

                def bright(env):
                    # brilho final = volume * (base entre batidas + pulso da batida)
                    return max(0.0, min(1.0, vol * (base + (1.0 - base) * env)))

                def peak_value():
                    return c["PEAK_WEAK"] + (c["PEAK_STRONG"] - c["PEAK_WEAK"]) * \
                        min(1.0, 0.5 * onset_strength + 0.5 * energy)

                # Soco: agenda beat no tempo exato (brilho ja escalado pelo volume)
                punch_now = False
                if replay_active:
                    punch_now = onset          # ja agendado acima, no tempo aprendido
                elif tracker.locked:
                    fb = tracker.next_future_beat(now)
                    if fb is not None and fb != last_sched_beat:
                        last_sched_beat = fb
                        punch_now = True
                        rf, gf, bf = colorsys.hsv_to_rgb(hue, sat_eff, bright(peak_value()))
                        st = fb + comp
                        if st > now:
                            sched.schedule(st, int(rf * 255), int(gf * 255), int(bf * 255), True)
                        last_idle = now
                elif onset:
                    punch_now = True
                    rf, gf, bf = colorsys.hsv_to_rgb(hue, sat_eff, bright(peak_value()))
                    sched.schedule(max(now, now + comp), int(rf * 255), int(gf * 255), int(bf * 255), True)
                    last_idle = now

                # Envelope da batida (0..pico) com decay pelo andamento
                if tracker.locked and tracker.period:
                    pulse = min(c["PULSE_MAX"], max(c["PULSE_MIN"], c["PULSE_FRACTION"] * tracker.period))
                else:
                    pulse = 0.25
                decay_factor = math.exp(-dt / max(pulse / 3.0, 1e-3))
                beat_env = max(beat_env * decay_factor, 0.0)
                if punch_now:
                    beat_env = max(beat_env, peak_value())

                value = bright(beat_env)
                rf, gf, bf = colorsys.hsv_to_rgb(hue, sat_eff, value)
                r, g, b = int(rf * 255), int(gf * 255), int(bf * 255)
                H, S, V = int(round(hue * 360)), int(round(sat_eff * 100)), int(round(value * 100))
                lH, lS, lV = last_sent
                changed = (abs(H - lH) >= c["SEND_DH"] or abs(S - lS) >= c["SEND_DS"] or abs(V - lV) >= c["SEND_DV"])
                if changed and (now - last_idle) >= c["MIN_UPDATE_INTERVAL"]:
                    sched.schedule(max(now, now + comp), r, g, b, energy > c["ENERGY_FLASH_TH"])
                    last_sent = (H, S, V)
                    last_idle = now

                # Status ao vivo (~30 Hz)
                if now - last_emit >= 0.033:
                    last_emit = now
                    meta = poller.get()
                    self.status.update(locked=tracker.locked, bpm=tracker.bpm(), energy=energy,
                                       rgb=(r, g, b), centroid=centroid, key=key_name, warmth=warmth,
                                       vol=vol, song=meta["title"], artist=meta["artist"], album=meta["album"],
                                       learned=player is not None)
                    self._emit()

        except Exception as e:
            self.status["error"] = str(e)
            self.log(f"[-] Erro no motor: {e}")
        finally:
            try:
                if recorder and recorder.save():
                    self.log(f"[+] Faixa aprendida salva: {recorder.coverage()*100:.0f}% coberta.")
            except Exception:
                pass
            try:
                poller.stop()
            except Exception:
                pass
            sched.stop()
            sched.join(timeout=0.5)
            try:
                if stream:
                    stream.stop_stream(); stream.close()
            except Exception:
                pass
            p.terminate()
            try:
                device.set_mode("white"); device.set_white_percentage(80, 0)
            except Exception:
                pass
            self.status.update(running=False, connected=False, locked=False)
            self._emit()
            self.log("[*] Motor parado. Luz branca restaurada.")


# ----------------------------- CLI -----------------------------
def _console_status(st):
    bar = "#" * int((st["rgb"][0] + st["rgb"][1] + st["rgb"][2]) / 765 * 20)
    bar = bar + "-" * (20 - len(bar))
    estars = "*" * int(st["energy"] * 5)
    tag = f"LOCK {st['bpm']:3.0f}bpm" if st["locked"] else "track"
    print(f"\r|{bar}| {tag:13s} E[{estars:<5s}] {st['centroid']:6.0f}Hz RGB{st['rgb']}", end="")
    sys.stdout.flush()


def main():
    print("=" * 64)
    print("   Spotify Sync v6 (CLI)  —  use 'python hub.py' p/ interface grafica")
    print("=" * 64)
    cfg = load_config()
    engine = SyncEngine(cfg, status_cb=_console_status, log_cb=print)
    engine.start()
    print("[*] Rodando. Ctrl+C para sair.")
    try:
        while engine.is_running():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[*] Encerrando...")
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
