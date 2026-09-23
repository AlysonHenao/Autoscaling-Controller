"""
Motor de Decisión y Políticas de Escalado (Policy Engine)
Basado en el algoritmo formal del Horizontal Pod Autoscaler (HPA) de Kubernetes
y las políticas de Target Tracking de AWS.

Incorpora:
1. Fórmula HPA: desired = ceil(current * (currentCPU / targetCPU))
2. Banda de tolerancia (+/- 10%) para evitar thrashing.
3. Ventana de estabilización para Scale-Down (Anti-Sobreprovisionamiento).
4. Cooldowns asimétricos para respetar el tiempo de provisión de la Golden AMI.
5. Límites estrictos del reto (Min: 1, Max: 5).
"""
import math
import time
from typing import Any, Dict, Tuple
import config
from logger import DECISION_MAINTAIN, DECISION_INCREASE, DECISION_REDUCE

class HPAPolicyEngine:
    def __init__(
        self,
        target_cpu: float = config.TARGET_CPU_UTILIZATION,
        tolerance: float = config.TOLERANCE_BAND,
        min_capacity: int = config.MIN_CAPACITY,
        max_capacity: int = config.MAX_CAPACITY,
        scale_up_cooldown: int = config.SCALE_UP_COOLDOWN_SECONDS,
        scale_down_cooldown: int = config.SCALE_DOWN_COOLDOWN_SECONDS,
        stabilization_cycles: int = config.SCALE_DOWN_STABILIZATION_CYCLES
    ):
        self.target_cpu = target_cpu
        self.tolerance = tolerance
        self.min_capacity = min_capacity
        self.max_capacity = max_capacity
        self.scale_up_cooldown = scale_up_cooldown
        self.scale_down_cooldown = scale_down_cooldown
        self.stabilization_cycles = stabilization_cycles

    def evaluate(
        self,
        current_cpu: float,
        current_capacity: int,
        state: Dict[str, Any],
        current_time: float = None
    ) -> Tuple[str, int, str, Dict[str, Any]]:
        """
        Evalúa la métrica y el estado actual para producir una decisión.
        Retorna: (decisión, nueva_capacidad_objetivo, justificación, estado_actualizado)
        """
        if current_time is None:
            current_time = time.time()

        updated_state = dict(state)
        updated_state["last_observed_cpu"] = current_cpu
        cooldown_expires = updated_state.get("cooldown_expires_at", 0.0)

        # 1. Comprobar si hay un período de enfriamiento (Cooldown) activo
        if current_time < cooldown_expires:
            remaining_cd = int(cooldown_expires - current_time)
            justification = (
                f"Período de enfriamiento (Cooldown) activo por {remaining_cd}s más. "
                f"Esperando estabilización de la infraestructura y registro de instancias."
            )
            updated_state["last_decision"] = DECISION_MAINTAIN
            return DECISION_MAINTAIN, current_capacity, justification, updated_state

        # 2. Calcular ratio de uso respecto al Target según HPA
        # Evitar división por cero en casos extremos
        target_ref = max(1.0, self.target_cpu)
        usage_ratio = current_cpu / target_ref

        # 3. Banda de Tolerancia (+/- 10% según estándar de Kubernetes HPA)
        lower_threshold = target_ref * (1.0 - self.tolerance)
        upper_threshold = target_ref * (1.0 + self.tolerance)

        if lower_threshold <= current_cpu <= upper_threshold:
            # CPU en zona óptima de confort
            updated_state["scale_down_consecutive_low"] = 0
            justification = (
                f"CPU actual ({current_cpu:.1f}%) se encuentra dentro de la banda de tolerancia HPA "
                f"[{lower_threshold:.1f}%, {upper_threshold:.1f}%] para el objetivo de {self.target_cpu}%. "
                f"Capacidad {current_capacity} adecuada."
            )
            updated_state["last_decision"] = DECISION_MAINTAIN
            return DECISION_MAINTAIN, current_capacity, justification, updated_state

        # 4. Caso Scale-Out: CPU por encima de la banda superior
        if current_cpu > upper_threshold:
            updated_state["scale_down_consecutive_low"] = 0
            
            # Aplicar fórmula formal HPA: ceil(current * (currentCPU / targetCPU))
            raw_desired = math.ceil(current_capacity * usage_ratio)
            desired_capacity = max(self.min_capacity, min(self.max_capacity, raw_desired))

            if desired_capacity > current_capacity:
                justification = (
                    f"Demanda elevada: CPU ({current_cpu:.1f}%) supera el umbral superior ({upper_threshold:.1f}%). "
                    f"Fórmula HPA calcula {raw_desired} instancias. Se escala de {current_capacity} a {desired_capacity} "
                    f"para preservar el SLO de la aplicación."
                )
                updated_state["cooldown_expires_at"] = current_time + self.scale_up_cooldown
                updated_state["last_action_timestamp"] = current_time
                updated_state["current_capacity"] = desired_capacity
                updated_state["last_decision"] = DECISION_INCREASE
                return DECISION_INCREASE, desired_capacity, justification, updated_state
            else:
                justification = (
                    f"CPU ({current_cpu:.1f}%) alta, pero ya se alcanzó la capacidad máxima permitida "
                    f"por las restricciones del sistema ({self.max_capacity} instancias)."
                )
                updated_state["last_decision"] = DECISION_MAINTAIN
                return DECISION_MAINTAIN, current_capacity, justification, updated_state

        # 5. Caso Scale-In: CPU por debajo de la banda inferior (Prevención de Sobreprovisionamiento)
        if current_cpu < lower_threshold:
            consecutive = updated_state.get("scale_down_consecutive_low", 0) + 1
            updated_state["scale_down_consecutive_low"] = consecutive

            # Verificar ventana de estabilización (Estándar Kubernetes HPA: 300 segundos / 5 periodos)
            if consecutive < self.stabilization_cycles:
                justification = (
                    f"Baja utilización observada: CPU ({current_cpu:.1f}%) < {lower_threshold:.1f}%. "
                    f"Ventana de estabilización en progreso ({consecutive}/{self.stabilization_cycles} ciclos). "
                    f"Manteniendo capacidad para descartar variaciones transitorias y evitar flapping."
                )
                updated_state["last_decision"] = DECISION_MAINTAIN
                return DECISION_MAINTAIN, current_capacity, justification, updated_state

            # Ventana de estabilización superada: demanda baja sostenida confirmada
            if current_capacity <= self.min_capacity:
                justification = (
                    f"Demanda baja sostenida ({consecutive} ciclos), pero ya se opera en la capacidad "
                    f"mínima permitida ({self.min_capacity} instancia)."
                )
                updated_state["last_decision"] = DECISION_MAINTAIN
                return DECISION_MAINTAIN, current_capacity, justification, updated_state

            # Reducción conservadora (de 1 en 1) para evitar sobrecargar abruptamente a las instancias restantes
            desired_capacity = max(self.min_capacity, current_capacity - config.MAX_SCALE_DOWN_STEP)
            
            justification = (
                f"Sobreprovisionamiento detectado y sostenido durante {consecutive} ciclos continuos. "
                f"CPU promedio ({current_cpu:.1f}%) < {lower_threshold:.1f}%. "
                f"Reduciendo de forma segura y conservadora de {current_capacity} a {desired_capacity} instancias "
                f"para optimizar costos sin degradar el servicio."
            )
            updated_state["cooldown_expires_at"] = current_time + self.scale_down_cooldown
            updated_state["last_action_timestamp"] = current_time
            updated_state["scale_down_consecutive_low"] = 0
            updated_state["current_capacity"] = desired_capacity
            updated_state["last_decision"] = DECISION_REDUCE
            return DECISION_REDUCE, desired_capacity, justification, updated_state

        # Por seguridad y consistencia
        return DECISION_MAINTAIN, current_capacity, "Condición estable por defecto.", updated_state
