"""Prueba del esquema esquema_plan con dos cadenas REALES del corpus.

Repositorio DFE-Digital/early-years-foundation-recovery:
  0006-content-storage-strategy.md  --superseded by-->  0011-contentful.md  (solo lo declara 0006)
  0018-user-tracking.md              --superseded by-->  0023-clarity-metrics.md  (lo declaran ambos)

Los individuos se anotaron a mano leyendo los ficheros. Convencion usada para
RelationDeclaration: relationType en {"supersedes", "amends"}, sourceADR = el
ADR nuevo, targetADR = el ADR afectado, declaresRelation = el documento que lo
afirma. Al final se compara con ground_truth_reemplazos.csv y se comprueba que
cada evidenceSnippet aparece literalmente en su fichero de origen.
"""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # Tesis_herramientas/
sys.path.insert(0, str(ROOT / "Ontologia"))
from cqbycq.dsl import load_graph

CORPUS = ROOT / "Extraccion_ADR" / "corpus_tesis"
REPO = "DFE-Digital/early-years-foundation-recovery"
DIR = CORPUS / "adr" / REPO.replace("/", "__")

o = load_graph(ROOT / "Ontologia" / "outputs" / "esquema_plan" / "ontology.py")
G = o.G


def slug(texto):
    """Nombre de nodo valido para el validador: letras, digitos, _ y -."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", texto)


def adr_id(file):
    return "efr_" + slug(file.removesuffix(".md"))


def adr(file, title, date=None):
    nid = adr_id(file)
    G.add_node(nid, type="ArchitectureDecisionRecord", fileName=file, title=title,
               **({"recordDate": date} if date else {}))
    G.add_edge(nid, "REPO-efr", relation="belongsToRepository")
    G.add_edge("REPO-efr", nid, relation="containsADR")
    return nid


def status(a, raw, norm):
    s = a + "_status"
    G.add_node(s, type="StatusDeclaration", rawStatusLiteral=raw, evidenceSnippet=raw)
    G.add_edge(a, s, relation="hasStatusDeclaration")
    G.add_edge(s, "NS-" + norm, relation="hasNormalizedStatus")


def decision(a, text):
    d = a + "_decision"
    G.add_node(d, type="Decision", decisionDescription=text, evidenceSnippet=text)
    G.add_edge(a, d, relation="recordsDecision")


def alternatives(a, opts, chosen=None):
    for i, t in enumerate(opts, 1):
        n = "%s_alt%d" % (a, i)
        G.add_node(n, type="Alternative", alternativeDescription=t, evidenceSnippet=t)
        G.add_edge(a, n, relation="considersAlternative")
        if t == chosen:
            G.add_edge(a, n, relation="selectsAlternative")


def topic(nid, name):
    if nid not in G:
        G.add_node(nid, type="Topic", topicName=name)
    return nid


def short(n):
    return n.split("_")[1]


def declares(declarer, new, old, rtype, evidence):
    r = "RD_%s_%s_by_%s" % (short(new), short(old), short(declarer))
    G.add_node(r, type="RelationDeclaration", relationType=rtype, evidenceSnippet=evidence)
    G.add_edge(new, r, relation="sourceADR")
    G.add_edge(old, r, relation="targetADR")
    G.add_edge(declarer, r, relation="declaresRelation")


# --- vocabulario compartido ---------------------------------------------
G.add_node("REPO-efr", type="Repository", repositoryName=REPO)
for ns in ("proposed", "accepted", "deprecated", "superseded", "amended"):
    G.add_node("NS-" + ns, type="NormalizedStatus")

# --- 0006 ----------------------------------------------------------------
a06 = adr("0006-content-storage-strategy.md", "Use YAML + Markdown for Content")
status(a06, "accepted, superseded by [ADR-0011](0011-contentful.md)", "superseded")
decision(a06, "Chosen option: Create content as a mix of YAML and Markdown and "
              "mechanically translate to GDS compliant HTML.")
alternatives(a06, ["Integrate with a commercial CMS, e.g. Contentful",
                   "Adapt the EYFS Reforms CMS",
                   "Create content as a mix of YAML and Markdown and mechanically translate to GDS compliant HTML",
                   "Create GDS compliant HTML by hand"],
             chosen="Create content as a mix of YAML and Markdown and mechanically translate to GDS compliant HTML")
G.add_edge(a06, topic("T-content", "content storage"), relation="addressesTopic")

# --- 0011 ----------------------------------------------------------------
a11 = adr("0011-contentful.md", "Use Contentful CMS for content storage")
status(a11, "accepted", "accepted")
decision(a11, "Contentful is the mandated CMS for DfE projects.")
alternatives(a11, ["Static YAML", "Git-based CMS", "Contentful", "Sanity", "Strapi"],
             chosen="Contentful")
G.add_edge(a11, "T-content", relation="addressesTopic")

# --- 0018 ----------------------------------------------------------------
a18 = adr("0018-user-tracking.md", "Use Hotjar for tracking user journeys")
status(a18, "superseded by [ADR-0023](0023-clarity-metrics.md)", "superseded")
decision(a18, "Chosen option: Use Hotjar")
# "Use Hotjar" NO esta entre las opciones consideradas: no se marca selectsAlternative
alternatives(a18, ["Get insight through Google Analytics",
                   "Get insight from user research sessions"])
G.add_edge(a18, topic("T-tracking", "user journey tracking"), relation="addressesTopic")

# --- 0023 ----------------------------------------------------------------
a23 = adr("0023-clarity-metrics.md", "Use Clarity for tracking user journeys")
status(a23, "accepted, supersedes [ADR-0018](0018-user-tracking.md)", "accepted")
decision(a23, "Use Microsoft Clarity, easy to set up and maintain")
alternatives(a23, ["Microsoft Clarity"], chosen="Microsoft Clarity")
G.add_edge(a23, "T-tracking", relation="addressesTopic")

# --- declaraciones de relacion -------------------------------------------
declares(a06, a11, a06, "supersedes", "superseded by [ADR-0011](0011-contentful.md)")
declares(a18, a23, a18, "supersedes", "superseded by [ADR-0023](0023-clarity-metrics.md)")
declares(a23, a23, a18, "supersedes", "supersedes [ADR-0018](0018-user-tracking.md)")


# ======================================================================== #
def sale(n, rel):
    return [v for _, v, d in G.out_edges(n, data=True) if d.get("relation") == rel]


def entra(n, rel):
    return [u for u, _, d in G.in_edges(n, data=True) if d.get("relation") == rel]


def a(n, k):
    return G.nodes[n].get(k)


ADRS = [n for n, d in G.nodes(data=True) if d.get("type") == "ArchitectureDecisionRecord"]
RDS = [n for n, d in G.nodes(data=True) if d.get("type") == "RelationDeclaration"]

problemas = o.validate()
print("Validacion:", "OK" if not problemas else "")
for p in problemas:
    print("  ", p.render())
print("Individuos:", o.summary()["individuals"], "| aserciones:", o.summary()["assertions"])

print("\nCQ1/CQ2/CQ3/CQ4")
for n in ADRS:
    alts = sale(n, "considersAlternative")
    sel = [a(x, "alternativeDescription")[:30] for x in sale(n, "selectsAlternative")]
    st = sale(n, "hasStatusDeclaration")[0]
    print("  %s | repo=%s | alt=%d elegida=%s | estado=%s (%r) | tema=%s" % (
        short(n), a(sale(n, "belongsToRepository")[0], "repositoryName"), len(alts),
        sel or "-", sale(st, "hasNormalizedStatus")[0][3:], a(st, "rawStatusLiteral")[:28],
        a(sale(n, "addressesTopic")[0], "topicName")))

print("\nCQ5: reemplazos y quien los declara")
pares = {}
for r in RDS:
    if a(r, "relationType") != "supersedes":
        continue
    k = (entra(r, "sourceADR")[0], entra(r, "targetADR")[0])
    pares.setdefault(k, set()).update(entra(r, "declaresRelation"))
for (new, old), who in pares.items():
    ambos = {new, old} <= who
    print("  %s reemplaza a %s | declarado por %s | %s" % (
        short(new), short(old), sorted(short(w) for w in who), "ambos" if ambos else "solo uno"))

print("\nCQ8: decision vigente por tema")
reemplazados = {old for (_, old) in pares}
for t in [n for n, d in G.nodes(data=True) if d.get("type") == "Topic"]:
    miembros = entra(t, "addressesTopic")
    vig = [m for m in miembros if m not in reemplazados]
    cadena = " -> ".join(short(m) for m in sorted(miembros))
    for v in vig:
        print("  %-22s vigente: %s  %r  (cadena %s)" % (
            a(t, "topicName"), short(v), a(sale(v, "recordsDecision")[0], "decisionDescription")[:45], cadena))

print("\nCQ7: estado en una fecha")
sin_fecha = [short(n) for n in ADRS if not a(n, "recordDate")]
print("  ADRs sin recordDate:", sin_fecha, "-> el estado en una fecha NO se puede reconstruir")

# --- comparacion con el ground truth --------------------------------------
print("\nGround truth (ground_truth_reemplazos.csv):")
gt = [r for r in csv.DictReader(open(CORPUS / "ground_truth_reemplazos.csv", encoding="utf-8"))
      if r["repo"] == REPO and r["veredicto"] == "valido"]
aciertos = 0
for r in gt:
    old, new = adr_id(r["adr_reemplazado"]), adr_id(r["adr_nuevo"])
    esperado = r["bidireccional"] == "True"
    who = pares.get((new, old))
    ok = who is not None and ({new, old} <= who) == esperado
    aciertos += ok
    print("  %s -> %s | bidireccional GT=%s | grafo=%s | %s" % (
        short(old), short(new), esperado, None if who is None else {new, old} <= who,
        "OK" if ok else "DISTINTO"))
print("  coincidencias: %d/%d" % (aciertos, len(gt)))

# --- fundamentacion: cada evidenceSnippet debe estar en su fichero ---------
def dueño(n):
    t = G.nodes[n]["type"]
    if t == "RelationDeclaration":
        return entra(n, "declaresRelation")
    if t == "StatusDeclaration":
        return entra(n, "hasStatusDeclaration")
    for rel in ("recordsDecision", "considersAlternative", "addressesTopic"):
        e = entra(n, rel)
        if e:
            return e
    return []


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


print("\nFundamentacion (evidenceSnippet presente en el fichero de origen):")
total = fund = 0
for n, d in G.nodes(data=True):
    if "evidenceSnippet" not in d:
        continue
    total += 1
    textos = [norm((DIR / a(x, "fileName")).read_text(encoding="utf-8")) for x in dueño(n)]
    if any(norm(d["evidenceSnippet"]) in t for t in textos):
        fund += 1
    else:
        print("  NO fundamentado:", n)
sin_ev = [n for n, d in G.nodes(data=True) if d.get("type") == "Topic" and "evidenceSnippet" not in d]
print("  %d/%d nodos con evidencia estan fundamentados" % (fund, total))
print("  Temas sin evidencia (compartidos entre ADRs):", sin_ev)
