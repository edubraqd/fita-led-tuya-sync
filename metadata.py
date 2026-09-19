#!/usr/bin/env python3
"""
metadata.py — Musica tocando agora (Windows SMTC, 100% local)
=============================================================
Le titulo/artista/album do que o Spotify esta tocando via
GlobalSystemMediaTransportControls. Sem OAuth, sem API key.

So o Spotify e referencia musical. Com YOUTUBE_ENABLE o navegador (onde o
YouTube toca) tambem entra como candidato; quem esta tocando ganha, empate vai
pro Spotify. Nunca usa a sessao "atual" do Windows (podia ser a Ferramenta de
Captura com um video aberto).

Obs: o Spotify descontinuou audio-features (tempo/valence/mode) p/ apps novos
(403 desde nov/2024), entao ritmo e modalidade saem da nossa analise LOCAL.
Aqui pegamos so a identidade da faixa (e detectamos troca de musica).
"""

import asyncio
import threading
import time
import datetime as _dt

clock = time.perf_counter

# Enum GlobalSystemMediaTransportControlsSessionPlaybackStatus
PLAYING = 4

# AppUserModelId (lower) dos navegadores no SMTC; 308046b0af4a39cb = Firefox
BROWSER_IDS = ("chrome", "msedge", "brave", "opera", "vivaldi", "firefox",
               "308046b0af4a39cb", "youtube")


def _is_playing(sess):
    try:
        info = sess.get_playback_info()
        return bool(info) and int(info.playback_status) == PLAYING
    except Exception:
        return False


def _pick_session(sessions, allow_youtube):
    """Spotify sempre; navegador (YouTube) so com a flag. Tocando ganha."""
    spotify = browser = None
    for cand in sessions:
        app = (cand.source_app_user_model_id or "").lower()
        if "spotify" in app:
            spotify = spotify or cand
        elif allow_youtube and any(b in app for b in BROWSER_IDS):
            browser = browser or cand
    ranked = [x for x in (spotify, browser) if x is not None]
    if not ranked:
        return None
    for cand in ranked:
        if _is_playing(cand):
            return cand
    return ranked[0]


def _fetch(allow_youtube=False):
    """Le a sessao de midia uma vez. Retorna dict ou None."""
    try:
        from winrt.windows.media.control import \
            GlobalSystemMediaTransportControlsSessionManager as MM
    except Exception:
        return None

    async def go():
        try:
            mgr = await MM.request_async()
            s = _pick_session(list(mgr.get_sessions()), allow_youtube)
            if not s:
                return None
            p = await s.try_get_media_properties_async()
            info = s.get_playback_info()
            status = int(info.playback_status) if info else 0
            # Posicao: o Spotify so atualiza a timeline a cada ~4-5 s (tambem
            # no Connect, tocando na Alexa); extrapola pela idade da ancora.
            pos = None
            duration = 0.0
            try:
                tl = s.get_timeline_properties()
                duration = float(tl.end_time.total_seconds())
                pos = float(tl.position.total_seconds())
                if status == PLAYING:
                    age = (_dt.datetime.now(_dt.timezone.utc) - tl.last_updated_time).total_seconds()
                    pos += max(0.0, min(age, 30.0))
            except Exception:
                pos = None
            return {
                "title": p.title or "",
                "artist": p.artist or "",
                "album": p.album_title or "",
                "status": status,
                "app": s.source_app_user_model_id or "",
                "pos": pos, "duration": duration, "pos_clock": clock(),
            }
        except Exception:
            return None

    try:
        return asyncio.run(go())
    except Exception:
        return None


class NowPlaying(threading.Thread):
    """Poll em segundo plano. .get() devolve a faixa atual; on_change(info)
    dispara na troca de musica. allow_youtube: bool ou callable lido a cada
    poll (flag YOUTUBE_ENABLE do config, muda ao vivo)."""

    def __init__(self, interval=0.5, on_change=None, allow_youtube=False):
        super().__init__(daemon=True)
        self.interval = interval
        self.on_change = on_change
        self.allow_youtube = allow_youtube
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._cur = {"title": "", "artist": "", "album": "", "status": 0, "app": "",
                     "pos": None, "duration": 0.0, "pos_clock": 0.0}
        self._offset = None      # pos - clock(), suavizado entre ancoras

    def get(self):
        with self._lock:
            return dict(self._cur)

    def position(self):
        """(posicao na faixa em s ou None, tocando?) — extrapolada agora."""
        with self._lock:
            off, st = self._offset, self._cur["status"]
        if off is None:
            return None, st == PLAYING
        return off + clock(), st == PLAYING

    def _update_offset(self, info, track_changed):
        pos = info.get("pos")
        if pos is None:
            return
        if info["status"] != PLAYING:
            self._offset = None      # pausado: posicao parada, nao extrapola
            return
        off = pos - info["pos_clock"]
        if self._offset is None or track_changed or abs(off - self._offset) > 0.8:
            self._offset = off       # seek / troca de faixa: pula
        else:
            self._offset += 0.3 * (off - self._offset)   # jitter da ancora (~0.1-0.3 s)

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            from winrt import system
            system.init_apartment()
        except Exception:
            pass
        last_key = None
        while not self._stop.is_set():
            allow = self.allow_youtube
            if callable(allow):
                allow = allow()
            info = _fetch(bool(allow))
            if info and info.get("title"):
                key = (info["title"], info["artist"])
                changed = key != last_key
                with self._lock:
                    self._cur = info
                    self._update_offset(info, changed)
                if changed:
                    last_key = key
                    if self.on_change:
                        try:
                            self.on_change(info)
                        except Exception:
                            pass
            self._stop.wait(self.interval)


if __name__ == "__main__":
    import time
    np_ = NowPlaying(on_change=lambda i: print("TROCOU ->", i["artist"], "-", i["title"]))
    np_.start()
    try:
        for _ in range(10):
            print("atual:", np_.get()["title"], "pos:", np_.position())
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    np_.stop()
