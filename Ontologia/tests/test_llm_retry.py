"""Reintentos y traduccion de errores de la API, sin gastar API.

Cubre la capa de `llm.py`: que un 503 pasajero no tire la corrida, que un
400 no se reintente en balde, y que los mensajes de error digan que hacer.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "fake-key-para-tests")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from google.genai import errors as genai_errors
from cqbycq.config import Config
from cqbycq import llm as L


def server_error(code, msg):
    resp = httpx.Response(code, json={"error": {"code": code, "message": msg}},
                          request=httpx.Request("POST", "https://x"))
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, {"error": {"code": code, "message": msg}}, resp)


def quota_error(quota_id, retry_delay=None,
                msg="You exceeded your current quota, please check your plan "
                    "and billing details."):
    """429 con el cuerpo que manda de verdad la API de Google."""
    items = [{
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaMetric": "generativelanguage.googleapis.com/"
                                       "generate_content_free_tier_requests",
                        "quotaId": quota_id}],
    }]
    if retry_delay:
        items.append({"@type": "type.googleapis.com/google.rpc.RetryInfo",
                      "retryDelay": retry_delay})
    body = {"error": {"code": 429, "message": msg,
                      "status": "RESOURCE_EXHAUSTED", "details": items}}
    resp = httpx.Response(429, json=body,
                          request=httpx.Request("POST", "https://x"))
    return genai_errors.ClientError(429, body, resp)


def client(**over):
    over.setdefault('overload_backoff_s', 0.01)
    cfg = Config(**over)
    c = L.LLMClient(cfg)
    return c


def patch(c, seq):
    """seq: lista de excepciones a lanzar; None = exito."""
    calls = {"n": 0}
    def fake(contents, system, thinking):
        i = calls["n"]; calls["n"] += 1
        item = seq[i] if i < len(seq) else None
        if item is not None:
            raise item
        return ("ok", "STOP", {"input_tokens": 1, "output_tokens": 2},
                "m", "rid")
    c._stream = fake
    return calls


fails = 0
def check(cond, label):
    global fails
    print(("  OK     " if cond else "  FALLA  ") + label)
    if not cond:
        fails += 1


print("\n[1] 503 transitorio: reintenta y acaba saliendo bien")
c = client(); n = patch(c, [server_error(503, "high demand"),
                            server_error(503, "high demand")])
r = c.complete("hola", tag="t")
check(r.text == "ok", "recupera tras 2 fallos 503")
check(n["n"] == 3, "hizo 3 intentos (2 fallidos + 1 bueno): %d" % n["n"])

print("\n[2] 503 permanente: agota reintentos y da un error accionable")
c = client(overload_retries=3); n = patch(c, [server_error(503, "high demand")] * 99)
try:
    c.complete("hola", tag="t"); check(False, "deberia haber fallado")
except L.LLMError as e:
    check(n["n"] == 4, "1 intento + 3 reintentos = 4 llamadas: %d" % n["n"])
    check("--model" in str(e) and "saturado" in str(e),
          "el mensaje sugiere que hacer")
    check("Traceback" not in str(e), "es un mensaje, no un volcado")

print("\n[3] backoff exponencial con tope")
c = client(overload_backoff_s=20.0)
delays = [min(20.0 * 2 ** i, L.BACKOFF_CAP_S) for i in range(5)]
check(delays == [20.0, 40.0, 80.0, 120.0, 120.0], "20/40/80/120/120 s: %s" % delays)
check(sum(delays) == 380.0, "espera total maxima 380 s (~6 min)")

print("\n[4] 400 NO se reintenta (es culpa de la peticion, no del servidor)")
c = client(); n = patch(c, [server_error(400, "bad request")] * 9)
try:
    c.complete("hola", tag="t"); check(False, "deberia haber fallado")
except L.LLMError as e:
    check(n["n"] == 1, "una sola llamada, sin reintentos: %d" % n["n"])
    check("Revisa `model`" in str(e), "orienta a revisar la config")

print("\n[5] 404 modelo inexistente: mensaje util, sin reintentos")
c = client(); n = patch(c, [server_error(404, "not found")] * 9)
try:
    c.complete("hola", tag="t"); check(False, "deberia haber fallado")
except L.LLMError as e:
    check(n["n"] == 1, "sin reintentos: %d" % n["n"])
    check("cqbycq models" in str(e), "dice como listar los modelos validos")

print("\n[6] corte de red a mitad del stream: tambien se reintenta")
c = client(); n = patch(c, [httpx.ReadError("conexion cortada")])
r = c.complete("hola", tag="t")
check(r.text == "ok" and n["n"] == 2, "recupera del corte de transporte")

print("\n[7] 429 (cuota) se trata como transitorio")
c = client(); n = patch(c, [server_error(429, "quota")])
r = c.complete("hola", tag="t")
check(r.text == "ok" and n["n"] == 2, "reintenta tras 429")

print("\n[8] el modelo que no admite thinking: reintento inmediato sin thinking")
c = client()
seen = []
def fake(contents, system, thinking):
    seen.append(thinking)
    if thinking == "level":
        raise server_error(400, "thinking_level is not supported")
    return ("ok", "STOP", {}, "m", "rid")
c._stream = fake
r = c.complete("hola", tag="t")
check(seen == ["level", "off"], "cae a sin-thinking y sigue: %s" % seen)
check(c._thinking_mode == "off", "no lo vuelve a intentar en la siguiente CQ")

print("\n[9] 429 por minuto: se reintenta y se respeta el retryDelay del servidor")
minute = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
info = L._quota_failure(quota_error(minute, "24s"))
check(info["retry_delay_s"] == 24.0, "lee retryDelay=24s: %s" % info["retry_delay_s"])
check(info["quota_id"] == minute, "identifica el limite tocado")
check(info["exhausted"] is False, "un limite por minuto NO es cuota agotada")
c = client(); n = patch(c, [quota_error(minute, "0s")])
r = c.complete("hola", tag="t")
check(r.text == "ok" and n["n"] == 2, "reintenta y sale adelante")

print("\n[10] 429 por dia: para en seco, sin quemar reintentos")
c = client(); n = patch(c, [quota_error(
    "GenerateRequestsPerDayPerProjectPerModel-FreeTier")] * 9)
try:
    c.complete("hola", tag="t"); check(False, "deberia haber fallado")
except L.LLMError as e:
    check(n["n"] == 1, "una sola llamada, no espera en balde: %d" % n["n"])
    check("no es un pico pasajero" in str(e), "lo distingue de la saturacion")
    check("facturacion" in str(e) and "--model" in str(e),
          "dice las salidas reales")

print("\n[11] retryDelay absurdamente largo tambien cuenta como agotada")
check(L._quota_failure(quota_error("AlgoRaro", "3600s"))["exhausted"] is True,
      "un retryDelay de 1h no es un pico pasajero")

print("\n[12] un 503 no se confunde con un problema de cuota")
check(L._quota_failure(server_error(503, "high demand")) is None,
      "_quota_failure solo mira los 429")

print("\n[13] Truncado por razonamiento: el mensaje senala la causa real")
c = client(max_tokens=16000)
msg = c._truncated({"thinking_tokens": 15359, "output_tokens": 637})
check("15359 en razonamiento" in msg, "da el desglose real del gasto")
check("96% razonamiento" in msg, "y el porcentaje: %r" % msg.splitlines()[1][-30:])
check("--effort medium" in msg, "propone acotar el razonamiento primero")
check(msg.index("--effort medium") < msg.index("--max-tokens"),
      "en ese orden: acotar antes que ampliar")
check("--resume" in msg, "recuerda que no hay que repetir las etapas previas")

print("\n[14] Truncado por respuesta larga de verdad: otro consejo")
msg = c._truncated({"thinking_tokens": 200, "output_tokens": 15800})
check("--max-tokens" in msg and "--effort medium" not in msg,
      "si el texto es lo que ocupa, lo que toca es mas margen")

print("\n[15] La escalera de razonamiento: budget -> level -> off")
c = client(thinking_budget=4096)
check(c._thinking_mode == "budget", "con thinking_budget usa el tope duro")
cfg = c._generation_config(None, thinking="budget")
check(cfg.thinking_config.thinking_budget == 4096, "y lo manda tal cual")
c2 = client()
check(c2._thinking_mode == "level", "sin tope, usa el nivel cualitativo")
c3 = client(thinking=False)
check(c3._thinking_mode == "off", "y se puede apagar del todo")
check(c3._generation_config(None, thinking="off").thinking_config is None,
      "apagado no manda thinking_config")

print("\n[16] Si el modelo rechaza el tope duro, se baja un escalon solo")
c = client(thinking_budget=4096)
modes = []
def fake_ladder(contents, system, thinking):
    modes.append(thinking)
    if thinking == "budget":
        raise server_error(400, "thinking_budget is not supported by this model")
    return ("ok", "STOP", {}, "m", "rid")
c._stream = fake_ladder
r = c.complete("hola", tag="t")
check(modes == ["budget", "level"], "budget -> level, sin apagar nada: %s" % modes)
check(c._thinking_mode == "level", "y se queda ahi para las siguientes CQs")

print("\n" + "=" * 60)
print("Todo correcto." if not fails else "%d comprobacion(es) fallan." % fails)
sys.exit(1 if fails else 0)
