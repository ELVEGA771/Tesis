"""Cliente LLM (Google Gemini) + registro de todas las comunicaciones.

El paper describe un "external memory module" que guarda el codigo generado y
las comunicaciones previas. Aqui la parte de comunicaciones la cubre este
modulo (transcript JSONL por corrida) y la parte de codigo la cubre
`memory.py`.

Este fichero es el UNICO punto de contacto con la API del modelo: el resto del
pipeline (prompts, validacion, memoria, evaluacion) no sabe que backend hay
detras.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .config import Config

# Variables de entorno que el SDK de Google reconoce.
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

# `effort` es terminologia de Anthropic; en Gemini el control equivalente de
# profundidad de razonamiento es `thinking_level`.
EFFORT_TO_THINKING_LEVEL = {
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "xhigh": "HIGH",
    "max": "HIGH",
}

# finish_reason que significan "el modelo no contesto por politica".
REFUSAL_REASONS = ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION",
                   "SPII")

# Codigos que son un problema pasajero del servidor, no de la peticion. El 503
# ("This model is currently experiencing high demand") es el habitual en los
# modelos recien salidos.
TRANSIENT_CODES = (408, 429, 500, 502, 503, 504)
BACKOFF_CAP_S = 120.0


@dataclass
class LLMResponse:
    text: str
    stop_reason: str | None
    usage: dict[str, int] = field(default_factory=dict)
    request_id: str | None = None
    latency_s: float = 0.0
    model: str = ""


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Envoltorio fino sobre el SDK `google-genai`.

    Nota sobre hiperparametros: el paper fija temperature=0 y penalizaciones=0.
    Gemini **si** acepta `temperature`, asi que por defecto se envia 0 y se
    replica el ajuste del paper (con Claude habia que sustituirlo por `effort`,
    porque los modelos actuales devuelven 400 si se les manda `temperature`).
    `effort` se sigue leyendo de la config y se traduce a `thinking_level`.

    Sobre el cacheo: los bloques de `prompts.py` llevan `cache_control`, que es
    un marcador de Anthropic. Aqui se ignora al construir la peticion, pero no
    se pierde el ahorro: Gemini aplica **cacheo implicito** sobre el prefijo
    comun de peticiones consecutivas, que es justo lo que prepara el orden de
    `section_order` (header + helper + historia identicos byte a byte en las N
    etapas). Los tokens servidos desde cache se reportan en
    `usage.cached_input_tokens`.
    """

    def __init__(self, config: Config, transcript_path: Path | None = None,
                 server_side_fallbacks: bool = True) -> None:
        self.config = config
        # `server_side_fallbacks` era el reencaminado del lado del servidor de
        # Anthropic. Gemini no tiene equivalente: se acepta el argumento para
        # no romper las llamadas existentes, pero no hace nada.
        self.server_side_fallbacks = server_side_fallbacks
        self.transcript_path = transcript_path
        self.total_usage: dict[str, int] = {}
        # Escalera de control del razonamiento, de mas preciso a menos:
        # tope explicito de tokens -> nivel cualitativo -> sin razonamiento.
        # Si el modelo rechaza un escalon, se baja al siguiente.
        if not config.thinking:
            self._thinking_mode = "off"
        elif config.thinking_budget is not None:
            self._thinking_mode = "budget"
        else:
            self._thinking_mode = "level"

        if not any(os.environ.get(var) for var in API_KEY_VARS):
            raise LLMError(
                "No encuentro la clave de la API. Exporta GEMINI_API_KEY "
                "(PowerShell: $env:GEMINI_API_KEY = \"...\"). Se obtiene en "
                "https://aistudio.google.com/apikey")
        self.client = genai.Client(
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    attempts=self.config.max_retries,
                    initial_delay=self.config.retry_initial_delay,
                    max_delay=self.config.retry_max_delay,
                    http_status_codes=list(TRANSIENT_CODES))))

    # ------------------------------------------------------------------ #

    def complete(self, content: list[dict] | str, system: str | None = None,
                 tag: str = "") -> LLMResponse:
        """Una interaccion con el modelo. Siempre en streaming: los prompts de
        CQbyCQ acumulan la ontologia entera y las respuestas pueden ser largas,
        y el streaming evita los timeouts HTTP del SDK."""
        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": content if isinstance(content, list) else [
                {"type": "text", "text": content}],
        }]
        # Los bloques del prompt son siempre de texto; `cache_control` y demas
        # claves propias de Anthropic se descartan aqui.
        parts = [types.Part.from_text(text=block["text"])
                 for block in messages[0]["content"]]
        contents = [types.Content(role="user", parts=parts)]

        started = time.time()
        result = self._stream_with_retries(contents, system)
        latency = time.time() - started
        text, finish_reason, usage, model_version, request_id = result

        if finish_reason in REFUSAL_REASONS:
            raise LLMError(
                "El modelo rechazo la peticion (finish_reason=%s). Revisa el "
                "contenido de la historia." % finish_reason)

        for key, value in usage.items():
            if isinstance(value, int):
                self.total_usage[key] = self.total_usage.get(key, 0) + value

        response = LLMResponse(
            text=text, stop_reason=finish_reason, usage=usage,
            request_id=request_id, latency_s=round(latency, 2),
            model=model_version or self.config.model,
        )
        self._log(tag, messages, system, response)

        if finish_reason == "MAX_TOKENS":
            raise LLMError(self._truncated(usage))
        if not text.strip():
            raise LLMError(
                "El modelo devolvio una respuesta vacia (finish_reason=%s)."
                % finish_reason)
        return response

    # ------------------------------------------------------------------ #

    def _generation_config(self, system: str | None,
                           thinking: str) -> types.GenerateContentConfig:
        kwargs: dict[str, Any] = {
            "max_output_tokens": self.config.max_tokens,
            "response_modalities": ["TEXT"],
            # Aqui no se declara ninguna herramienta: el modelo solo devuelve
            # texto. Desactivar el automatic function calling evita el aviso
            # del SDK y una capa de envoltorio que no se usa.
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(
                disable=True),
        }
        if system:
            kwargs["system_instruction"] = system
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        if thinking == "budget":
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=self.config.thinking_budget)
        elif thinking == "level":
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=EFFORT_TO_THINKING_LEVEL.get(
                    self.config.effort, "HIGH"))
        return types.GenerateContentConfig(**kwargs)

    def _stream_with_retries(
            self, contents: list[types.Content], system: str | None,
    ) -> tuple[str, str | None, dict[str, int], str | None, str | None]:
        """Reemite la llamada entera ante fallos pasajeros del servidor.

        Los reintentos del SDK solo cubren la peticion inicial: si el stream se
        corta a mitad de la generacion, no los cubre nadie. Este bucle si, y
        ademas espera lo suficiente como para que un pico de demanda se pase
        (el SDK reintenta en 1-2 segundos, que ante un 503 no sirve de nada).
        """
        attempt = 0
        while True:
            try:
                return self._stream(contents, system,
                                    thinking=self._thinking_mode)
            except genai_errors.ClientError as exc:
                # No todos los modelos admiten las dos formas de configurar el
                # razonamiento. Si rechaza esta, se baja un escalon y se
                # reintenta en el acto.
                if self._thinking_mode != "off" and _is_thinking_error(exc):
                    self._thinking_mode = ("level"
                                           if self._thinking_mode == "budget"
                                           else "off")
                    print("      el modelo no acepta esa forma de acotar el "
                          "razonamiento; se pasa a '%s'" % self._thinking_mode,
                          file=sys.stderr, flush=True)
                    continue
                if getattr(exc, "code", None) not in TRANSIENT_CODES:
                    raise self._translate(exc) from exc
                transient: Exception = exc
            except genai_errors.ServerError as exc:
                transient = exc
            except genai_errors.APIError as exc:
                raise self._translate(exc) from exc
            except LLMError:
                raise
            except httpx.TransportError as exc:
                transient = exc
            except Exception as exc:  # fallo de red no clasificado
                raise LLMError(
                    "Error de red al llamar a la API: %s" % exc) from exc

            # Una cuota diaria agotada no se arregla esperando dos minutos:
            # mejor parar ya y decirlo, que quemar 5 reintentos en balde.
            quota = _quota_failure(transient)
            if quota and quota["exhausted"]:
                raise self._quota_exhausted(quota) from transient

            attempt += 1
            if attempt > self.config.overload_retries:
                raise self._exhausted(transient, attempt) from transient
            # Si el servidor dice cuanto esperar (RetryInfo), se le hace caso;
            # si no, backoff exponencial.
            server_delay = quota["retry_delay_s"] if quota else None
            delay = (server_delay + 1.0 if server_delay is not None
                     else min(self.config.overload_backoff_s * 2 ** (attempt - 1),
                              BACKOFF_CAP_S))
            print("      API no disponible (%s). Reintento %d/%d en %ds...%s"
                  % (_short(transient), attempt, self.config.overload_retries,
                     round(delay),
                     " (espera indicada por el servidor)"
                     if server_delay is not None else ""),
                  file=sys.stderr, flush=True)
            time.sleep(delay)

    def _stream(self, contents: list[types.Content], system: str | None,
                thinking: str) -> tuple[str, str | None, dict[str, int],
                                        str | None, str | None]:
        """Consume el stream y devuelve (texto, finish_reason, uso, modelo, id)."""
        pieces: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        model_version: str | None = None
        request_id: str | None = None

        for chunk in self.client.models.generate_content_stream(
                model=self.config.model, contents=contents,
                config=self._generation_config(system, thinking)):
            feedback = getattr(chunk, "prompt_feedback", None)
            if feedback is not None and getattr(feedback, "block_reason", None):
                raise LLMError(
                    "La API bloqueo el prompt (block_reason=%s). Revisa el "
                    "contenido de la historia." % feedback.block_reason)
            pieces.append(_chunk_text(chunk))
            if getattr(chunk, "model_version", None):
                model_version = chunk.model_version
            if getattr(chunk, "response_id", None):
                request_id = chunk.response_id
            if getattr(chunk, "usage_metadata", None):
                usage = _usage_dict(chunk.usage_metadata)
            for candidate in (chunk.candidates or []):
                if candidate.finish_reason:
                    finish_reason = candidate.finish_reason.name
        return "".join(pieces), finish_reason, usage, model_version, request_id

    def _translate(self, exc: genai_errors.APIError) -> LLMError:
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        if code in (401, 403):
            return LLMError(
                "Credenciales invalidas o sin permiso (%s). Revisa "
                "GEMINI_API_KEY." % code)
        if code == 404:
            return LLMError(
                "El modelo '%s' no existe o no esta disponible para tu clave.\n"
                "Lista los que si lo estan con:\n"
                "  python -m cqbycq models\n"
                "y ajusta `model` en la config." % self.config.model)
        if code == 429:
            return LLMError("Rate limit / cuota agotada: %s" % message)
        if code and 400 <= code < 500:
            return LLMError(
                "Peticion rechazada por la API (%s): %s\n"
                "Revisa `model`, `effort`, `temperature` y `max_tokens` en la "
                "config." % (code, message))
        return LLMError("Error de la API (%s): %s" % (code, message))

    def _truncated(self, usage: dict[str, int]) -> str:
        """Mensaje de truncado que dice en que se gasto el presupuesto.

        En Gemini el razonamiento sale de `max_output_tokens`, asi que un
        truncado casi nunca significa "la ontologia es muy larga": significa
        que el modelo penso demasiado y se quedo sin sitio para escribirla.
        Sin el desglose, el consejo obvio ("sube max_tokens") puede ser el
        equivocado."""
        thinking = usage.get("thinking_tokens", 0)
        text = usage.get("output_tokens", 0)
        spent = thinking + text
        msg = ["La respuesta se corto por `max_tokens` (%d): un truncado a "
               "mitad deja la CQ mal modelada." % self.config.max_tokens]
        if spent:
            share = 100 * thinking / spent
            msg.append("  Presupuesto gastado: %d en razonamiento + %d en "
                       "texto = %d (%.0f%% razonamiento)."
                       % (thinking, text, spent, share))
        if thinking and thinking > 2 * max(text, 1):
            msg.append(
                "  En Gemini el razonamiento sale de `max_tokens`, y aqui se "
                "lo ha comido casi entero.\n"
                "  Lo que de verdad ayuda, por orden:\n"
                "    - acotar el razonamiento: --effort medium (o "
                "thinking_budget en la config, que le pone un tope duro);\n"
                "    - y luego si, mas margen: --max-tokens %d."
                % max(self.config.max_tokens * 2, 32000))
        else:
            msg.append("  Sube el margen: --max-tokens %d."
                       % max(self.config.max_tokens * 2, 32000))
        msg.append("  Con --resume solo se repite esta etapa, no las "
                   "anteriores.")
        return "\n".join(msg)

    def _quota_exhausted(self, quota: dict[str, Any]) -> LLMError:
        limit = quota["quota_id"] or "(sin identificar)"
        return LLMError(
            "Cuota agotada, y no es un pico pasajero: se ha alcanzado el "
            "limite '%s' del modelo '%s'.\n"
            "Esperar no lo arregla dentro de esta corrida. Opciones:\n"
            "  - activar facturacion en el proyecto de Google Cloud / AI "
            "Studio (el tier gratuito de los modelos nuevos es muy corto);\n"
            "  - usar un modelo con mas cuota: --model <id> "
            "(`python -m cqbycq models` los lista);\n"
            "  - retomar cuando la cuota se renueve (suele ser a medianoche "
            "hora del Pacifico)."
            % (limit, self.config.model))

    def _exhausted(self, exc: Exception, attempts: int) -> LLMError:
        return LLMError(
            "La API sigue sin responder tras %d reintentos: %s\n"
            "El modelo '%s' esta saturado ahora mismo. Opciones:\n"
            "  - esperar y relanzar el mismo comando;\n"
            "  - probar otro modelo: --model <id> "
            "(`python -m cqbycq models` los lista);\n"
            "  - aumentar la paciencia: overload_retries / overload_backoff_s "
            "en la config."
            % (attempts - 1, _short(exc), self.config.model))

    # ------------------------------------------------------------------ #

    def _log(self, tag: str, messages: list[dict], system: str | None,
             response: LLMResponse) -> None:
        if not self.transcript_path:
            return
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "tag": tag,
            "timestamp": time.time(),
            "model": response.model,
            "system": system,
            "prompt": messages,
            "response": response.text,
            "stop_reason": response.stop_reason,
            "usage": response.usage,
            "request_id": response.request_id,
            "latency_s": response.latency_s,
        }
        with self.transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------- #

def available_models() -> list[str]:
    """Modelos de generacion visibles para la clave actual."""
    if not any(os.environ.get(var) for var in API_KEY_VARS):
        raise LLMError("No encuentro la clave de la API. Exporta GEMINI_API_KEY.")
    names = []
    for model in genai.Client().models.list():
        actions = getattr(model, "supported_actions", None) or []
        if not actions or "generateContent" in actions:
            names.append(model.name.removeprefix("models/"))
    return sorted(names)


def _chunk_text(chunk: Any) -> str:
    """Texto de un chunk, descartando las partes de razonamiento interno."""
    out: list[str] = []
    for candidate in (getattr(chunk, "candidates", None) or []):
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) or []):
            if getattr(part, "thought", False):
                continue
            if getattr(part, "text", None):
                out.append(part.text)
    return "".join(out)


def _usage_dict(meta: Any) -> dict[str, int]:
    """Uso de tokens con los mismos nombres que usaba el backend anterior, para
    que `summary.json` siga siendo comparable entre corridas."""
    fields = {
        "input_tokens": "prompt_token_count",
        "output_tokens": "candidates_token_count",
        "thinking_tokens": "thoughts_token_count",
        "cached_input_tokens": "cached_content_token_count",
        "total_tokens": "total_token_count",
    }
    usage: dict[str, int] = {}
    for name, attr in fields.items():
        value = getattr(meta, attr, None)
        if isinstance(value, int):
            usage[name] = value
    return usage


def _quota_failure(exc: Exception) -> dict[str, Any] | None:
    """Lee el cuerpo de un 429 de Google: que limite se ha tocado y cuanto
    pide esperar el servidor.

    El cuerpo trae `QuotaFailure` (con el `quotaId`, que dice si el limite es
    por minuto o por dia) y `RetryInfo.retryDelay`. Distinguirlos importa: un
    limite por minuto se pasa esperando, uno por dia no.
    """
    if getattr(exc, "code", None) != 429:
        return None
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return None
    items = details.get("error", details).get("details") or []

    quota_id = None
    retry_delay_s = None
    for item in items:
        kind = str(item.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            for violation in item.get("violations") or []:
                quota_id = (violation.get("quotaId")
                            or violation.get("quotaMetric") or quota_id)
        elif kind.endswith("RetryInfo"):
            raw = str(item.get("retryDelay", "")).strip()
            if raw.endswith("s"):
                try:
                    retry_delay_s = float(raw[:-1])
                except ValueError:
                    pass

    haystack = "%s %s" % (quota_id or "", getattr(exc, "message", "") or "")
    lowered = haystack.lower()
    # "PerDay" en el quotaId, o un retryDelay tan largo que ya no es un pico.
    exhausted = ("perday" in lowered.replace(" ", "")
                 or "per day" in lowered
                 or "daily" in lowered
                 or (retry_delay_s is not None and retry_delay_s > 600))
    return {"quota_id": quota_id, "retry_delay_s": retry_delay_s,
            "exhausted": exhausted}


def _short(exc: Exception) -> str:
    """Una linea legible para el aviso de reintento."""
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    message = " ".join(str(message).split())
    if len(message) > 90:
        message = message[:87] + "..."
    return "%s %s" % (code, message) if code else message


def _is_thinking_error(exc: genai_errors.ClientError) -> bool:
    return "thinking" in (str(getattr(exc, "message", None) or exc)).lower()
