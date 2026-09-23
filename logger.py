"""
Módulo de Auditoría y Registro Estructurado (Logger)
Cumple con la sección 9 del documento SI3016 (Reconstrucción completa de cada ciclo de decisión).
"""
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

# Enumeración de decisiones obligatorias según Listing 1
DECISION_MAINTAIN = "MAINTAIN_CAPACITY"
DECISION_INCREASE = "INCREASE_CAPACITY"
DECISION_REDUCE = "REDUCE_CAPACITY"

class ControllerLogger:
    def __init__(self, log_file: str = "controller_decisions.jsonl"):
        self.log_file = log_file
        
        # Configurar logging estándar para consola
        self.console_logger = logging.getLogger("AutoScalingController")
        self.console_logger.setLevel(logging.INFO)
        if not self.console_logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
            handler.setFormatter(formatter)
            self.console_logger.addHandler(handler)

    def log_cycle(
        self,
        decision: str,
        justification: str,
        metrics: Dict[str, Any],
        observation_interval: Dict[str, Any],
        existing_capacity: int,
        existing_state: Dict[str, Any],
        action_requested: str,
        action_result: str,
        extra_metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Registra el ciclo de decisión de forma estructurada según los requerimientos del reto:
        - Tiempo exacto de la decisión
        - Métricas e intervalo observado
        - Capacidad existente y su estado
        - Decisión tomada y justificación matemática/operativa
        - Acción solicitada a la infraestructura
        - Resultado de la acción
        """
        now = datetime.now(timezone.utc).isoformat()
        
        record = {
            "timestamp": now,
            "decision": decision,
            "justification": justification,
            "metrics": metrics,
            "observation_interval": observation_interval,
            "existing_capacity": existing_capacity,
            "existing_state": existing_state,
            "action_requested": action_requested,
            "action_result": action_result,
            "extra_metadata": extra_metadata or {}
        }

        # Imprimir en consola de forma clara y destacada
        self.console_logger.info(
            f"=== CICLO DE CONTROL ===\n"
            f"  Decisión: {decision}\n"
            f"  Capacidad actual: {existing_capacity} | Acción: {action_requested} | Resultado: {action_result}\n"
            f"  Métricas: {metrics}\n"
            f"  Justificación: {justification}\n"
            f"========================"
        )

        # Persistir en archivo JSON Lines para análisis experimental y evidencias
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            self.console_logger.error(f"Error escribiendo en bitácora de decisiones: {e}")

        return record

    def info(self, msg: str):
        self.console_logger.info(msg)

    def warning(self, msg: str):
        self.console_logger.warning(msg)

    def error(self, msg: str):
        self.console_logger.error(msg)
