r"""Le logs/motor-*.jsonl e mostra, por faixa, o resumo e os trechos em que a luz ficou "parada".
Uso:  .venv\Scripts\python.exe tools\log_report.py [arquivo.jsonl | AAAAMMDD]   (padrao: hoje)
      ... --detalhe   mostra os eventos em volta de cada trecho parado
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import diaglog  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pick(arg):
    if arg and os.path.isfile(arg):
        return arg
    if arg and arg.isdigit():
        return os.path.join(BASE, "logs", f"motor-{arg}.jsonl")
    files = sorted(glob.glob(os.path.join(BASE, "logs", "motor-*.jsonl")))
    return files[-1] if files else None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    detail = "--detalhe" in sys.argv
    path = pick(args[0] if args else None)
    if not path or not os.path.isfile(path):
        print("nenhum log em logs/ (DIAG_LOG ligado? o motor ja rodou?)")
        return 1
    print(f"== {path}")
    rep = diaglog.report(path)
    evs = None
    for tr in rep:
        t0 = tr["t0"] or 0.0
        print(f"\n[{tr['artist']} - {tr['title']}]")
        print("  " + diaglog.format_summary(tr["summary"]))
        for w in tr["stuck"]:
            print(f"  PARADA {w['t0'] - t0:6.1f}s -> {w['t1'] - t0:6.1f}s: {w['beats']} batidas a "
                  f"{w['bpm']:.0f} BPM, amplitude {w['contrast']:.2f}")
            if detail:
                if evs is None:
                    evs = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
                for e in evs:
                    if e.get("t") is not None and w["t0"] - 0.3 <= e["t"] <= w["t1"] + 0.3 \
                            and e["k"] in ("beat", "send", "gate", "lock", "unlock"):
                        extra = {k: v for k, v in e.items() if k not in ("k", "t", "rgb")}
                        print(f"      {e['t'] - t0:8.3f}s {e['k']:6s} {extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
