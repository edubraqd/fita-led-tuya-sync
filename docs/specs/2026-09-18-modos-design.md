# Modos do painel — design (2026-09-18)

## Objetivo
Grade MODOS no hub web (hub_page.html) com os 12 modos do painel NEXUS antigo,
cada um ligado a comportamento real no motor atual (spotify_sync.py), reaproveitando
os .pyc compilados (structure, ble_strip, bass_dsp, multi_out, acoustic_sync) e
reescrevendo ALIEN/PICANTE (fonte perdida).

## Semântica dos modos
| Modo | Chave | Onde vive | Efeito |
|---|---|---|---|
| ALIEN | ALIEN_ENABLE | modes.ColorFX | eletrônica: paleta verde→violeta, punch mais seco, "abdução" (estrobo 2–3 pulsos branco-esverdeado) em onset forte com prob ∝ ALIEN_FLASH_MIN |
| PICANTE | SPICY_ENABLE | modes.ColorFX | sensual: matiz puxado p/ vermelho (0.95–0.08), sat ≥ .9, decay 40 % mais longo, piso mais alto |
| SEÇÕES | SECTIONS_ENABLE | modes.Structure→structure.pyc | begin/record/commit por faixa; timeline em /api/structure |
| AUTO | AUTO_ENABLE | modes.DropDetector | drop ao vivo por contraste (grave+energia sobem após queda) → add_auto_drop + log |
| ANTECIPA | ANTICIPATE_ENABLE | modes.Structure.anticipation | build-up ANTICIPATE_BUILD s antes do drop conhecido: piso e sat sobem; no drop punch forte |
| PREDIÇÃO | ENABLE_PREDICTION | já existe | agenda beat no tempo exato |
| 2ª FITA BLE | BLE_ENABLE | modes.BLEMirror→ble_strip.pyc | replica cor p/ fita ELK-BLEDOM; engine._ble p/ set_cal do hub |
| ÁUDIO 3D | SPATIAL_ENABLE | multi_out.pyc (lê no run) | reinicia espelho |
| GRAVE+ | BASS_ENABLE | multi_out/bass_dsp (apply_bass ao vivo) | — |
| RESYNC ACUST. | ACOUSTIC_SYNC | multi_out→acoustic_sync (lê no run) | reinicia espelho; escreve BT_HEARD_LATENCY |
| LED SEGUE BT | LED_FOLLOW_BT | spotify_sync comp | comp = BT_HEARD_LATENCY − BULB_LATENCY quando > 0 |
| AUTO-SHED BT | MIRROR_AUTOSHED | multi_out (lê ao vivo) | — |

ALIEN × PICANTE exclusivos (web_hub set_cfg já força).

## Componentes
- `spotify_sync.DEFAULTS`: chaves novas (defaults = os que os pyc usam).
- `modes.py` (novo, puro): `ColorFX`, `DropDetector`, `Structure`, `BLEMirror`, `follow_bt_comp()`.
  Tudo com `cfg` compartilhado (dict vivo), sem estado global. Falha em pyc = log + modo inerte.
- `spotify_sync.SyncEngine._run`: 5 pontos de gancho (start/stop, open_track, grade 0,5 s,
  comp, antes de hsv_to_rgb). Expõe `_ble` e `_struct`.
- `hub_page.html`: grupo MODOS (grade 2 col, pílula, ativo magenta), tooltips, marca ↻ nos que reiniciam espelho.

## Testes
- `tools/test_modes.py`: ColorFX (ranges, exclusividade, abdução determinística com seed),
  DropDetector (drop sintético detecta 1×, silêncio 0×), Structure com pyc real em tmp,
  follow_bt_comp.
- `tools/engine_offline.py` com modos ligados: sem exceção.
- Painel: tools/hub_test.py em 8790.

## MEDITAÇÃO (adendo, sessão seguinte)
- `modes.Meditation(cfg).frame(now, vol, centroid_norm, activity, meta) -> (h, s, v)`; sem batida, sem ALIEN/PICANTE (pílula exclusiva `x`).
- Chaves: MEDITATION_ENABLE F, MED_SCENE "auto"|flauta|selva|chuva, MED_GAIN 0,7, MED_FLOOR 0,15, MED_BREATH_SECS 10, MED_WARMTH 0.
- Motor: gancho único antes de `base` (agenda só fade, `continue`); `emit_status()` extraído p/ os dois caminhos; replay não agenda punches em meditação.
- UI: grupo "Meditação" (need MEDITATION_ENABLE) com segmentado AUTO|FLAUTA|SELVA|CHUVA (`seg:` em SGRP, CSS .seg) + 4 sliders.
- Verificado: test_modes 23/23, engine_offline 3 cenas sem erro e 0 jump, painel 8790 (exclusividade, seg, gate).

## Adendo 2026-09-19 — log de diagnóstico + batida rápida "seca"

**Problema relatado:** em eletrônica (128–150 BPM) a luz "parece parada". Medido no harness
offline a 140 BPM antes da mudança: pico→piso mediano **246 ms** num período de 428 ms — a luz
passava mais da metade da batida ainda caindo, e cada batida recebia **2 socos** (~13 ms de
diferença) porque o realinhamento de fase do `BeatTracker.set_tempo` (4×/s) desloca a grade
uns ms e `fb != last_sched_beat` disparava de novo.

**diaglog.py** — `DiagLog(dir)` grava `logs/motor-AAAAMMDD.jsonl` (1 evento JSON/linha, buffer
com flush a cada 1 s): `track`, `onset`, `beat` (t alvo, bpm, conf, fast), `send` (t real, alvo,
ms gastos na fita, V, jump, rgb — vem do `OutputScheduler.on_send`), `gate` (1 por trecho em que
um frame mudado ficou segurado pelo `MIN_UPDATE_INTERVAL`), `lock`/`unlock`, `silence`, `replay`,
`drop`, `error`, `summary`. `TrackStats` analisa **por intervalo de batida** (V máx/mín dos envios
entre uma batida e a próxima): contraste = amplitude mediana; pico→piso = tempo do máx até
mín+20 % da amplitude; "parada" = ≥ 4 batidas seguidas com amplitude < 0,35 (limiares absolutos
de pico/piso enganavam: com `vol` 0,55 só 9 de 56 socos contavam, e `FLOOR_CALM` 0,28 deixaria
toda faixa calma "parada"). Ao trocar de faixa/parar: linha em `logs/faixas.jsonl` + `[≡] Resumo:`
no log do painel. `tools/log_report.py [arquivo|AAAAMMDD] [--detalhe]` lista por faixa.
`DIAG_LOG` liga/desliga (toggle no painel, bloco ritmo). `logs/` no .gitignore.

**modes.FastBeat** — `FAST_BPM` (124; 0 = off) e `FAST_HOLD` (0,09 s). Com BPM ≥ limiar o soco vira
`plan(st, pico, piso)` = JUMP pico em `st` + JUMP piso em `st+hold`; `punch_now=False` (sem
envelope), `last_idle = fb + hold` segura os fades até o piso sair, `last_sent` = HSV do piso. No
replay o mesmo, com `bpm_near(pos)` da faixa aprendida. Status `fx` ganha sufixo `+seco`.
Correção junto: batida só é nova se `|fb − last_sched_beat| > P/2`.

**Resultado (harness, 14 s):** 140 BPM pico→piso 246 → **90 ms**, 1,08 soco/batida, envios
164 → 76 em 10 s; 128 e 150 BPM idem 90 ms; 100 BPM (abaixo do limiar) inalterado (220 ms, erro
p90 8 ms). Ainda não visto na fita real.

## Adendo 2026-09-19 — REGGAE (pacífico)

Efeito exclusivo `REGGAE_ENABLE` (grupo x com ALIEN/PICANTE/MEDITAÇÃO), implementado como
modo de `modes.ColorFX` (não classe nova): mantém o pipeline de batida.

- Paleta: triângulo lento matiz 0,0 → 0,33 → 0,0 (vermelho → âmbar → verde → âmbar…) com
  período `REGGAE_DRIFT_SECS` (40 s); ignora o matiz da música (como PICANTE); sat ≥ 0,8; nunca jump.
- `pulse_scale` 1,6 (pulso longo). `floor_min()` = `REGGAE_FLOOR` (0,35): motor faz
  `base = max(base, fx.floor_min())`.
- `calm` = True: sem clarão branco no drop, sem jump por `ENERGY_FLASH_TH`, sem batida seca
  (`fast = ... and not fx.calm` — importa porque o tracker dobra 75 → 150 BPM no harness).
- `REGGAE_OFFBEAT` (off): `st = fb + comp + P/2` (skank). Pílula CONTRATEMPO. Só validar na fita:
  o envelope (`punch_now`) ainda sobe no tempo forte, então o soco deslocado cai seco (~30 ms).
- Harness: `engine_offline.py 90 14 REGGAE_ENABLE=1` → exit 0, rasta (azul nunca domina),
  V mín 0,39 ≥ 0,8·piso·vol, jumps = batidas (21). A 75 BPM o tracker trava em 150 (octave)
  com ou sem REGGAE — limitação pré-existente do sintético/tracker, não do modo.

## Adendo 2026-09-19 — layout do painel (UI/UX)

Base: Lei de Hick (Leis da Psicologia/UX p.34-45), Performance Load (Universal Principles
of Design p.176-177), hierarquia de ações (Refactoring UI p.1-83), "less is a bore"
(Universal Principles of UX p.8-56).

- Efeitos exclusivos (flag `x`: ALIEN/PICANTE/MEDITAÇÃO/REGGAE) viram o seletor **CENA**
  (`sceneHtml/sceneSet/paintScene`), com NORMAL = nenhum ligado. Sliders da cena (`need`
  = efeito) logo abaixo, em `details.grp.sub`. CONTRATEMPO ganhou 6º campo `need`
  (`REGGAE_ENABLE`) e só aparece com REGGAE.
- Demais toggles em `details` por bloco (`BLOCOS`: Fonte, Estrutura/predição, Saídas) com
  contador `ligados/total` no título (`paintCnt`), fechados por padrão. `Ajustes` e
  `Detecção de batida` (ex-Ritmo) também fechados.
- Barra: um botão `#go` (INICIAR sólido verde ↔ PARAR sólido vermelho por `paintRun`);
  SALVAR em contorno, âmbar só quando sujo. `stopbtn` removido.
- Centro: `.nphead` (título + pílula de fonte `#lsrc`) em vez de `.npbox` absoluto sobre o
  orbe; `#spec` ocupa `1fr`; `.center>*{min-width:0}` evita estouro no mobile.
- Telemetria: `tline(txt,cls,hist,k)` marca `k-<tipo>` (batida/tom/fonte/cor/log); chips
  `#tfil` escondem por classe `h-<tipo>` em `#term`; COR oculto por padrão.
- Tokens: `--r1/--r2/--r3` (6/10/14 px), `--ln` borda, `--dim` #6b7590 (contraste);
  scanline estática (sem animação).

## Adendo 2026-09-19 — PICANTE revisto

Medido antes (harness): 90 BPM 52 jumps/21 batidas (flash de `ENERGY_FLASH_TH`), 130 BPM 82/32 e
pico→piso 90 ms (modo seco), rgb final (59,22,5) = marrom. Drop dava clarão branco.
Base: Universal Principles of Design §112 Red Effects (vermelho ↑ atratividade); Itten, Elements of
Color p.22-46 (vermelho/laranja perdem radiância em luz fraca → marrom).

- `calm` agora = {reggae, spicy}: sem jump por energia, sem batida seca.
- `drop_rgb()`: branco padrão, PICANTE rosa-quente hsv(0,95, 0,5, 1) = (255,127,165), REGGAE None.
  Motor: `drgb = fx.drop_rgb(); if drgb is not None: schedule(*drgb, True)`.
- `peak_cap()` 0,85 no PICANTE (brasa, não flash); `peak_value()` faz `min(fx.peak_cap(), …)`.
- `floor_bias` PICANTE 0,10 → 0,15.
- `apply(..., bass=)`: EMA do grave (`SPICY_BASS_TAU` 0,5 s; grave real mediana 0,02, p90 0,38 →
  `sqrt(bass/0,4)`) escolhe o centro do matiz: pesado → 0,93 (vinho/magenta), leve → 0,05 (pêssego);
  timbre só ±0,02; **dim-to-warm** `+0,085·(1−V)` (piso puxa âmbar, pico fica vermelho).
  Motor passa `bass=bass_ratio` nos três `apply`.
- Depois (harness): 90 BPM 21 jumps/21 batidas, V mín 0,35, banda vermelha; 130 BPM 32/32,
  pico→piso 146 ms (sem seco). engine_offline: critério `spicy:` (red, V mín ≥ 0,4·vol, jumps ≤
  batidas+1) e check de seco pulado quando `calm`.
- Não feito (a validar na fita antes): ondulação lenta de V entre batidas; canal branco quente
  (`colourtemp`) como piso — spike de 10 min na fita real.
