#!/usr/bin/env python3
"""
bt_calibrate.py — Calibracao do atraso do Bluetooth (mic) v3 "track + correlacao"
================================================================================
Toca um AUDIO LINEAR continuo com BEATS (cliques) em posicoes conhecidas pela
saida padrao (Echo via BT). O mic grava continuo. Casamos cada beat gravado com
o emitido por FILTRO CASADO (correlacao) -> latencia precisa por beat.

Vantagens vs pulsos isolados:
  - Um unico playback continuo (rapido, sem esperar silencio).
  - Robusto a BT LENTO (a correlacao acha o beat mesmo com 500 ms de atraso e
    beats sobrepostos no ar).
  - Sub-amostra: pico da correlacao da o instante exato.

  OUTPUT_DELAY_sugerido = mediana - MIC_LATENCY

ATENCAO mic de laptop: cancelamento de eco pode atrapalhar. Se o contraste for
baixo, troque MIC_INDEX (mic USB externo e o ideal) ou desligue aprimoramentos.

Uso:  python bt_calibrate.py   (ou botao no hub)
"""

import time
import threading
import statistics

import numpy as np
import pyaudiowpatch as pa

import spotify_sync as ss
from calibrate import _robust

clock = time.perf_counter

BEEP_FREQ = 1000.0
CLICK_FREQ = 3000.0
CLICK_DUR = 0.004
N = 512


def _tone_bytes(sr, channels, freq, dur, amp=0.95):
    n = int(sr * dur)
    t = np.arange(n) / sr
    wave = amp * np.sin(2.0 * np.pi * freq * t)
    fade = max(1, int(sr * 0.0015))
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    wave *= env
    data = np.repeat((wave * 32767)[:, None], channels, axis=1).reshape(-1)
    return data.astype(np.int16), wave  # (interleaved int16, mono float)


def _env(x, sr):
    w = max(1, int(0.003 * sr))
    return np.convolve(np.abs(x), np.ones(w) / w, "same")


def _fft_xcorr_lag(a, b):
    """Lag (amostras) tal que a[n] ~ b[n-lag]. Via FFT (rapido)."""
    n = len(a) + len(b)
    nf = 1 << int(np.ceil(np.log2(max(2, n))))
    c = np.fft.irfft(np.fft.rfft(a, nf) * np.conj(np.fft.rfft(b, nf)), nf)
    lag = int(np.argmax(c))
    if lag > nf // 2:
        lag -= nf
    return lag


def measure_bt_latency(cfg, log=print, pulses=20):
    p = pa.PyAudio()
    out = mic = None
    try:
        try:
            o = p.get_default_output_device_info()
            mi = int(cfg.get("MIC_INDEX", -1))
            i = p.get_device_info_by_index(mi) if mi >= 0 else p.get_default_input_device_info()
        except Exception as e:
            return {"error": f"dispositivos de audio: {e}"}

        sr = int(o["defaultSampleRate"])
        in_sr = int(i["defaultSampleRate"])
        out_ch = min(2, max(1, int(o.get("maxOutputChannels", 2))))
        log(f"[+] Saida: {o['name']}")
        log(f"[+] Mic  : {i['name']}")
        if "echo" not in o["name"].lower() and "bluetooth" not in o["name"].lower():
            log("[!] Saida padrao nao parece o Echo. Ponha o Echo como saida padrao do Windows.")

        try:
            out = p.open(format=pa.paInt16, channels=out_ch, rate=sr, output=True,
                         output_device_index=o["index"], frames_per_buffer=1024)
            mic = p.open(format=pa.paInt16, channels=1, rate=in_sr, input=True,
                         input_device_index=i["index"], frames_per_buffer=N)
        except Exception as e:
            return {"error": f"nao abriu os streams de audio: {e}"}

        # ---- contraste rapido (1 beep) p/ detectar mic surdo / eco ----
        win = np.hanning(N).astype(np.float32)
        freqs = np.fft.rfftfreq(N, d=1.0 / in_sr)
        band = np.where((freqs >= CLICK_FREQ - 300) & (freqs <= CLICK_FREQ + 300))[0]

        def tone_energy():
            d = np.frombuffer(mic.read(N, exception_on_overflow=False), dtype=np.int16).astype(np.float32)
            return float(np.abs(np.fft.rfft(d * win))[band].sum())

        base = statistics.median([tone_energy() for _ in range(int(0.4 * in_sr / N))]) or 1.0
        test_i16, _ = _tone_bytes(sr, out_ch, CLICK_FREQ, 0.10)
        out.write(test_i16.tobytes())
        on = base
        t_end = clock() + 0.6
        while clock() < t_end:
            on = max(on, tone_energy())
        contrast = on / (base + 1e-9)
        log(f"[+] Contraste do mic: {contrast:.1f}x.")
        if contrast < 2.0:
            return {"error": "mic mal ouve o Echo (contraste baixo). Provavel cancelamento de "
                             "eco / volume baixo / mic longe. Troque MIC_INDEX ou use mic USB."}
        time.sleep(0.4)

        # ---- monta o track linear: cliques em posicoes conhecidas ----
        spacing = float(cfg.get("CAL_SPACING", 0.45))
        lead, tail = 0.5, 1.2
        click_i16, _ = _tone_bytes(sr, out_ch, CLICK_FREQ, CLICK_DUR)
        click_len = len(click_i16) // out_ch
        total = lead + pulses * spacing + tail
        track = np.zeros((int(total * sr), out_ch), dtype=np.float32)
        pos = []
        for j in range(pulses):
            s = int((lead + j * spacing) * sr)
            seg = click_i16.reshape(-1, out_ch).astype(np.float32)
            track[s:s + click_len] += seg
            pos.append(s)
        track_bytes = np.clip(track.reshape(-1), -32768, 32767).astype(np.int16).tobytes()

        # template do clique no sr do mic (p/ correlacao)
        _, ctpl = _tone_bytes(in_sr, 1, CLICK_FREQ, CLICK_DUR)
        cenv = _env(ctpl, in_sr)

        # ---- toca (thread) e grava continuo ----
        log(f"[*] Tocando track linear com {pulses} beats e gravando...")
        rec = []
        th = threading.Thread(target=lambda: out.write(track_bytes), daemon=True)
        mic.read(N, exception_on_overflow=False)         # warmup
        th.start()
        t_play0 = clock()
        t_rec0 = None
        t_stop = t_play0 + total
        while clock() < t_stop:
            buf = mic.read(N, exception_on_overflow=False)
            tn = clock()
            if t_rec0 is None:
                t_rec0 = tn - N / in_sr
            rec.append(buf)
        th.join(timeout=1.0)
    finally:
        for s in (out, mic):
            try:
                if s:
                    s.stop_stream(); s.close()
            except Exception:
                pass
        p.terminate()

    recorded = np.frombuffer(b"".join(rec), dtype=np.int16).astype(np.float32)
    renv = _env(recorded, in_sr)
    if renv.size < N:
        return {"error": "gravacao vazia."}

    # 1) atraso GLOBAL: correlaciona o trem inteiro (robusto, evita pegar vizinho)
    eenv = np.zeros(renv.size, dtype=np.float32)
    cl = len(cenv)
    for s in pos:
        if s + cl <= eenv.size:
            eenv[s:s + cl] += cenv
    lag = _fft_xcorr_lag(renv, eenv)                      # renv[n] ~ eenv[n-lag]
    log(f"[+] Atraso global: {(lag / in_sr + (t_rec0 - t_play0)) * 1000:.0f} ms")

    # 2) refina cada beat numa janela ESTREITA em volta do pico esperado
    corr = np.correlate(renv, cenv, mode="valid")
    thrc = float(corr.max()) * 0.30
    W = int(spacing * 0.35 * in_sr)                       # < meio espacamento
    lat = []
    for s in pos:
        k0 = s + lag
        lo, hi = max(0, k0 - W), min(corr.size, k0 + W)
        if hi <= lo:
            continue
        k = lo + int(np.argmax(corr[lo:hi]))
        if corr[k] < thrc:
            continue
        rec_time = t_rec0 + k / in_sr
        emit_time = t_play0 + s / sr
        lat.append(rec_time - emit_time)

    lat = [x for x in lat if 0.0 <= x <= 1.5]
    if len(lat) < 3:
        return {"error": "poucos beats casados. Suba o volume / aproxime o mic / troque MIC_INDEX."}

    keep, median, jitter = _robust(lat)
    for x in lat:
        log(f"    beat: {x * 1000:.0f} ms")
    mic_lat = float(cfg.get("MIC_LATENCY", 0.03))
    suggestion = max(0.0, min(0.50, median - mic_lat))
    return {"median": median, "jitter": jitter, "min": min(keep), "max": max(keep),
            "n": len(keep), "n_raw": len(lat), "mic_latency": mic_lat, "suggestion": suggestion}


def list_microphones():
    p = pa.PyAudio()
    out = []
    try:
        for idx in range(p.get_device_count()):
            d = p.get_device_info_by_index(idx)
            if d.get("maxInputChannels", 0) > 0 and "loopback" not in d["name"].lower():
                out.append((idx, d["name"]))
    finally:
        p.terminate()
    return out


def main():
    print("=" * 60)
    print("   Calibracao do atraso do Bluetooth (mic) v3 — track + correlacao")
    print("=" * 60)
    cfg = ss.load_config()
    print("Microfones (use MIC_INDEX no config p/ trocar):")
    for idx, name in list_microphones():
        print(f"   [{idx}] {name}")
    res = measure_bt_latency(cfg, pulses=int(cfg.get("PULSE_COUNT", 20)))
    if "error" in res:
        print(f"\n[-] {res['error']}")
        return
    print("\n--- Resultado ---")
    print(f"  Atraso BT (mediana): {res['median']*1000:.0f} ms  (inclui ~{res['mic_latency']*1000:.0f} ms do mic)")
    print(f"  Jitter             : {res['jitter']*1000:.0f} ms")
    print(f"  Faixa              : {res['min']*1000:.0f}..{res['max']*1000:.0f} ms  | {res['n']}/{res['n_raw']}")
    print(f"  >> OUTPUT_DELAY sugerido: {res['suggestion']*1000:.0f} ms ({res['suggestion']:.3f} s)")
    if input("\nAplicar e salvar em config.json? [s/N] ").strip().lower() == "s":
        cfg["OUTPUT_DELAY"] = round(res["suggestion"], 3)
        ss.save_config(cfg)
        print("[+] Salvo.")


if __name__ == "__main__":
    main()
