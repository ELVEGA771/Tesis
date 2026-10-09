"""Configuracion del experimento + manifiesto de reproducibilidad."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"


@dataclass
class Config:
    """Parametros de una corrida CQbyCQ.

    Los nombres siguen la terminologia del paper (header / helper / story /
    footer, memoria externa, tecnica de prompting) para que el mapeo con el
    metodo original sea directo.
    """

    # --- Modelo -------------------------------------------------------- #
    # Backend: Google Gemini (`google-genai`). Ver `cqbycq/llm.py`.
    model: str = "gemini-3.8-flash"
    # OJO: en Gemini los tokens de razonamiento salen de este mismo
    # presupuesto. Con `effort=high` el modelo puede gastar aqui 15.000 tokens
    # pensando y quedarse sin sitio para escribir la ontologia (la respuesta
    # se corta con finish_reason=MAX_TOKENS). De ahi que el margen sea amplio.
    max_tokens: int = 32000
    # `effort` se traduce a `thinking_level` de Gemini: low -> LOW,
    # medium -> MEDIUM, high/xhigh/max -> HIGH.
    effort: str = "high"                 # low | medium | high | xhigh | max
    thinking: bool = True                # razonamiento interno del modelo
    # Tope explicito de tokens de razonamiento. `thinking_level` no acota
    # cuanto piensa el modelo; esto si. Util cuando el razonamiento se dispara
    # y se come el presupuesto de la respuesta. None = sin tope explicito.
    thinking_budget: int | None = None
    # El paper fija temperature=0 y penalizaciones=0 para CQbyCQ. Gemini si
    # acepta `temperature`, asi que se replica el ajuste del paper tal cual.
    # Ponlo a None para dejar el valor por defecto del modelo.
    temperature: float | None = 0.0
    # Dos capas de reintento (ver `llm.py`):
    #   - transporte: las gestiona el SDK, para cortes de red y 5xx puntuales.
    #   - saturacion: las gestiona el pipeline, reemitiendo la llamada entera
    #     cuando la API responde 503 ("high demand") o 429. Son esperas largas
    #     a proposito: un pico de demanda no se pasa en dos segundos.
    max_retries: int = 3                 # intentos de transporte del SDK
    retry_initial_delay: float = 2.0     # segundos
    retry_max_delay: float = 30.0        # segundos
    overload_retries: int = 5            # reintentos de la llamada completa
    overload_backoff_s: float = 20.0     # espera inicial; se duplica, tope 120s

    # --- Metodo -------------------------------------------------------- #
    technique: str = "CQbyCQ"            # CQbyCQ | zero-shot
    # Orden de las secciones del prompt. Sigue al prompt original: las
    # secciones constantes van primero (y forman el prefijo cacheado), la CQ de
    # la etapa va despues, y el grafo de la etapa anterior cierra el prompt,
    # igual que el "Here is the last RDF" del original.
    section_order: list[str] = field(default_factory=lambda: [
        "header", "helper", "story", "task", "footer", "memory"
    ])
    # Como se recupera la memoria externa en cada iteracion:
    #   "canonical" -> se re-serializa el grafo acumulado (recomendado)
    #   "raw"       -> se pasa el script tal cual lo emitio el modelo
    memory_mode: str = "canonical"
    # True  -> el LLM reemite la ontologia entera en cada etapa y su salida
    #          SUSTITUYE a la anterior. Es lo que pide el prompt original
    #          ("your output at each stage is an independent turtle").
    # False -> el LLM devuelve solo el delta y el pipeline lo fusiona.
    llm_does_merge: bool = True
    # Red de seguridad para el modo anterior: si el modelo pierde nodos o
    # aristas de la etapa previa y no los recupera tras las correcciones, se
    # reinyectan y queda registrado en el `record` de la CQ.
    restore_lost: bool = True
    # Intentos del bucle de validacion/correccion sintactica por CQ.
    repair_attempts: int = 2
    # Modela la "additional statement" de la historia como una iteracion final.
    model_additional_statement: bool = True
    namespace: str = "http://example.org/onto#"

    # --- Prompts ------------------------------------------------------- #
    prompt_dir: str = str(PROMPTS_DIR)
    header_file: str = "header_cqbycq.md"
    helper_file: str = "helper.md"
    footer_file: str = "footer.md"
    repair_file: str = "repair.md"
    verify_file: str = "verify_cq.md"

    # --- Salidas ------------------------------------------------------- #
    output_dir: str = "outputs"
    run_id: str = ""

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")

    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        if not path:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError("Claves desconocidas en la config: %s" % sorted(unknown))
        return cls(**data)

    def prompt_path(self, attr: str) -> Path:
        return Path(self.prompt_dir) / getattr(self, attr)

    def run_dir(self) -> Path:
        return Path(self.output_dir) / self.run_id

    def manifest(self, extra: dict | None = None) -> dict:
        """Manifiesto de reproducibilidad: parametros + hash de cada prompt.

        El paper reporta variabilidad entre corridas con prompts identicos;
        guardar el hash de los prompts permite distinguir "cambie el prompt"
        de "el modelo no fue determinista"."""
        prompts = {}
        for attr in ("header_file", "helper_file", "footer_file",
                     "repair_file", "verify_file"):
            p = self.prompt_path(attr)
            if p.exists():
                prompts[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        manifest = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "config": asdict(self),
            "prompt_sha256": prompts,
            "python": platform.python_version(),
            "platform": platform.platform(),
        }
        if extra:
            manifest.update(extra)
        return manifest
