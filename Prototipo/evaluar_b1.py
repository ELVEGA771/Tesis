"""Bloque 1 - fidelidad de construccion: grafo del pipeline frente al gold.

    python evaluar_b1.py --particion desarrollo

Solo cuentan los ADR anotados de la particion. Emparejamiento (uno a uno, voraz):
  - decision: las evidencias de pipeline y gold se solapan en el texto del ADR
    (>= 50 % de la mas corta, por posicion de caracteres; ambas son literales);
  - alternativa: mismo nombre normalizado, uno contenido en el otro, o solape de evidencia.
Ademas: exactitud de la alternativa elegida y del estado normalizado, P/R de
relaciones (tipo, nuevo, afectado) y tasa de nodos no fundamentados.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import networkx as nx

from comun import CORPUS, DATOS, particion, texto_adr

sys.path.insert(0, str(CORPUS / "anotacion"))
from csv_a_grafo import adr_id as nodo_adr  # noqa: E402
from conformidad import comprobar  # noqa: E402


def _g(data):
    return nx.node_link_graph(data, directed=True, multigraph=True, edges="links")


def _out(G, n, rel):
    return [v for _, v, d in G.out_edges(n, data=True) if d.get("relation") == rel] if n in G else []


def _span(texto, ev):
    ev = re.sub(r"\s+", " ", ev or "").strip()
    t = re.sub(r"\s+", " ", texto)
    i = t.find(ev) if ev else -1
    return (i, i + len(ev)) if i >= 0 else None


def _solapa(texto, a, b):
    sa, sb = _span(texto, a), _span(texto, b)
    if not sa or not sb:
        return False
    inter = min(sa[1], sb[1]) - max(sa[0], sb[0])
    return inter > 0 and inter >= 0.5 * min(sa[1] - sa[0], sb[1] - sb[0])


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _empareja(pred, gold, igual):
    usados, tp = set(), 0
    for p in pred:
        for j, g in enumerate(gold):
            if j not in usados and igual(p, g):
                usados.add(j)
                tp += 1
                break
    return tp


def prf(tp, np_, ng):
    p = tp / np_ if np_ else (1.0 if not ng else 0.0)
    r = tp / ng if ng else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--particion", default="desarrollo")
    args = ap.parse_args()
    gold = _g(json.load(open(CORPUS / "anotacion" / "gold_anotado.json", encoding="utf-8")))
    part = particion()
    acc = {k: [0, 0, 0] for k in ("decision", "alternativa", "relacion")}
    elegida_ok = elegida_n = estado_ok = estado_n = 0
    fund_ok = fund_tot = 0
    filas = []
    for clave, p in part.items():
        repo, archivo = clave.split("|")
        a = nodo_adr(repo, archivo)
        if p != args.particion or not _out(gold, a, "hasDecisionStatement"):
            continue
        f = DATOS / "grafos" / args.particion / f"{a}.json"
        if not f.exists():
            print("sin corrida:", clave)
            continue
        P = _g(json.load(open(f, encoding="utf-8"))["grafo"])
        texto = texto_adr(repo, archivo)
        ev = lambda G, n: G.nodes[n].get("evidenceText", "")  # noqa: E731

        dp, dg = _out(P, a, "hasDecisionStatement"), _out(gold, a, "hasDecisionStatement")
        tp = _empareja(dp, dg, lambda x, y: _solapa(texto, ev(P, x), ev(gold, y)))
        acc["decision"] = [acc["decision"][0] + tp, acc["decision"][1] + len(dp), acc["decision"][2] + len(dg)]

        ap_, ag = _out(P, a, "considersAlternative"), _out(gold, a, "considersAlternative")

        def alt_igual(x, y):
            nx_, ny = _norm(P.nodes[x].get("alternativeName")), _norm(gold.nodes[y].get("alternativeName"))
            return (nx_ and ny and (nx_ == ny or nx_ in ny or ny in nx_)) or _solapa(texto, ev(P, x), ev(gold, y))
        tp = _empareja(ap_, ag, alt_igual)
        acc["alternativa"] = [acc["alternativa"][0] + tp, acc["alternativa"][1] + len(ap_), acc["alternativa"][2] + len(ag)]

        sg, sp = _out(gold, a, "selectsAlternative"), _out(P, a, "selectsAlternative")
        if sg:
            elegida_n += 1
            elegida_ok += bool(sp) and alt_igual(sp[0], sg[0])

        eg = _out(gold, a, "hasStatusStatement")
        if eg:
            estado_n += 1
            ep = _out(P, a, "hasStatusStatement")
            estado_ok += bool(ep) and _out(P, ep[0], "hasNormalizedStatus") == _out(gold, eg[0], "hasNormalizedStatus")

        def rels(G):
            out = set()
            for n in _out(G, a, "declaringADR"):
                src = [u for u, _, d in G.in_edges(n, data=True) if d.get("relation") == "sourceADR"]
                dst = [u for u, _, d in G.in_edges(n, data=True) if d.get("relation") == "targetADR"]
                out.add((tuple(_out(G, n, "relationType")), tuple(src), tuple(dst)))
            return out
        rp, rg = rels(P), rels(gold)
        acc["relacion"] = [acc["relacion"][0] + len(rp & rg), acc["relacion"][1] + len(rp), acc["relacion"][2] + len(rg)]

        _, met = comprobar(P, CORPUS / "adr")
        fund_ok += met["grounded_ok"]
        fund_tot += met["grounded_total"]
        filas.append((archivo, len(dp), len(dg), len(ap_), len(ag)))

    print(f"Bloque 1 · particion {args.particion} · {len(filas)} ADR anotados")
    for k, (tp, np_, ng) in acc.items():
        p, r, f1 = prf(tp, np_, ng)
        print(f"  {k:<12} P={p:.2f} R={r:.2f} F1={f1:.2f}  (tp={tp}, pred={np_}, gold={ng})")
    print(f"  alternativa elegida correcta: {elegida_ok}/{elegida_n}")
    print(f"  estado normalizado correcto:  {estado_ok}/{estado_n}")
    print(f"  tasa de nodos no fundamentados: {1 - fund_ok / fund_tot if fund_tot else 0:.3f} ({fund_tot - fund_ok}/{fund_tot})")


if __name__ == "__main__":
    main()
