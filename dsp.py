"""
dsp.py - nucleo matematico do motor (puro numpy, sem device de audio, testavel).

Referencias (literatura de MIR; a base de conhecimento local nao cobre o tema):
- Bock & Widmer, "Maximum filter vibrato suppression for onset detection", DAFx-13
  (SuperFlux): banco de filtros em escala log-frequencia, log10(X*F+1), max-filter
  +-1 banda, fluxo positivo; peak-picking online = maximo dos ultimos 30 ms acima
  da media local + delta.
- Grosche & Muller, "Extracting predominant local pulse information" (PLP) e a
  versao tempo-real com latencia zero (TISMIR 2024): tempograma de Fourier causal
  sobre a novelty; a fase do coeficiente no tempo escolhido posiciona o kernel
  cosseno, que se estende ao futuro -> beats previstos sem PLL.
- Peeters 2006 / Stark, Davies & Plumbley (BTrack, DAFx-09): Fourier erra a oitava
  para cima (harmonicos), ACF para baixo (sub-harmonicos); o produto dos dois
  resolve. Prior log-gaussiano em torno de 120 BPM (Ellis 2007).
- Krumhansl & Schmuckler: perfis de tonalidade (maior/menor).
- Hyndman, "Forecasting: principles and practice", 2.3: ACF como medida de
  periodicidade (unico ponto coberto pela base local).
"""

import math
from collections import deque

import numpy as np

EPS = 1e-9


# ------------------------------------------------------------------
#  Banco de filtros em escala log (semitons), triangular, >=1 bin por banda
# ------------------------------------------------------------------
def log_filterbank(freqs, fmin=40.0, fmax=16000.0, bands_per_octave=12):
    """Matriz (n_bands, n_bins) de filtros triangulares com centros em escala
    log. Centros que caem no mesmo bin sao fundidos (grave com FFT curta).
    Nao normaliza area (SuperFlux, sec. 2.1). Retorna (F, centros_hz)."""
    n_bins = len(freqs)
    df = freqs[1] - freqs[0] if n_bins > 1 else 1.0
    n_oct = math.log2(fmax / fmin)
    centers = fmin * 2.0 ** (np.arange(0, n_oct * bands_per_octave + 1) / bands_per_octave)
    centers = centers[centers <= freqs[-1]]
    cbins = np.unique(np.round(centers / df).astype(int))
    cbins = cbins[(cbins >= 1) & (cbins < n_bins)]
    if len(cbins) < 3:
        F = np.zeros((max(len(cbins), 1), n_bins), dtype=np.float32)
        for i, b in enumerate(cbins):
            F[i, b] = 1.0
        return F, freqs[cbins]
    F = np.zeros((len(cbins) - 2, n_bins), dtype=np.float32)
    for i in range(1, len(cbins) - 1):
        lo, c, hi = cbins[i - 1], cbins[i], cbins[i + 1]
        if c > lo:
            F[i - 1, lo:c + 1] = np.linspace(0.0, 1.0, c - lo + 1)
        else:
            F[i - 1, c] = 1.0
        if hi > c:
            F[i - 1, c:hi + 1] = np.linspace(1.0, 0.0, hi - c + 1)
    return F, freqs[cbins[1:-1]]


def log_compress(x, gamma):
    """log10(1 + gamma*x). gamma<=0 -> linear."""
    if gamma <= 0:
        return x
    return np.log10(1.0 + gamma * x)


# ------------------------------------------------------------------
#  Onset: SuperFlux (banda do bumbo p/ disparo, banda cheia p/ tempo)
# ------------------------------------------------------------------
class OnsetDetector:
    def __init__(self, freqs, onset_lo=20.0, onset_hi=200.0, gamma=10.0,
                 thresh_k=1.6, thresh_sigma=2.0, history=200, refractory=0.12,
                 rms_gate=0.005, fmin=40.0, fmax=16000.0, mu=1):
        self.F, self.centers = log_filterbank(freqs, fmin, fmax)
        self.kick = np.where((self.centers >= onset_lo) & (self.centers <= onset_hi))[0]
        if len(self.kick) == 0:
            self.kick = np.arange(min(4, len(self.centers)))
        self.gamma = float(gamma)
        self.k = float(thresh_k)
        self.ks = float(thresh_sigma)
        self.refractory = float(refractory)
        self.rms_gate = float(rms_gate)
        self.mu = max(1, int(mu))
        nb = self.F.shape[0]
        # peso por oitava: cada oitava soma 1 no ODF de tempo (chimbal nao domina)
        octv = np.floor(np.log2(np.maximum(self.centers, 1.0))).astype(int)
        cnt = {o: int(np.sum(octv == o)) for o in np.unique(octv)}
        self.wband = np.array([1.0 / cnt[o] for o in octv], dtype=np.float32)
        self.prev = deque([np.zeros(nb, dtype=np.float32) for _ in range(self.mu)], maxlen=self.mu)
        self.hist = deque(maxlen=int(history))
        self._sum = 0.0
        self._sumsq = 0.0
        self.prev_odf = 0.0
        self.prev_thr = 0.0
        self.last_accepted = -1e9
        self.odf = 0.0          # banda do bumbo (disparo do flash)
        self.odf_full = 0.0     # banda cheia (tempo)
        self.thr = 0.0
        self.flux = 0.0         # = odf_full (atividade)

    @staticmethod
    def mu_for(n_fft, hop, ratio=0.5):
        """Eq. 2 do SuperFlux: distancia em frames p/ que as janelas se sobreponham
        menos que `ratio` de altura (Hann)."""
        w = np.hanning(n_fft)
        first = int(np.argmax(w > ratio))
        return max(1, int(math.floor((n_fft / 2 - first) / hop + 0.5)))

    def _push_hist(self, v):
        if len(self.hist) == self.hist.maxlen:
            old = self.hist[0]
            self._sum -= old
            self._sumsq -= old * old
        self.hist.append(v)
        self._sum += v
        self._sumsq += v * v

    def process(self, mag, rms, now):
        """mag: |X| linear do rfft. Retorna (onset, strength)."""
        spec = log_compress(self.F @ mag.astype(np.float32), self.gamma).astype(np.float32)
        ref = self.prev[0]
        pm = ref.copy()
        if len(pm) > 2:
            pm[1:-1] = np.maximum(ref[1:-1], np.maximum(ref[:-2], ref[2:]))
        pos = np.maximum(spec - pm, 0.0)
        odf = float(np.sum(pos[self.kick]))
        self.odf_full = float(np.dot(pos, self.wband))
        self.flux = self.odf_full
        self.prev.append(spec)
        self._push_hist(odf)
        n = len(self.hist)
        mean = self._sum / n
        var = max(0.0, self._sumsq / n - mean * mean)
        thr = max(mean * self.k, mean + self.ks * math.sqrt(var)) + EPS
        onset = (odf > thr) and (self.prev_odf <= self.prev_thr) and \
                (now - self.last_accepted > self.refractory) and (rms > self.rms_gate)
        strength = 0.0
        if onset:
            strength = min(1.0, max(0.0, (odf / thr - 1.0) / 2.0))
            self.last_accepted = now
        self.prev_odf, self.prev_thr = odf, thr
        self.odf, self.thr = odf, thr
        return onset, strength


# ------------------------------------------------------------------
#  Tempo + fase: tempograma de Fourier x ACF x prior (PLP tempo-real)
# ------------------------------------------------------------------
class TempoEstimator:
    def __init__(self, frame_rate, min_bpm=80.0, max_bpm=160.0, window_s=6.0,
                 prior_bpm=120.0, prior_sigma_oct=1.0, bpm_step=1.0):
        self.fr = float(frame_rate)
        self.n = int(round(window_s * self.fr))
        self.buf = np.zeros(self.n, dtype=np.float32)
        self.pos = 0
        self.filled = 0
        # pontua numa grade larga (min/2 .. max*2) e dobra o vencedor p/ [min, max)
        self.min_bpm, self.max_bpm = float(min_bpm), float(max_bpm)
        self.bpms = np.arange(min_bpm / 2.0, max_bpm * 2.0 + 1e-6, bpm_step)
        self.omega = self.bpms / 60.0 / self.fr                  # ciclos por frame
        self.prior = np.exp(-0.5 * (np.log2(self.bpms / prior_bpm) / prior_sigma_oct) ** 2)
        # janela causal (metade direita de Hann): frames m' = -(n-1)..0
        m = np.arange(-(self.n - 1), 1, dtype=np.float64)
        self.win = (0.5 * (1.0 + np.cos(np.pi * m / self.n))).astype(np.float64)
        self.E = np.exp(-2j * np.pi * np.outer(self.omega, m)) * self.win   # (n_bpm, n)
        self.wsum = float(self.win.sum())
        self.period = None
        self.bpm_raw = 0.0
        self.confidence = 0.0
        self.t_last = 0.0
        self.tf = None
        self.ta = None

    def reset(self):
        self.buf[:] = 0.0
        self.pos = 0
        self.filled = 0
        self.period = None
        self.confidence = 0.0

    def push(self, odf, t):
        self.buf[self.pos] = odf
        self.pos = (self.pos + 1) % self.n
        if self.filled < self.n:
            self.filled += 1
        self.t_last = t

    def ready(self):
        return self.filled >= int(2.0 * self.fr)   # >= 2 s de historico

    def _ordered(self):
        if self.filled < self.n:
            x = np.zeros(self.n, dtype=np.float32)
            x[self.n - self.filled:] = self.buf[:self.filled]
            return x
        return np.concatenate((self.buf[self.pos:], self.buf[:self.pos]))

    @staticmethod
    def _acf(x):
        x = x - x.mean()
        n = len(x)
        nfft = 1 << int(math.ceil(math.log2(2 * n)))
        X = np.fft.rfft(x, nfft)
        r = np.fft.irfft(X * np.conj(X), nfft)[:n]
        r0 = r[0] if r[0] > EPS else EPS
        return (r / r0) * (n / np.maximum(n - np.arange(n), 1))

    def _comb_score(self, x, m_next, pf):
        """Soma do ODF (max +-1 frame) nas posicoes passadas da grade de periodo pf
        (frames) que passa por m_next frames a frente do frame atual."""
        n = len(x)
        m = (n - 1) + m_next - pf
        s, ws = 0.0, 0.0
        while m >= 1:
            i = int(round(m))
            s += float(x[max(0, i - 1):min(n, i + 2)].max()) * self.win[i]
            ws += self.win[i]
            m -= pf
        return s / ws if ws > 0 else 0.0     # media ponderada: sem vies p/ o candidato mais recente

    def estimate(self, prev_next_beat=None):
        """Retorna (periodo_s | None, confianca 0..1, t_proximo_beat | None).
        prev_next_beat: beat previsto pela grade atual (s), p/ histerese na fase."""
        if not self.ready():
            return None, 0.0, None
        x = self._ordered().astype(np.float64)
        xm = x - (x * self.win).sum() / self.wsum
        Fc = self.E @ xm                                         # tempograma de Fourier causal
        tf = np.abs(Fc) / (np.abs(xm * self.win).sum() + EPS)
        r = self._acf(x)
        lag = self.fr * 60.0 / self.bpms
        ta = np.interp(lag, np.arange(len(r)), r)
        ta = np.clip(ta, 0.0, 1.0)
        score = tf * ta * self.prior
        i = int(np.argmax(score))
        bpm = float(self.bpms[i])
        if 0 < i < len(score) - 1:                               # pico interpolado
            a, b, c = score[i - 1], score[i], score[i + 1]
            den = a - 2 * b + c
            if abs(den) > EPS:
                d = 0.5 * (a - c) / den
                if -1.0 < d < 1.0:
                    bpm += d * (self.bpms[1] - self.bpms[0])
        conf = float(ta[i])
        self.tf, self.ta = tf, ta
        self.bpm_raw = bpm
        # dobra de oitava p/ dentro da faixa (BTrack): 70 -> 140, 174 -> 87
        folded = bpm
        while folded < self.min_bpm:
            folded *= 2.0
        while folded >= self.max_bpm:
            folded /= 2.0
        self.period = 60.0 / folded
        self.confidence = conf
        # fase no tempo ORIGINAL (onde ha energia; no sub-harmonico o coeficiente e ~0):
        # kernel cos(2pi(w m' - phi)); pico em m' = (phi + k)/w. A grade dobrada contem esse beat.
        phi = -math.atan2(Fc[i].imag, Fc[i].real) / (2.0 * math.pi)
        w = bpm / 60.0 / self.fr
        m0 = (phi % 1.0) / w                                      # frames a frente do frame atual
        p_raw = 1.0 / w
        pf = self.period * self.fr
        m_next = m0
        if pf > 1.5 * p_raw:
            # dobrou p/ baixo: qual dos sub-beats e o beat? comb de pulsos no ODF decide
            nsub = int(round(pf / p_raw))
            cands = [(m0 + j * p_raw) % pf for j in range(nsub)]
            scores = [self._comb_score(x, c, pf) for c in cands]
            best = int(np.argmax(scores))
            if prev_next_beat is not None:
                # histerese: mantem a grade atual se empata (ate 20% abaixo do melhor)
                mp = (prev_next_beat - self.t_last) * self.fr
                d = [abs(((c - mp) + 0.5 * pf) % pf - 0.5 * pf) for c in cands]
                keep = int(np.argmin(d))
                if scores[keep] >= 0.8 * scores[best]:
                    best = keep
            m_next = cands[best]
        t_next = self.t_last + m_next / self.fr
        return self.period, conf, t_next


# ------------------------------------------------------------------
#  Beat tracker: tempo/fase do tempograma; mediana de IBI como fallback (replay)
# ------------------------------------------------------------------
class BeatTracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ibis = deque(maxlen=int(cfg["IBI_WINDOW"]))
        self.last_onset = None
        self.period = None
        self.locked = False
        self.next_beat = None
        self.acf_period = None
        self.acf_conf = 0.0
        self.acf_locked = False
        self._lock_hits = 0
        self._unlock_hits = 0

    def bpm(self):
        return (60.0 / self.period) if self.period else 0.0

    def set_tempo(self, period, conf, t_next=None):
        """Alimentado pelo TempoEstimator (~4x/s). Histerese no lock."""
        c = self.cfg
        th = float(c.get("TEMPO_LOCK_CONF", 0.25))
        if period is None:
            self.acf_period = None
            self.acf_conf = 0.0
            self.acf_locked = False
            self._lock_hits = self._unlock_hits = 0
            self._recompute()
            return
        self.acf_conf = conf
        if conf >= th:
            self._lock_hits += 1
            self._unlock_hits = 0
        elif conf < 0.6 * th:
            self._unlock_hits += 1
            self._lock_hits = 0
        if not self.acf_locked and self._lock_hits >= 2:
            self.acf_locked = True
        elif self.acf_locked and self._unlock_hits >= 3:
            self.acf_locked = False
        if self.acf_period is None or not self.acf_locked or \
                abs(period - self.acf_period) > 0.08 * self.acf_period:
            self.acf_period = period
        else:
            self.acf_period += 0.3 * (period - self.acf_period)
        self._recompute()
        if t_next is not None and self.acf_locked and self.period:
            P = self.period
            if self.next_beat is None:
                self.next_beat = t_next
            else:
                # alinha a grade atual ao beat previsto (erro embrulhado em [-P/2, P/2))
                err = (t_next - self.next_beat + 0.5 * P) % P - 0.5 * P
                gain = float(c.get("PHASE_GAIN", 0.2)) * 2.5
                self.next_beat += min(1.0, gain) * err

    def update(self, t, strength=1.0):
        """Onset observado (audio ou replay)."""
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
        if self.acf_locked:
            return                       # fase vem do tempograma, nao do onset
        if self.period:
            if self.next_beat is None:
                self.next_beat = t + self.period
            elif self.locked:
                err = (t - self.next_beat + 0.5 * self.period) % self.period - 0.5 * self.period
                if abs(err) < self.period * 0.35:
                    gain = c["PHASE_GAIN"] * (0.5 + 0.5 * max(0.0, min(1.0, strength)))
                    self.next_beat += gain * err
                else:
                    self.next_beat = t + self.period
            else:
                self.next_beat = t + self.period

    def _recompute(self):
        c = self.cfg
        ibi_period, ibi_locked = None, False
        if len(self.ibis) >= int(c["LOCK_MIN_SAMPLES"]):
            arr = sorted(self.ibis)
            n = len(arr)
            ibi_period = arr[n // 2] if n % 2 else 0.5 * (arr[n // 2 - 1] + arr[n // 2])
            mean = sum(self.ibis) / n
            std = (sum((x - mean) ** 2 for x in self.ibis) / n) ** 0.5
            ibi_locked = ibi_period > 0 and (std / ibi_period) < c["LOCK_TOL"]
        if self.acf_locked and self.acf_period:
            self.period = self.acf_period
            self.locked = bool(c["ENABLE_PREDICTION"])
        else:
            self.period = ibi_period
            self.locked = bool(c["ENABLE_PREDICTION"]) and ibi_locked

    def next_future_beat(self, now):
        if not self.locked or self.next_beat is None or not self.period:
            return None
        while now >= self.next_beat:
            self.next_beat += self.period
        return self.next_beat


# ------------------------------------------------------------------
#  Chroma com FFT longa (lobulo Hann +-5,9 Hz: semitom a partir de ~130 Hz) + tonalidade K-S
# ------------------------------------------------------------------
PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_KMAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KMIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_KMAJ_c = _KMAJ - _KMAJ.mean(); _KMAJ_n = np.linalg.norm(_KMAJ_c) + 1e-9
_KMIN_c = _KMIN - _KMIN.mean(); _KMIN_n = np.linalg.norm(_KMIN_c) + 1e-9


class ChromaExtractor:
    def __init__(self, sample_rate, n_fft=16384, fmin=130.0, fmax=5000.0, gamma=10.0):
        self.n = int(n_fft)
        self.gamma = float(gamma)
        self.window = np.hanning(self.n).astype(np.float32)
        freqs = np.fft.rfftfreq(self.n, d=1.0 / sample_rate)
        mask = (freqs >= fmin) & (freqs <= fmax)
        self.bins = np.where(mask)[0]
        midi = 69.0 + 12.0 * np.log2(np.maximum(freqs[self.bins], 1.0) / 440.0)
        note = np.round(midi).astype(int)
        # peso: penaliza bins a mais de 1/4 de semitom do centro (entre notas)
        frac = np.abs(midi - np.round(midi))
        self.w = np.clip(1.0 - 2.0 * frac, 0.25, 1.0).astype(np.float32)
        # banda por semitom = media dos bins (senao agudo, com 40 bins/semitom, domina)
        notes = np.unique(note)
        self.note_idx = np.searchsorted(notes, note)
        self.note_w = np.zeros(len(notes), dtype=np.float32)
        np.add.at(self.note_w, self.note_idx, self.w)
        self.note_pc = np.mod(notes, 12)

    def compute(self, audio):
        """audio: ultimos n_fft samples (float). Retorna chroma(12) somando 1 (ou zeros)."""
        if len(audio) < self.n:
            audio = np.pad(audio, (self.n - len(audio), 0))
        mag = np.abs(np.fft.rfft(audio[-self.n:] * self.window)) / (self.n / 4.0)   # ~amplitude
        band = np.zeros(len(self.note_w), dtype=np.float64)
        np.add.at(band, self.note_idx, mag[self.bins] * self.w)
        band = log_compress(band / np.maximum(self.note_w, EPS), self.gamma)
        ch = np.zeros(12, dtype=np.float64)
        np.add.at(ch, self.note_pc, band)
        s = ch.sum()
        if s > EPS:
            ch /= s
        return ch


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


# ------------------------------------------------------------------
#  Loudness perceptivo (dB) -> 0..1
# ------------------------------------------------------------------
def loudness_db(rms, db_min=-50.0, db_ref=-16.0):
    db = 20.0 * math.log10(max(rms, 1e-6))
    return max(0.0, min(1.0, (db - db_min) / max(db_ref - db_min, 1e-6)))
