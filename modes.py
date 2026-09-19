"""
modes.py — modos opcionais do motor (spotify_sync.SyncEngine)
=============================================================
Cada modo e uma classe pequena que le o cfg VIVO (dict compartilhado com o hub)
e nao guarda estado global. Se o modulo compilado que o modo precisa (structure,
ble_strip) nao carregar, o modo fica inerte e o motor segue.

  ColorFX        ALIEN (eletronica: verde->violeta, punch seco, "abducao")
                 PICANTE (sensual: vinho<-grave->pessego, brasa no piso, pulso longo, sem estrobo)
                 REGGAE (pacifico: paleta rasta lenta, pulso longo, piso alto, sem estrobo)
  DropDetector   AUTO: drop ao vivo por contraste estrutural (grave+energia sobem
                 depois de uma queda) na grade de 0,5 s
  Structure      SECOES/ANTECIPA: envolve structure.SongStructure (pyc)
  BLEMirror      2a FITA BLE: replica a cor p/ ble_strip.BLEStrip (pyc)
  output_delay   LED SEGUE BT: atraso da luz = latencia ouvida da caixa BT
  Meditation     MEDITACAO: sem batida; luz respira (~6/min) e segue a intensidade
                 do audio; cenas FLAUTA (ambar->creme), SELVA (verde), CHUVA (teal)
"""
import colorsys
import math
import random

GRID = 0.5          # s; mesma grade de structure.pyc
ABDUCT_PULSE = 0.15 # s por pulso do estrobo
ABDUCT_PULSES = 3
ABDUCT_REFRACT = 2.5


def output_delay(cfg):
    """Atraso da saida de audio visto pela luz. LED_FOLLOW_BT usa a latencia
    ouvida pelo mic (escrita por acoustic_sync) quando existir."""
    if cfg.get("LED_FOLLOW_BT"):
        heard = float(cfg.get("BT_HEARD_LATENCY") or 0.0)
        if heard > 0.0:
            return heard
    return float(cfg["OUTPUT_DELAY"])


class ColorFX:
    """Efeitos de cor sobre (hue, sat, value) antes do envio. ALIEN x PICANTE x REGGAE
    exclusivos (o hub ja garante; aqui ALIEN ganha se mais de um vier ligado)."""

    def __init__(self, cfg, rng=None):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self._abd_t0 = -1e9
        self.abductions = 0
        self._bass = 0.2            # EMA do grave (PICANTE), tau SPICY_BASS_TAU
        self._bass_t = None

    @property
    def mode(self):
        if self.cfg.get("ALIEN_ENABLE"):
            return "alien"
        if self.cfg.get("SPICY_ENABLE"):
            return "spicy"
        if self.cfg.get("REGGAE_ENABLE"):
            return "reggae"
        return None

    # ---- parametros que o motor consulta -------------------------------------
    def pulse_scale(self):
        """Multiplica a duracao do envelope da batida."""
        return {"alien": 0.7, "spicy": 1.4, "reggae": 1.6}.get(self.mode, 1.0)

    def floor_bias(self):
        """Somado ao piso entre batidas (antes de multiplicar pelo volume)."""
        return {"alien": -0.05, "spicy": 0.15}.get(self.mode, 0.0)

    def peak_cap(self):
        """Teto do pico da batida (PICANTE: brasa, nao flash)."""
        return 0.85 if self.mode == "spicy" else 1.0

    def drop_rgb(self):
        """Cor do clarao no drop: branco padrao, rosa-quente no PICANTE, None = sem clarao (REGGAE)."""
        m = self.mode
        if m == "reggae":
            return None
        if m == "spicy":
            rf, gf, bf = colorsys.hsv_to_rgb(0.95, 0.5, 1.0)
            return int(rf * 255), int(gf * 255), int(bf * 255)
        return 255, 255, 255

    def floor_min(self):
        """Piso minimo entre batidas (REGGAE: REGGAE_FLOOR); 0 = sem minimo."""
        return float(self.cfg.get("REGGAE_FLOOR", 0.35)) if self.mode == "reggae" else 0.0

    @property
    def calm(self):
        """Sem flash de energia, sem batida seca (o drop vai por drop_rgb)."""
        return self.mode in ("reggae", "spicy")

    def offbeat(self):
        """REGGAE_OFFBEAT: soco no contratempo (fase +P/2)."""
        return self.mode == "reggae" and bool(self.cfg.get("REGGAE_OFFBEAT"))

    def on_onset(self, now, strength, energy):
        """ALIEN: chance de abducao em onset forte. Retorna True se disparou."""
        if self.mode != "alien":
            return False
        if now - self._abd_t0 < ABDUCT_PULSE * ABDUCT_PULSES + ABDUCT_REFRACT:
            return False
        drive = 0.5 * strength + 0.5 * energy
        if drive < 0.55:
            return False
        p = min(1.0, float(self.cfg.get("ALIEN_FLASH_MIN", 0.08)) * 5.0 * drive)
        if self.rng.random() >= p:
            return False
        self._abd_t0 = now
        self.abductions += 1
        return True

    def abducting(self, now):
        return 0.0 <= now - self._abd_t0 < ABDUCT_PULSE * ABDUCT_PULSES

    def apply(self, now, hue, sat, value, bass=None):
        """(hue, sat, value) -> (hue, sat, value, jump). hue em 0..HUE_MAX do motor;
        bass = fracao de energia em 20-200 Hz (so o PICANTE usa)."""
        m = self.mode
        if m is None:
            return hue, sat, value, False
        hmax = float(self.cfg.get("HUE_MAX", 0.75)) or 0.75
        n = max(0.0, min(1.0, hue / hmax))
        if m == "alien":
            if self.abducting(now):
                k = int((now - self._abd_t0) / ABDUCT_PULSE)
                if k % 2 == 0:
                    return 0.42, 0.25, 1.0, True          # clarao branco-esverdeado
                return 0.75, 1.0, value * 0.3, True       # violeta escuro entre pulsos
            return 0.33 + 0.52 * n, max(sat, 0.8), value, False
        if m == "reggae":
            # rasta lenta: vermelho -> ambar -> verde -> ambar -> vermelho (triangulo, nunca azul)
            period = max(1.0, float(self.cfg.get("REGGAE_DRIFT_SECS", 40.0)))
            ph = (now / period) % 1.0
            return 0.33 * (1.0 - abs(2.0 * ph - 1.0)), max(sat, 0.8), value, False
        # spicy: grave (EMA) escolhe o centro, vinho/magenta (0,93) <- pesado ... leve -> pessego (0,05);
        # timbre so empurra +-0,02; ao escurecer puxa pro ambar (brasa), ja que vermelho em luz
        # fraca vira marrom (Itten). Grave real: mediana 0,02, p90 0,38 -> sqrt(bass/0,4).
        if bass is not None:
            dt = 0.0 if self._bass_t is None else max(0.0, min(0.5, now - self._bass_t))
            tau = float(self.cfg.get("SPICY_BASS_TAU", 0.5))
            self._bass += (1.0 - math.exp(-dt / max(tau, 1e-3))) * (max(0.0, float(bass)) - self._bass)
            self._bass_t = now
        nb = min(1.0, math.sqrt(self._bass / 0.4))
        h = 0.05 - 0.12 * nb + 0.04 * (n - 0.5) + 0.085 * (1.0 - max(0.0, min(1.0, value)))
        return h % 1.0, max(sat, 0.9), value, False


class DropDetector:
    """Drop ao vivo por CONTRASTE: media dos ultimos 2 passos (1 s) contra a
    janela de 4 s que terminou 1 s antes. Passo = GRID (0,5 s). Uma deteccao a
    cada DROP_REFRACT s."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.hist = []          # (energy, bass, flux)
        self.last_drop = -1e9
        self.score = 0.0

    def step(self, t, energy, bass, flux):
        """Retorna True se detectou drop neste passo."""
        self.hist.append((float(energy), float(bass), float(flux)))
        if len(self.hist) > 32:
            del self.hist[0]
        if len(self.hist) < 12:
            return False
        h = self.hist
        now2 = h[-2:]
        before = h[-10:-2]      # 4 s terminando 1 s antes
        far = h[-32:-10] or before
        def mean(rows, i):
            return sum(r[i] for r in rows) / len(rows)
        e_rise = mean(now2, 0) - mean(before, 0)
        b_rise = mean(now2, 1) - mean(before, 1)
        flux_n = min(1.0, mean(now2, 2))
        dip = max(0.0, mean(far, 0) - mean(before, 0))     # queda pre-drop = bonus
        self.score = 0.40 * max(0.0, e_rise) + 0.30 * max(0.0, b_rise) + 0.15 * flux_n + 0.15 * min(1.0, dip * 3.0)
        if e_rise <= 0.0 and b_rise <= 0.0:
            return False
        if self.score < float(self.cfg.get("DROP_THRESH", 0.22)):
            return False
        if t - self.last_drop < float(self.cfg.get("DROP_REFRACT", 8.0)):
            return False
        self.last_drop = t
        return True


class Structure:
    """SECOES (grava energia/grave/fluxo por posicao) + ANTECIPA (rampa antes do
    drop conhecido). Usa structure.SongStructure do pyc; sem ele fica inerte."""

    def __init__(self, cfg, log=print, path=None):
        self.cfg = cfg
        self.log = log
        self.ss = None
        self.next_drop = None
        try:
            import structure
            self.ss = structure.SongStructure(path or structure.STRUCT_PATH)
        except Exception as e:
            log(f"[!] structure.pyc indisponivel: {e} (SECOES/ANTECIPA inertes)")

    @property
    def enabled(self):
        return self.ss is not None and bool(self.cfg.get("SECTIONS_ENABLE"))

    def begin(self, info):
        if not self.enabled or not info or not info.get("title"):
            return
        try:
            self.ss.begin(info.get("artist") or "", info.get("title") or "",
                          float(info.get("duration") or 0.0))
        except Exception as e:
            self.log(f"[!] structure.begin: {e}")

    def record(self, pos, energy, bass, flux):
        if self.enabled and pos is not None:
            try:
                self.ss.record(pos, energy, bass, flux)
            except Exception:
                pass

    def add_auto_drop(self, pos):
        if self.enabled and pos is not None:
            try:
                self.ss.add_auto_drop(pos)
            except Exception:
                pass

    def commit(self):
        if self.ss is not None:
            try:
                self.ss.commit()
            except Exception as e:
                self.log(f"[!] structure.commit: {e}")

    def anticipation(self, pos):
        """(nivel 0..1, segundos ate o proximo drop ou None)."""
        if self.ss is None or pos is None or not self.cfg.get("ANTICIPATE_ENABLE"):
            self.next_drop = None
            return 0.0, None
        try:
            lvl, nxt = self.ss.anticipation(pos, float(self.cfg.get("ANTICIPATE_BUILD", 4.0)))
        except Exception:
            lvl, nxt = 0.0, None
        self.next_drop = nxt
        return max(0.0, min(1.0, float(lvl or 0.0))), nxt

    def view(self):
        try:
            return self.ss.view() if self.ss else None
        except Exception:
            return None


class BLEMirror:
    """Replica set_color p/ a 2a fita (ELK-BLEDOM) via ble_strip.pyc."""

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.strip = None

    def start(self):
        if not self.cfg.get("BLE_ENABLE") or not self.cfg.get("BLE_ADDRESS"):
            return False
        try:
            import ble_strip
            self.strip = ble_strip.BLEStrip(self.cfg["BLE_ADDRESS"], int(self.cfg.get("BLE_FPS", 20)),
                                            self.log, self.cfg)
            self.strip.start()
            self.log(f"[+] 2a fita BLE: {self.cfg['BLE_ADDRESS']}")
            return True
        except Exception as e:
            self.strip = None
            self.log(f"[!] 2a fita BLE falhou: {e}")
            return False

    def set_cal(self, cal):
        if self.strip:
            try:
                self.strip.set_cal(cal)
            except Exception:
                pass

    def set_color(self, r, g, b):
        if self.strip and self.cfg.get("BLE_ENABLE"):
            try:
                self.strip.set_color(r, g, b)
            except Exception:
                pass

    def stop(self):
        if self.strip:
            try:
                self.strip.stop()
            except Exception:
                pass
            self.strip = None


class Meditation:
    """MEDITACAO: substitui o pipeline de batida. Brilho = piso + intensidade do
    audio (ataque ~1 s, soltura ~2,5 s) x respiracao (MED_BREATH_SECS). Cena da
    paleta por MED_SCENE ou pela palavra no titulo/artista (auto).
    Base: Universal Principles of Design (Blue/Green/Red Effects, Biophilia),
    Palmer PNAS 2013 (lento/suave -> escuro, dessaturado), Reymore & Lindsey 2025
    (timbre brilhante -> cor clara, menos saturada)."""

    SCENES = ("flauta", "selva", "chuva")
    KEYWORDS = (("chuva", ("chuva", "rain")),
                ("selva", ("selva", "jungle", "floresta", "forest")),
                ("flauta", ("flauta", "flute")))
    ATTACK, RELEASE = 1.0, 2.5      # s; envelope do volume
    BREATH_DEPTH = 0.30             # queda de brilho no fundo da expiracao
    IDLE = 0.6                      # intensidade com volume zero (antes do MED_GAIN)
    SPARK_TAU, SPARK_GAIN = 0.3, 0.2

    def __init__(self, cfg):
        self.cfg = cfg
        self._env = 0.0
        self._spark = 0.0
        self._last = None

    def scene(self, meta):
        want = str(self.cfg.get("MED_SCENE") or "auto").lower()
        if want in self.SCENES:
            return want
        text = " ".join(str((meta or {}).get(k) or "") for k in ("title", "artist")).lower()
        for name, words in self.KEYWORDS:
            if any(w in text for w in words):
                return name
        return "flauta"

    def frame(self, now, vol, centroid_norm, activity, meta):
        """-> (hue, sat, value) em 0..1; sempre transicao suave (sem jump)."""
        c = self.cfg
        dt = 0.0 if self._last is None else max(0.0, min(0.5, now - self._last))
        self._last = now
        vol = max(0.0, min(1.0, float(vol)))
        tau = self.ATTACK if vol > self._env else self.RELEASE
        self._env += (1.0 - math.exp(-dt / tau)) * (vol - self._env)
        cen = max(0.0, min(1.0, float(centroid_norm)))

        gain = float(c.get("MED_GAIN", 0.7))
        floor = float(c.get("MED_FLOOR", 0.15))
        period = max(1.0, float(c.get("MED_BREATH_SECS", 10.0)))
        breath = 1.0 - self.BREATH_DEPTH * 0.5 * (1.0 - math.cos(2.0 * math.pi * now / period))
        inten = (1.0 - gain) * self.IDLE + gain * self._env
        v = floor + (1.0 - floor) * inten * breath

        sc = self.scene(meta)
        if sc == "flauta":              # ambar -> creme: timbre brilhante clareia e dessatura
            h = 0.08 + 0.04 * cen
            s = 0.85 - 0.45 * cen
            v *= 0.9 + 0.1 * cen
        elif sc == "selva":             # verde-floresta <-> verde-ambar, deriva lenta
            h = 0.33 + 0.05 * math.sin(2.0 * math.pi * now / 47.0)
            s = 0.85
        else:                           # chuva: teal pouco saturado + cintilacao em transientes
            h = 0.54 + 0.04 * math.sin(2.0 * math.pi * now / 31.0)
            s = 0.40 - 0.10 * cen
            self._spark *= math.exp(-dt / self.SPARK_TAU)
            if float(activity) > 0.6:
                self._spark = max(self._spark, float(activity))
            v += self.SPARK_GAIN * self._spark

        w = max(0.0, min(1.0, float(c.get("MED_WARMTH", 0.0))))
        if w > 0.0:                     # noite: puxa a cena pro ambar
            h += (0.08 - h) * w
            s += (0.75 - s) * 0.5 * w
        return h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v))


class FastBeat:
    """Batida rapida "seca": acima de FAST_BPM o soco vira JUMP ao pico seguido de JUMP ao
    piso FAST_HOLD s depois. Sem envelope nem escada de fades: a luz liga/desliga nitida e
    volta a ter contraste mesmo a 130-150 BPM, onde o decay normal nao chega ao piso."""

    def __init__(self, cfg):
        self.c = cfg

    def active(self, bpm):
        th = float(self.c.get("FAST_BPM", 0) or 0)
        return bool(th > 0 and bpm >= th)

    def hold(self):
        return float(self.c.get("FAST_HOLD", 0.09))

    def plan(self, st, peak_rgb, floor_rgb):
        """[(t, r, g, b, jump)] a agendar para um soco em st."""
        pr, pg, pb = peak_rgb
        fr, fg, fb = floor_rgb
        return [(st, pr, pg, pb, True), (st + self.hold(), fr, fg, fb, True)]
