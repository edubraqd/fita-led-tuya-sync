"""Injeta hub_page.html na constante PAGE de web_hub.pyc (fonte .py nao existe).

    python tools/inject_page.py            # grava web_hub.pyc, backup em web_hub.pyc.orig
    python tools/inject_page.py --extrair  # so escreve a PAGE atual em hub_page.html

O pyc e Python 3.13; roda com o mesmo interpretador da .venv.
"""
import marshal, sys, shutil, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYC = ROOT / "web_hub.pyc"
HTML = ROOT / "hub_page.html"
MARK = "<title>NEXUS // LED SYNC</title>"


def _is_page(c):
    return isinstance(c, str) and c.startswith("<!DOCTYPE html>") and MARK in c and "/monitor" not in c[:200]


def main():
    raw = PYC.read_bytes()
    head, code = raw[:16], marshal.loads(raw[16:])
    idx = [i for i, c in enumerate(code.co_consts) if _is_page(c)]
    if len(idx) != 1:
        sys.exit(f"[-] esperava 1 constante PAGE no nivel do modulo, achei {len(idx)}")
    i = idx[0]
    if "--extrair" in sys.argv:
        HTML.write_text(code.co_consts[i], encoding="utf-8")
        print(f"[+] PAGE extraida -> {HTML} ({len(code.co_consts[i])} chars)")
        return
    new = HTML.read_text(encoding="utf-8")
    consts = list(code.co_consts)
    consts[i] = new
    code = code.replace(co_consts=tuple(consts))
    orig = PYC.with_suffix(".pyc.orig")
    if not orig.exists():
        shutil.copy2(PYC, orig)
    PYC.write_bytes(head + marshal.dumps(code))
    print(f"[+] PAGE substituida: {len(new)} chars -> {PYC} (backup: {orig.name})")


if __name__ == "__main__":
    main()
