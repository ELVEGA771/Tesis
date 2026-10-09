"""Herramienta local de anotacion para el esquema v1.1.

    python anotador/servidor.py          ->  http://127.0.0.1:8765

Lee y escribe anotaciones.csv (el mismo fichero que usa csv_a_grafo.py).
Cada guardado reescribe la hoja de forma atomica y valida el grafo completo
con conformidad.py. El avance por ADR se guarda en anotador/progreso.json.
"""
import csv
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ANOT = HERE.parent
sys.path.insert(0, str(ANOT))
sys.path.insert(0, str(HERE))
import csv_a_grafo as C  # noqa: E402
import sugerencias as S  # noqa: E402
from conformidad import comprobar  # noqa: E402

PUERTO = 8765
PROGRESO = HERE / "progreso.json"
LOCK = threading.Lock()
SUBSET = list(csv.DictReader(open(C.CORPUS / "anotacion_subset.csv", encoding="utf-8")))
FECHAS = C.fechas_manifiesto()


def cargar_progreso():
    try:
        return json.loads(PROGRESO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_progreso(p):
    tmp = PROGRESO.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PROGRESO)


def clave(repo, archivo):
    return repo + "|" + archivo


def vacia(f):
    return not f["evidencia"].strip() and not f["valor"].strip()


def texto_adr(repo, archivo):
    return C.ruta_adr(repo, archivo).read_text(encoding="utf-8", errors="replace").replace("\r", "")


def archivos_repo(repo):
    d = C.CORPUS / "adr" / repo.replace("/", "__")
    return sorted(p.name for p in d.iterdir() if p.suffix == ".md")


def estado_general(filas, prog):
    temas = {}
    for f in filas:
        if f["elemento"] == "tema" and f["valor"]:
            temas.setdefault(f["repo"], set()).add(f["valor"].lower())
    adrs = []
    for r in SUBSET:
        k = clave(r["repo"], r["filename"])
        p = prog.get(k, {})
        fs = [f for f in filas if f["repo"] == r["repo"] and f["archivo"] == r["filename"]]
        empezado = p.get("abierto") or any(f["evidencia"].strip() and f["elemento"] != "driver" for f in fs)
        adrs.append({"repo": r["repo"], "archivo": r["filename"], "cadena": r.get("cadena_id", ""),
                     "titulo": C.titulo(r["repo"], r["filename"]) or r["filename"],
                     "estado": "terminado" if p.get("terminado") else "empezado" if empezado else "pendiente"})
    return {"adrs": adrs, "temas": {k: sorted(v) for k, v in temas.items()}}


def filas_de(filas, repo, archivo):
    return [f for f in filas if f["repo"] == repo and f["archivo"] == archivo]


def preparar(repo, archivo, filas, prog):
    fs = [dict(f) for f in filas_de(filas, repo, archivo)]
    if prog.get(clave(repo, archivo), {}).get("abierto"):
        return fs, False
    texto = texto_adr(repo, archivo)
    cadena = fs[0]["cadena"] if fs else ""
    fs = [f for f in fs if not (f["elemento"] in ("estado", "decision") and vacia(f))]
    for f in fs:
        if f["elemento"] == "relacion" and not f["evidencia"].strip():
            ev = S.evidencia_relacion(texto, f)
            if ev:
                f["evidencia"] = ev
                f["nota"] = S.AUTO + ("; " + f["nota"] if f["nota"] else "")
    presentes = {f["elemento"] for f in fs if f["evidencia"].strip() or (f["elemento"] == "tema" and f["valor"])}
    sug = S.sugerir(texto, set(archivos_repo(repo)), archivo, presentes,
                    [f for f in fs if f["elemento"] == "relacion"])
    for s in sug:
        s.update(repo=repo, archivo=archivo, cadena=cadena)
    if not any(f["elemento"] == "tema" for f in fs):
        fs.append({**{c: "" for c in C.COLUMNAS}, "repo": repo, "archivo": archivo, "cadena": cadena,
                   "elemento": "tema"})
    return fs + sug, True


def guardar(repo, archivo, nuevas, terminado):
    with LOCK:
        filas = C.leer_hoja()
        prog = cargar_progreso()
        cadena = next((f["cadena"] for f in filas_de(filas, repo, archivo)), "")
        limpias = []
        for n in nuevas:
            f = {c: str(n.get(c, "") or "") for c in C.COLUMNAS}
            f.update(repo=repo, archivo=archivo, cadena=cadena)
            if terminado and f["nota"].startswith(S.AUTO):
                f["nota"] = f["nota"][len(S.AUTO):].lstrip("; ")
            limpias.append(f)
        pos = next((i for i, f in enumerate(filas) if f["repo"] == repo and f["archivo"] == archivo), len(filas))
        resto = [f for f in filas if not (f["repo"] == repo and f["archivo"] == archivo)]
        antes = sum(1 for f in filas[:pos] if not (f["repo"] == repo and f["archivo"] == archivo))
        filas = resto[:antes] + limpias + resto[antes:]
        # el tema se comparte en toda la cadena
        tema = next((f["valor"] for f in limpias if f["elemento"] == "tema" and f["valor"]), "")
        if tema and cadena:
            otros = {(f["repo"], f["archivo"]) for f in filas
                     if f["repo"] == repo and f["cadena"] == cadena and f["archivo"] != archivo}
            for f in filas:
                if (f["repo"], f["archivo"]) in otros and f["elemento"] == "tema":
                    f["valor"] = tema
        C.escribir_hoja(filas)
        p = prog.setdefault(clave(repo, archivo), {})
        p["abierto"] = True
        p["terminado"] = bool(terminado)
        guardar_progreso(prog)
    return validar(filas, repo, archivo)


def validar(filas, repo, archivo):
    G, problemas, _ = C.construir(filas, FECHAS)
    hallazgos, met = comprobar(G, C.CORPUS / "adr")
    aid = C.adr_id(repo, archivo)
    cola = aid.split("__", 1)[1]
    propios = [str(h) for h in hallazgos
               if (h.nodo.startswith(aid) or ("__por_" + cola) in h.nodo)
               and h.tipo not in ("referenciado", "fecha")]
    mias = [i for i, f in enumerate(filas) if f["repo"] == repo and f["archivo"] == archivo]
    prob = ["%s: %s" % (filas[i]["elemento"], m) for i, m in problemas if i in mias]
    return {"problemas": prob + propios, "fundamentacion": met,
            "errores_totales": sum(h.severidad == "error" for h in hallazgos) + len(problemas)}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def enviar(self, obj, tipo="application/json", codigo=200):
        cuerpo = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self.enviar((HERE / "index.html").read_bytes(), "text/html")
            if u.path == "/api/estado":
                return self.enviar(estado_general(C.leer_hoja(), cargar_progreso()))
            if u.path == "/api/adr":
                filas = C.leer_hoja()
                fs, sug = preparar(q["repo"], q["archivo"], filas, cargar_progreso())
                return self.enviar({"texto": texto_adr(q["repo"], q["archivo"]), "filas": fs, "sugerido": sug,
                                    "archivos_repo": archivos_repo(q["repo"]),
                                    "terminado": cargar_progreso().get(clave(q["repo"], q["archivo"]), {}).get("terminado", False)})
            return self.enviar({"error": "no encontrado"}, codigo=404)
        except Exception as e:  # noqa: BLE001
            return self.enviar({"error": "%s: %s" % (type(e).__name__, e)}, codigo=500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            datos = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
            if u.path == "/api/guardar":
                return self.enviar(guardar(datos["repo"], datos["archivo"], datos["filas"], datos.get("terminado")))
            if u.path == "/api/sugerir":
                texto = texto_adr(datos["repo"], datos["archivo"])
                fs = datos["filas"]
                presentes = {f["elemento"] for f in fs if (f.get("evidencia") or "").strip()
                             or (f["elemento"] == "tema" and f.get("valor"))}
                sug = S.sugerir(texto, set(archivos_repo(datos["repo"])), datos["archivo"], presentes,
                                [f for f in fs if f["elemento"] == "relacion"])
                return self.enviar({"filas": sug})
            return self.enviar({"error": "no encontrado"}, codigo=404)
        except Exception as e:  # noqa: BLE001
            return self.enviar({"error": "%s: %s" % (type(e).__name__, e)}, codigo=500)


if __name__ == "__main__":
    print("Anotador v1.1 en http://127.0.0.1:%d  (Ctrl+C para salir)" % PUERTO)
    ThreadingHTTPServer(("127.0.0.1", PUERTO), H).serve_forever()
