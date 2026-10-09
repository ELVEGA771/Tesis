"""Piezas compartidas por el pipeline (A5/A6), la linea base RAG (A7) y el evaluador (A8).

- Rutas del corpus, textos de los ADR y particion desarrollo/prueba.
- Tokenizador de Qwen3 para medir el presupuesto de contexto B en las dos condiciones.
- Cliente de Ollama (API REST) con configuracion fija (temperatura 0, semilla 42)
  que registra tokens y latencia de cada llamada en un .jsonl: es el costo de
  construir el grafo y el tiempo de consulta que pide la retroalimentacion.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
TESIS = RAIZ.parent
CORPUS = TESIS / "Extraccion_ADR" / "corpus_tesis"
DATOS = RAIZ / "datos"
REGISTROS = RAIZ / "registros"

MODELO = "qwen3:4b-instruct"
TOKENIZADOR = "Qwen/Qwen3-4B-Instruct-2507"
OPCIONES = {"temperature": 0, "seed": 42, "num_ctx": 16384}
OLLAMA = "http://127.0.0.1:11434/api/chat"
B = 2048   # presupuesto de contexto (tokens de Qwen) igual en RAG y grafo (observacion O2)


def adr_id(repo: str, filename: str) -> str:
    return f"{repo}|{filename}"


@lru_cache
def manifiesto() -> list[dict]:
    return list(csv.DictReader(open(CORPUS / "corpus_manifest.csv", encoding="utf-8")))


@lru_cache
def particion() -> dict[str, str]:
    return {adr_id(r["repo"], r["filename"]): r["particion"]
            for r in csv.DictReader(open(CORPUS / "particion.csv", encoding="utf-8"))}


def texto_adr(repo: str, filename: str) -> str:
    return (CORPUS / "adr" / repo.replace("/", "__") / filename).read_text(encoding="utf-8", errors="replace")


@lru_cache
def _tok():
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(TOKENIZADOR)


def n_tokens(texto: str) -> int:
    return len(_tok()(texto, add_special_tokens=False)["input_ids"])


def chat(mensajes: list[dict], *, herramientas: list[dict] | None = None,
         etiqueta: str = "", registro: str | None = None, **extra) -> dict:
    """Una llamada a Ollama. Devuelve el mensaje y anota tokens y segundos en registros/<registro>.jsonl."""
    cuerpo = {"model": MODELO, "stream": False, "options": OPCIONES, "messages": mensajes, **extra}
    if herramientas:
        cuerpo["tools"] = herramientas
    req = urllib.request.Request(OLLAMA, json.dumps(cuerpo).encode(), {"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:   # p. ej. llamada a funcion truncada que Ollama no puede parsear
        resp = {"message": {"role": "assistant", "content": "", "error": f"HTTP {e.code}: {e.read()[:200]!r}"}}
    seg = time.perf_counter() - t0
    if registro:
        REGISTROS.mkdir(exist_ok=True)
        with open(REGISTROS / f"{registro}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"etiqueta": etiqueta, "segundos": round(seg, 3),
                                 "tokens_entrada": resp.get("prompt_eval_count"),
                                 "tokens_salida": resp.get("eval_count"),
                                 "error": resp["message"].get("error"),
                                 "modelo": MODELO, **OPCIONES}, ensure_ascii=False) + "\n")
    return resp["message"]
