#!/usr/bin/env python3
"""
calibrate.py — Calibracao por webcam (malha fechada, v2)
========================================================
A webcam VE a fita. Mandamos um flash e medimos quando a camera detecta a luz
=> mede o ATRASO REAL de saida (rede + firmware + foton) e o JITTER.

Melhorias v2:
  - Forca MJPG + baixa resolucao -> mais FPS -> menos quantizacao.
  - MEDIANA + corte de outliers (MAD), no lugar da media crua.
  - AUTO-estima a latencia da propria webcam = N_frames * periodo_do_frame
    (medido na hora), em vez de um valor fixo.
  - Esvazia o buffer antes de cada flash (frame fresco).

  BULB_LATENCY_sugerido = mediana - latencia_camera

Aponte a webcam para a fita, de preferencia num ambiente mais escuro.
Uso:  python calibrate.py   (ou run_calibrate.bat / botao no hub)
"""

import time
import statistics

import numpy as np

import spotify_sync as ss

clock = time.perf_counter


def _open_camera(index, cfg):
    try:
        import cv2
    except Exception:
        return None, None, "OpenCV nao instalado. Rode: pip install opencv-python-headless"
    try:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)     # DSHOW abre rapido no Windows
        if not cap.isOpened():
            cap.release()
            return None, cv2, f"webcam (indice {index}) nao abriu. Em uso por outro app?"
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # MJPG = mais fps
        except Exception:
            pass
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(cfg.get("CAM_W", 320)))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cfg.get("CAM_H", 240)))
        cap.set(cv2.CAP_PROP_FPS, int(cfg.get("CAM_FPS", 60)))
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)          # menos buffer = frame mais fresco
        except Exception:
            pass
        for _ in range(15):                              # aquecimento
            cap.read()
        return cap, cv2, None
    except Exception as e:
        return None, None, str(e)


def _gray(frame):
    return frame.mean(axis=2) if frame.ndim == 3 else frame.astype(np.float32)


def _roi(gray, mask):
    return float(gray[mask].mean()) if mask is not None else float(gray.mean())


def _grab(cap, n=3):
    g = None
    for _ in range(n):
        ok, fr = cap.read()
        if ok:
            g = _gray(fr)
    return g


def _frame_period(cap, n=40):
    """Mede o periodo medio entre frames (s) -> base p/ quantizacao e fps."""
    ts = []
    for _ in range(n):
        ok, _ = cap.read()
        if ok:
            ts.append(clock())
    dts = sorted(ts[i] - ts[i - 1] for i in range(1, len(ts)) if ts[i] - ts[i - 1] > 0)
    return dts[len(dts) // 2] if dts else (1.0 / 30.0)


def _robust(vals):
    """Corta SO outliers altos (hiccups de rede/frame perdido) via MAD; o piso
    fisico (flashes rapidos) fica. Retorna (limpos, mediana, jitter_robusto)."""
    s = sorted(vals)
    med = statistics.median(s)
    mad = statistics.median([abs(v - med) for v in s]) or 1e-9
    hi = med + 3.0 * 1.4826 * mad
    keep = [v for v in s if v <= hi]
    if len(keep) < 3:
        keep = s
    med2 = statistics.median(keep)
    jit = 1.4826 * statistics.median([abs(v - med2) for v in keep])
    return keep, med2, jit


def _send(device, music, r, g, b):
    try:
        if music:
            device.set_music_colour(device.MUSIC_TRANSITION_JUMP, r, g, b)
        else:
            device.set_colour(r, g, b)
    except Exception:
        pass


def _restore(device, music):
    try:
        device.set_mode("white")
        device.set_white_percentage(80, 0)
    except Exception:
        pass


def measure_latency(cfg, log=print, trials=12, cam_index=0):
    """Mede o atraso de saida da fita via webcam. Retorna dict com stats."""
    try:
        did, ip, key, ver = ss.load_device_info()
        device, music = ss.connect_bulb(did, ip, key, ver)
    except Exception as e:
        return {"error": f"fita: {e}"}

    cap, cv2, err = _open_camera(cam_index, cfg)
    if cap is None:
        return {"error": err or "webcam indisponivel"}

    try:
        period = _frame_period(cap)
        fps = 1.0 / period if period > 0 else 0.0
        log(f"[+] Webcam ~{fps:.0f} fps (frame {period*1000:.0f} ms).")

        log("[*] Localizando a fita (apague/acenda)...")
        _send(device, music, 0, 0, 0); time.sleep(0.7)
        off = _grab(cap)
        _send(device, music, 255, 255, 255); time.sleep(0.7)
        on = _grab(cap)
        if off is None or on is None:
            return {"error": "sem frames da webcam."}

        diff = np.abs(on - off)
        mask = diff > 40
        npix = int(mask.sum())
        if npix < 30:
            mask = None
            log("[!] Nao vi a fita com clareza; usando o quadro inteiro. Aproxime/aponte melhor.")
        else:
            log(f"[+] Fita localizada: {npix} pixels mudaram.")

        off_lvl, on_lvl = _roi(off, mask), _roi(on, mask)
        if on_lvl - off_lvl < 8:
            return {"error": "contraste baixo: a webcam mal ve a fita acender. "
                             "Escureca o ambiente e aponte direto p/ a fita."}
        rng = on_lvl - off_lvl
        low_thr = off_lvl + 0.12 * rng          # pega o frame parcial (inicio do flash)
        exposure = period                       # exposicao ~ 1 frame (camera lenta/AE no maximo)
        log(f"[+] Contraste OK (off={off_lvl:.0f} on={on_lvl:.0f}). Medindo {trials} flashes (sub-frame)...")

        lat = []
        for i in range(trials):
            _send(device, music, 0, 0, 0)
            time.sleep(0.30 + 0.04 * (i % 5))
            for _ in range(2):                  # esvazia buffer -> frame fresco
                cap.grab()
            t0 = clock()
            _send(device, music, 255, 255, 255)   # FLASH
            est, tend = None, t0 + 1.5
            while clock() < tend:
                ok, fr = cap.read()
                ts = clock()
                if not ok:
                    continue
                b = _roi(_gray(fr), mask)
                if b >= low_thr:
                    # fracao da exposicao que estava iluminada -> interpola o instante do flash
                    f = max(0.05, min(1.0, (b - off_lvl) / (rng + 1e-9)))
                    est = (ts - t0) - f * exposure
                    break
            if est is not None:
                est = max(0.0, est)
                lat.append(est)
                log(f"    {i + 1}/{trials}: {est * 1000:.0f} ms")
            else:
                log(f"    {i + 1}/{trials}: sem deteccao")
        _send(device, music, 0, 0, 0)
    finally:
        try:
            cap.release()
        except Exception:
            pass
        _restore(device, music)

    if len(lat) < 3:
        return {"error": "poucas deteccoes. Verifique mira, contraste e ambiente escuro."}

    keep, median, jitter = _robust(lat)
    mn = min(keep)
    # Latencia da camera: override manual (config>0) ou auto = N_frames * periodo
    # Sub-frame ja removeu a quantizacao -> so resta o readout/transporte (pequeno)
    override = float(cfg.get("CAMERA_LATENCY", 0.0))
    cam = override if override > 0 else float(cfg.get("CAMERA_TRANSPORT", 0.012))
    suggestion = max(0.0, min(0.40, median - cam))
    return {"median": median, "jitter": jitter, "min": mn, "n": len(keep),
            "n_raw": len(lat), "fps": fps, "frame_ms": period * 1000.0,
            "camera_latency": cam, "suggestion": suggestion}


def main():
    print("=" * 60)
    print("   Calibracao por webcam — atraso da fita (v2)")
    print("=" * 60)
    cfg = ss.load_config()
    res = measure_latency(cfg, trials=int(cfg.get("CAL_TRIALS", 12)),
                          cam_index=int(cfg.get("CAM_INDEX", 0)))
    if "error" in res:
        print(f"[-] {res['error']}")
        return
    print("\n--- Resultado ---")
    print(f"  Webcam         : ~{res['fps']:.0f} fps ({res['frame_ms']:.0f} ms/frame)")
    print(f"  Latencia (mediana): {res['median']*1000:.0f} ms  "
          f"(inclui ~{res['camera_latency']*1000:.0f} ms estimados da webcam)")
    print(f"  Jitter (robusto)  : {res['jitter']*1000:.0f} ms   (menor = fita mais consistente)")
    print(f"  Minimo            : {res['min']*1000:.0f} ms   | medidas validas: {res['n']}/{res['n_raw']}")
    print(f"  >> BULB_LATENCY sugerido: {res['suggestion']*1000:.0f} ms ({res['suggestion']:.3f} s)")
    ans = input("\nAplicar e salvar em config.json? [s/N] ").strip().lower()
    if ans == "s":
        cfg["BULB_LATENCY"] = round(res["suggestion"], 3)
        ss.save_config(cfg)
        print("[+] Salvo.")


if __name__ == "__main__":
    main()
