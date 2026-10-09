"""Linea base RAG vectorial plana (A7): bge-base-en-v1.5 + ChromaDB sobre los 140 ADR.

    python rag.py indexar
    python rag.py "Which reverse proxy does datalab use now?"

Fragmentacion: una seccion Markdown por fragmento (los ADR se escriben por
secciones: Context, Decision, Consequences...); si una seccion pasa de 350
tokens se divide por parrafos. Cada fragmento lleva delante el titulo del ADR,
para que un fragmento suelto ("We will use Kong") no pierda de que trata.

Recuperacion con presupuesto (observacion O2): se ordenan los fragmentos por
similitud y se meten en el contexto mientras quepan en B tokens de Qwen; la
condicion grafo usa el mismo B, asi que la diferencia la pone la representacion.
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache

from comun import B, DATOS, adr_id, manifiesto, n_tokens, texto_adr

MODELO_EMB = "BAAI/bge-base-en-v1.5"
INSTRUCCION = "Represent this sentence for searching relevant passages: "   # bge v1.5, solo consultas
COLECCION = "adr_secciones"
MAX_FRAG = 350


def _titulo(texto, filename):
    m = re.search(r"(?m)^#\s+(.+)$", texto)
    return m.group(1).strip() if m else filename


def fragmentar(repo, filename):
    texto = texto_adr(repo, filename)
    titulo = _titulo(texto, filename)
    partes = re.split(r"(?m)^(?=#{2,4}\s)", texto)
    frags = []
    for p in partes:
        p = p.strip()
        if not p:
            continue
        m = re.match(r"#{1,4}\s+(.+)", p)
        seccion = m.group(1).strip() if m else "(inicio)"
        bloques = [p] if n_tokens(p) <= MAX_FRAG else _por_parrafos(p)
        for b in bloques:
            frags.append({"texto": f"{titulo}\n{b}", "repo": repo, "filename": filename,
                          "seccion": seccion, "titulo": titulo})
    return frags


def _por_parrafos(p):
    out, actual = [], ""
    for par in re.split(r"\n\s*\n", p):
        cand = f"{actual}\n\n{par}" if actual else par
        if actual and n_tokens(cand) > MAX_FRAG:
            out.append(actual)
            actual = par
        else:
            actual = cand
    if actual:
        out.append(actual)
    return out


@lru_cache
def _emb():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODELO_EMB)


@lru_cache
def _col():
    import chromadb
    cli = chromadb.PersistentClient(path=str(DATOS / "chroma"))
    return cli.get_or_create_collection(COLECCION, metadata={"hnsw:space": "cosine"})


def indexar():
    import chromadb
    cli = chromadb.PersistentClient(path=str(DATOS / "chroma"))
    if COLECCION in [c.name for c in cli.list_collections()]:
        cli.delete_collection(COLECCION)
    _col.cache_clear()
    frags = [f for r in manifiesto() for f in fragmentar(r["repo"], r["filename"])]
    vec = _emb().encode([f["texto"] for f in frags], normalize_embeddings=True,
                        batch_size=32, show_progress_bar=True)
    col = _col()
    for i in range(0, len(frags), 500):
        lote = frags[i:i + 500]
        col.add(ids=[f"{adr_id(f['repo'], f['filename'])}#{i + j}" for j, f in enumerate(lote)],
                embeddings=vec[i:i + 500].tolist(),
                documents=[f["texto"] for f in lote],
                metadatas=[{k: f[k] for k in ("repo", "filename", "seccion", "titulo")} for f in lote])
    print(f"{len(frags)} fragmentos de {len(manifiesto())} ADR indexados")


def recuperar(pregunta: str, presupuesto: int = B, candidatos: int = 50):
    """Fragmentos por similitud hasta llenar el presupuesto. Devuelve (contexto, fragmentos usados)."""
    q = _emb().encode([INSTRUCCION + pregunta], normalize_embeddings=True)[0].tolist()
    r = _col().query(query_embeddings=[q], n_results=candidatos)
    usados, total, piezas = [], 0, []
    for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0]):
        pieza = f"[{meta['repo']}/{meta['filename']} · {meta['seccion']}]\n{doc}"
        t = n_tokens(pieza) + 2
        if total + t > presupuesto:
            continue
        piezas.append(pieza)
        total += t
        usados.append({**meta, "similitud": round(1 - dist, 4), "tokens": t})
    return "\n\n".join(piezas), usados


if __name__ == "__main__":
    if sys.argv[1:] == ["indexar"]:
        indexar()
    else:
        ctx, usados = recuperar(" ".join(sys.argv[1:]))
        for u in usados:
            print(f"{u['similitud']:.3f} {u['tokens']:>4} tok  {u['repo']}/{u['filename']} · {u['seccion']}")
        print(f"total {sum(u['tokens'] for u in usados)} / {B} tokens")
