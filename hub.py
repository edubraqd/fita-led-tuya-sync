#!/usr/bin/env python3
"""
Hub Central — Fita LED Tuya  (interface grafica)
================================================
Painel visual p/ configurar e rodar a sincronizacao musical.
- Sliders de todos os ajustes (aplicam AO VIVO no motor rodando)
- Start/Stop, monitor ao vivo (lock/BPM/energia/cor), teste manual
- Salva/carrega config.json

Rode:  python hub.py   (ou run_hub.bat)
"""

import threading
import queue
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox

import spotify_sync as ss

# Aparencia (tema escuro)
BG = "#14161c"
PANEL = "#1d212b"
FG = "#e6e6e6"
MUTED = "#8b93a7"
ACCENT = "#34d399"
ACCENT2 = "#60a5fa"

# Grupos de sliders: (chave, rotulo, min, max, inteiro?)
GROUPS = {
    "Latencia & Predicao": [
        ("BULB_LATENCY", "Atraso fita/rede [s]", 0.0, 0.40, False),
        ("OUTPUT_DELAY", "Atraso Bluetooth/Alexa [s]", 0.0, 0.50, False),
        ("MIN_UPDATE_INTERVAL", "Intervalo min update [s]", 0.03, 0.20, False),
    ],
    "Cromoterapia": [
        ("CENTROID_LOW_HZ", "Grave->Vermelho ate [Hz]", 50, 1500, True),
        ("CENTROID_HIGH_HZ", "Agudo->Violeta a partir [Hz]", 1500, 12000, True),
        ("HUE_MAX", "Alcance de matiz (0.75=violeta)", 0.30, 1.00, False),
        ("HUE_SMOOTH", "Suavizacao da cor", 0.05, 1.00, False),
    ],
    "Dinamica / Energia": [
        ("ENERGY_FLASH_TH", "Limiar p/ PISCAR (strobe)", 0.0, 1.0, False),
        ("FLOOR_CALM", "Base entre batidas (calmo)", 0.0, 0.80, False),
        ("FLOOR_ENERGETIC", "Base entre batidas (energetico)", 0.0, 0.60, False),
        ("PEAK_WEAK", "Pico batida fraca", 0.20, 1.0, False),
        ("PEAK_STRONG", "Pico batida forte", 0.50, 1.0, False),
        ("PULSE_FRACTION", "Duracao do pulso (fracao do beat)", 0.20, 1.0, False),
        ("SAT_ENERGY_BOOST", "Saturacao por energia", 0.0, 0.50, False),
    ],
    "Volume / Intensidade": [
        ("VOL_GATE", "Silencio -> apaga (gate)", 0.0, 0.030, False),
        ("VOL_REF", "Volume p/ brilho maximo", 0.05, 0.40, False),
        ("VOL_ATTACK", "Rapidez ao subir", 0.10, 1.0, False),
        ("VOL_RELEASE", "Suavidade ao abaixar", 0.02, 0.50, False),
        ("VOL_GAMMA", "Curva (menor=clareia baixo)", 0.40, 1.50, False),
        ("SAT_VOL_DUCK", "Desbota no volume baixo", 0.0, 0.60, False),
    ],
    "Onset / Beat": [
        ("ODF_THRESH_K", "Sensib. kick (maior=menos)", 1.0, 3.0, False),
        ("REFRACTORY", "Refratario do onset [s]", 0.05, 0.30, False),
        ("ONSET_LOW_HZ", "Banda kick min [Hz]", 10, 100, True),
        ("ONSET_HIGH_HZ", "Banda kick max [Hz]", 100, 500, True),
        ("LOCK_TOL", "Tolerancia p/ travar ritmo (fallback IBI)", 0.05, 0.30, False),
        ("ODF_LOG_GAMMA", "Compressao log do espectro (0=linear)", 0.0, 50.0, False),
        ("TEMPO_MIN_BPM", "Faixa de tempo: BPM minimo", 50, 120, True),
        ("TEMPO_MAX_BPM", "Faixa de tempo: BPM maximo", 100, 240, True),
        ("TEMPO_PRIOR_BPM", "BPM preferido (prior)", 80, 160, True),
        ("TEMPO_LOCK_CONF", "Confianca p/ travar (tempograma)", 0.05, 0.80, False),
    ],
}


class Hub:
    def __init__(self, root):
        self.root = root
        self.cfg = ss.load_config()
        self.engine = None
        self.status_q = queue.Queue()
        self.log_q = queue.Queue()
        self.value_labels = {}
        self.scales = {}

        root.title("Hub LED Tuya — Sincronizador Musical")
        root.configure(bg=BG)
        root.geometry("1000x680")
        root.minsize(900, 620)
        self._style()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(60, self._poll)

    # ---------- estilo ----------
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL)
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TLabelframe", background=BG, foreground=ACCENT2, bordercolor="#2b3140")
        st.configure("TLabelframe.Label", background=BG, foreground=ACCENT2)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Muted.TLabel", background=BG, foreground=MUTED)
        st.configure("Big.TLabel", background=BG, foreground=FG, font=("Segoe UI", 22, "bold"))
        st.configure("Panel.TLabel", background=PANEL, foreground=FG)
        st.configure("TButton", background=PANEL, foreground=FG, borderwidth=0, padding=6)
        st.map("TButton", background=[("active", "#2b3140")])
        st.configure("Start.TButton", background=ACCENT, foreground="#06281c", font=("Segoe UI", 10, "bold"))
        st.map("Start.TButton", background=[("active", "#28b889")])
        st.configure("Stop.TButton", background="#ef4444", foreground="#2a0707", font=("Segoe UI", 10, "bold"))
        st.map("Stop.TButton", background=[("active", "#d63a3a")])
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(12, 6))
        st.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", ACCENT)])
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL)

    # ---------- montagem ----------
    def _build(self):
        root = self.root
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(1, weight=1)

        # Barra superior
        top = ttk.Frame(root)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))
        self.btn_start = ttk.Button(top, text="▶  Iniciar", style="Start.TButton", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(top, text="■  Parar", style="Stop.TButton", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Salvar config", command=self._save).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Padrao", command=self._reset).pack(side="left", padx=(8, 0))
        self.btn_cal = ttk.Button(top, text="Calibrar luz (webcam)", command=self._calibrate)
        self.btn_cal.pack(side="left", padx=(8, 0))
        self.btn_bt = ttk.Button(top, text="Calibrar BT (mic)", command=self._calibrate_bt)
        self.btn_bt.pack(side="left", padx=(8, 0))
        self.lbl_state = ttk.Label(top, text="parado", style="Muted.TLabel")
        self.lbl_state.pack(side="right")

        # Notebook de ajustes (esquerda)
        nb = ttk.Notebook(root)
        nb.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=6)
        for title, items in GROUPS.items():
            tab = ttk.Frame(nb)
            nb.add(tab, text=title)
            for i, (key, label, lo, hi, is_int) in enumerate(items):
                self._add_slider(tab, i, key, label, lo, hi, is_int)
            tab.columnconfigure(1, weight=1)
        # Extras na 1a aba (predicao) + aba captura
        self._build_capture_tab(nb)

        # Predicao (checkbox) na barra superior tambem
        self.var_pred = tk.BooleanVar(value=bool(self.cfg["ENABLE_PREDICTION"]))
        ttk.Checkbutton(top, text="Antecipar batida", variable=self.var_pred,
                        command=self._toggle_pred).pack(side="left", padx=(16, 0))

        # Painel direito: monitor + manual + log
        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=6)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        self._build_nowplaying(right)
        self._build_monitor(right)
        self._build_manual(right)
        self._build_log(right)

    def _add_slider(self, parent, row, key, label, lo, hi, is_int):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        val = float(self.cfg.get(key, lo))
        vlbl = ttk.Label(parent, text=self._fmt(val, is_int), width=8, style="Muted.TLabel")
        vlbl.grid(row=row, column=2, sticky="e", padx=8)
        self.value_labels[key] = (vlbl, is_int)
        sc = ttk.Scale(parent, from_=lo, to=hi, orient="horizontal",
                       command=lambda v, k=key, ii=is_int: self._on_slide(k, v, ii))
        sc.set(val)
        sc.grid(row=row, column=1, sticky="ew", padx=8)
        self.scales[key] = sc

    def _build_capture_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Captura")
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text="Saida de audio a capturar").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        try:
            devs = ss.list_loopback_devices()
        except Exception:
            devs = []
        self.var_dev = tk.StringVar(value=self.cfg.get("LOOPBACK_DEVICE_MATCH", ""))
        cb = ttk.Combobox(tab, textvariable=self.var_dev, values=devs, width=42)
        cb.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self._set_dev())
        cb.bind("<FocusOut>", lambda e: self._set_dev())
        ttk.Label(tab, text="(aplica ao Iniciar. Escolha o Echo p/ Bluetooth)",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", padx=8)
        ttk.Button(tab, text="Atualizar lista", command=lambda: cb.configure(values=ss.list_loopback_devices())
                   ).grid(row=2, column=0, sticky="w", padx=8, pady=8)
        ttk.Label(tab, text="Aviso de silencio [s]").grid(row=3, column=0, sticky="w", padx=8, pady=8)
        self._add_inline_slider(tab, 3, "SILENCE_WARN_SECS", 1.0, 10.0, False)

    def _add_inline_slider(self, parent, row, key, lo, hi, is_int):
        val = float(self.cfg.get(key, lo))
        vlbl = ttk.Label(parent, text=self._fmt(val, is_int), width=8, style="Muted.TLabel")
        vlbl.grid(row=row, column=2, sticky="e", padx=8)
        self.value_labels[key] = (vlbl, is_int)
        sc = ttk.Scale(parent, from_=lo, to=hi, orient="horizontal",
                       command=lambda v, k=key, ii=is_int: self._on_slide(k, v, ii))
        sc.set(val)
        sc.grid(row=row, column=1, sticky="ew", padx=8)
        self.scales[key] = sc

    def _build_nowplaying(self, parent):
        nf = ttk.LabelFrame(parent, text="Tocando agora")
        nf.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.lbl_song = ttk.Label(nf, text="—", font=("Segoe UI", 12, "bold"))
        self.lbl_song.pack(anchor="w", padx=10, pady=(8, 0))
        self.lbl_artist = ttk.Label(nf, text="", style="Muted.TLabel")
        self.lbl_artist.pack(anchor="w", padx=10)
        mood = ttk.Frame(nf)
        mood.pack(anchor="w", fill="x", padx=10, pady=8)
        self.lbl_key = ttk.Label(mood, text="tom: —", style="Muted.TLabel")
        self.lbl_key.pack(side="left")
        self.lbl_mood = ttk.Label(mood, text="○ NEUTRA", style="Muted.TLabel")
        self.lbl_mood.pack(side="right")

    def _build_monitor(self, parent):
        mon = ttk.LabelFrame(parent, text="Monitor ao vivo")
        mon.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        mon.columnconfigure(1, weight=1)
        # Swatch de cor
        self.swatch = tk.Canvas(mon, width=90, height=90, bg="#000000", highlightthickness=1,
                                highlightbackground="#2b3140")
        self.swatch.grid(row=0, column=0, rowspan=3, padx=10, pady=10)
        self.lbl_bpm = ttk.Label(mon, text="-- BPM", style="Big.TLabel")
        self.lbl_bpm.grid(row=0, column=1, sticky="w", padx=6)
        self.lbl_lock = ttk.Label(mon, text="SEM LOCK", style="Muted.TLabel")
        self.lbl_lock.grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(mon, text="Energia", style="Muted.TLabel").grid(row=2, column=1, sticky="w", padx=6)
        self.energy_bar = ttk.Progressbar(mon, maximum=100, length=180)
        self.energy_bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 4))
        ttk.Label(mon, text="Intensidade (volume)", style="Muted.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", padx=10)
        self.vol_bar = ttk.Progressbar(mon, maximum=100, length=180)
        self.vol_bar.grid(row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))
        self.lbl_info = ttk.Label(mon, text="dispositivo: -\nmodo: -\ncentroide: -", style="Muted.TLabel", justify="left")
        self.lbl_info.grid(row=6, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))

    def _build_manual(self, parent):
        man = ttk.LabelFrame(parent, text="Teste manual (com o motor parado)")
        man.grid(row=2, column=0, sticky="ew", pady=8)
        ttk.Button(man, text="Ligar", command=lambda: self._manual("on")).pack(side="left", padx=6, pady=8)
        ttk.Button(man, text="Desligar", command=lambda: self._manual("off")).pack(side="left", padx=6)
        ttk.Button(man, text="Branco", command=lambda: self._manual("white")).pack(side="left", padx=6)
        ttk.Button(man, text="Escolher cor", command=self._manual_color).pack(side="left", padx=6)

    def _build_log(self, parent):
        box = ttk.LabelFrame(parent, text="Log")
        box.grid(row=3, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.log = tk.Text(box, height=8, bg=PANEL, fg=MUTED, insertbackground=FG,
                           relief="flat", wrap="word", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.log.configure(state="disabled")

    # ---------- handlers ----------
    def _fmt(self, x, is_int):
        return str(int(round(x))) if is_int else f"{x:.3f}"

    def _on_slide(self, key, v, is_int):
        x = float(v)
        if is_int:
            x = int(round(x))
        self.cfg[key] = x                      # dict compartilhado -> aplica AO VIVO
        lbl, ii = self.value_labels[key]
        lbl.config(text=self._fmt(x, ii))

    def _toggle_pred(self):
        self.cfg["ENABLE_PREDICTION"] = bool(self.var_pred.get())

    def _set_dev(self):
        self.cfg["LOOPBACK_DEVICE_MATCH"] = self.var_dev.get().strip()

    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        if self.engine and self.engine.is_running():
            return
        self._set_dev()
        ss.save_config(self.cfg)
        self.engine = ss.SyncEngine(self.cfg,
                                    status_cb=lambda s: self.status_q.put(s),
                                    log_cb=lambda m: self.log_q.put(m))
        self.engine.start()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.lbl_state.config(text="rodando", foreground=ACCENT)

    def _stop(self):
        if not self.engine:
            return
        self.btn_stop.config(state="disabled")
        self.lbl_state.config(text="parando...", foreground=MUTED)
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self):
        try:
            self.engine.stop()
        except Exception:
            pass
        self.root.after(0, self._after_stop)

    def _after_stop(self):
        self.btn_start.config(state="normal")
        self.lbl_state.config(text="parado", foreground=MUTED)
        self.lbl_lock.config(text="SEM LOCK", foreground=MUTED)
        self.lbl_bpm.config(text="-- BPM")
        self.energy_bar["value"] = 0
        self.vol_bar["value"] = 0
        self.swatch.config(bg="#000000")

    def _save(self):
        self._set_dev()
        try:
            ss.save_config(self.cfg)
            self._log("[+] config.json salvo.")
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _reset(self):
        if not messagebox.askyesno("Padrao", "Restaurar todos os ajustes para o padrao?"):
            return
        self.cfg.update(ss.DEFAULTS)
        for key, sc in self.scales.items():
            sc.set(self.cfg[key])          # dispara _on_slide -> atualiza cfg + label
        self.var_pred.set(bool(self.cfg["ENABLE_PREDICTION"]))
        self.var_dev.set(self.cfg["LOOPBACK_DEVICE_MATCH"])
        self._log("[+] Ajustes restaurados ao padrao.")

    # ---------- calibracao por webcam ----------
    def _calibrate(self):
        if self.engine and self.engine.is_running():
            messagebox.showinfo("Pare primeiro", "Pare a sincronizacao antes de calibrar.")
            return
        if not messagebox.askyesno(
                "Calibrar com webcam",
                "Aponte a webcam para a fita (ambiente mais escuro ajuda).\n"
                "A fita vai piscar varias vezes para medir o atraso.\n\nIniciar?"):
            return
        self.btn_cal.config(state="disabled")
        self._log("[*] Calibrando pela webcam... (a fita vai piscar)")
        threading.Thread(target=self._calibrate_worker, daemon=True).start()

    def _calibrate_worker(self):
        try:
            import calibrate
            res = calibrate.measure_latency(
                self.cfg, log=lambda m: self.log_q.put(m),
                trials=int(self.cfg.get("CAL_TRIALS", 12)),
                cam_index=int(self.cfg.get("CAM_INDEX", 0)))
        except Exception as e:
            res = {"error": str(e)}
        self.root.after(0, lambda: self._after_calibrate(res))

    def _after_calibrate(self, res):
        self.btn_cal.config(state="normal")
        if "error" in res:
            self._log(f"[-] Calibracao: {res['error']}")
            messagebox.showerror("Calibracao", res["error"])
            return
        msg = (f"Webcam: ~{res['fps']:.0f} fps ({res['frame_ms']:.0f} ms/frame)\n"
               f"Latencia (mediana): {res['median']*1000:.0f} ms "
               f"(inclui ~{res['camera_latency']*1000:.0f} ms estimados da webcam)\n"
               f"Jitter: {res['jitter']*1000:.0f} ms | minimo: {res['min']*1000:.0f} ms | "
               f"{res['n']}/{res['n_raw']} medidas\n\nBULB_LATENCY sugerido: {res['suggestion']*1000:.0f} ms")
        self._log("[+] " + msg.replace("\n", " "))
        if messagebox.askyesno("Calibracao concluida", msg + "\n\nAplicar e salvar?"):
            if "BULB_LATENCY" in self.scales:
                self.scales["BULB_LATENCY"].set(res["suggestion"])
            self.cfg["BULB_LATENCY"] = round(res["suggestion"], 3)
            ss.save_config(self.cfg)
            self._log("[+] BULB_LATENCY aplicado e salvo.")

    def _calibrate_bt(self):
        if self.engine and self.engine.is_running():
            messagebox.showinfo("Pare primeiro", "Pare a sincronizacao antes de calibrar.")
            return
        if not messagebox.askyesno(
                "Calibrar Bluetooth (mic)",
                "O PC vai emitir pulsos pela saida (Echo) e o microfone vai ouvir.\n"
                "Deixe o Echo como saida padrao do Windows e o mic perto dele.\n\nIniciar?"):
            return
        self.btn_bt.config(state="disabled")
        self._log("[*] Calibrando Bluetooth... (vai apitar varias vezes)")
        threading.Thread(target=self._calibrate_bt_worker, daemon=True).start()

    def _calibrate_bt_worker(self):
        try:
            import bt_calibrate
            res = bt_calibrate.measure_bt_latency(
                self.cfg, log=lambda m: self.log_q.put(m),
                pulses=int(self.cfg.get("PULSE_COUNT", 20)))
        except Exception as e:
            res = {"error": str(e)}
        self.root.after(0, lambda: self._after_calibrate_bt(res))

    def _after_calibrate_bt(self, res):
        self.btn_bt.config(state="normal")
        if "error" in res:
            self._log(f"[-] BT: {res['error']}")
            messagebox.showerror("Calibracao Bluetooth", res["error"])
            return
        msg = (f"Atraso BT (mediana): {res['median']*1000:.0f} ms "
               f"(inclui ~{res['mic_latency']*1000:.0f} ms do mic)\n"
               f"Jitter: {res['jitter']*1000:.0f} ms | faixa "
               f"{res['min']*1000:.0f}..{res['max']*1000:.0f} ms\n\n"
               f"OUTPUT_DELAY sugerido: {res['suggestion']*1000:.0f} ms")
        self._log("[+] " + msg.replace("\n", " "))
        if messagebox.askyesno("Calibracao Bluetooth", msg + "\n\nAplicar e salvar?"):
            if "OUTPUT_DELAY" in self.scales:
                self.scales["OUTPUT_DELAY"].set(res["suggestion"])
            self.cfg["OUTPUT_DELAY"] = round(res["suggestion"], 3)
            ss.save_config(self.cfg)
            self._log("[+] OUTPUT_DELAY aplicado e salvo.")

    def _manual(self, action):
        if self.engine and self.engine.is_running():
            messagebox.showinfo("Pare primeiro", "Pare a sincronizacao antes do teste manual.")
            return
        threading.Thread(target=self._manual_worker, args=(action, None), daemon=True).start()

    def _manual_color(self):
        if self.engine and self.engine.is_running():
            messagebox.showinfo("Pare primeiro", "Pare a sincronizacao antes do teste manual.")
            return
        rgb, _ = colorchooser.askcolor(title="Escolha a cor")
        if rgb:
            threading.Thread(target=self._manual_worker,
                             args=("colour", tuple(int(x) for x in rgb)), daemon=True).start()

    def _manual_worker(self, action, rgb):
        try:
            ss.quick_command(action, rgb)
            self.log_q.put(f"[+] manual: {action} {rgb or ''}")
        except Exception as e:
            self.log_q.put(f"[-] manual falhou: {e}")

    # ---------- loop de atualizacao ----------
    def _poll(self):
        while not self.log_q.empty():
            self._log(self.log_q.get_nowait())
        st = None
        while not self.status_q.empty():
            st = self.status_q.get_nowait()
        if st:
            self._update_monitor(st)
        self.root.after(60, self._poll)

    def _update_monitor(self, st):
        r, g, b = st.get("rgb", (0, 0, 0))
        self.swatch.config(bg=f"#{r:02x}{g:02x}{b:02x}")
        self.lbl_song.config(text=st.get("song") or "—")
        self.lbl_artist.config(text=st.get("artist", ""))
        self.lbl_key.config(text=f"tom: {st.get('key', '—')}")
        w = st.get("warmth", 0.0)
        if w > 0.12:
            self.lbl_mood.config(text=f"● QUENTE ({w:+.2f})", foreground="#fb923c")
        elif w < -0.12:
            self.lbl_mood.config(text=f"● FRIA ({w:+.2f})", foreground="#60a5fa")
        else:
            self.lbl_mood.config(text=f"○ NEUTRA ({w:+.2f})", foreground=MUTED)
        if st.get("locked"):
            self.lbl_bpm.config(text=f"{st['bpm']:.0f} BPM")
            self.lbl_lock.config(text="● RITMO TRAVADO", foreground=ACCENT)
        else:
            self.lbl_bpm.config(text="-- BPM")
            self.lbl_lock.config(text="○ buscando ritmo", foreground=MUTED)
        self.energy_bar["value"] = max(0, min(100, st.get("energy", 0) * 100))
        self.vol_bar["value"] = max(0, min(100, st.get("vol", 0) * 100))
        sil = "  [SEM AUDIO!]" if st.get("silence") else ""
        src = st.get("source", "-")
        if src == "replay":
            fonte = f"REPLAY (faixa aprendida) {st.get('pos', 0):.0f}s"
        elif src == "mudo":
            fonte = "mudo" + ("  [faixa nao aprendida]" if not st.get("learned") else "")
        else:
            fonte = "loopback" + ("  [aprendendo]" if not st.get("learned") else "  [ja aprendida]")
        self.lbl_info.config(text=f"dispositivo: {st.get('device','-')}\n"
                                  f"modo: {st.get('mode','-')}{sil}  fonte: {fonte}\n"
                                  f"centroide: {st.get('centroid',0):.0f} Hz")

    def _on_close(self):
        try:
            if self.engine and self.engine.is_running():
                self.engine.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    Hub(root)
    root.mainloop()


if __name__ == "__main__":
    main()
