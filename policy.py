"""
Motor de Decisión y Políticas de Escalado Multi-Métrica (Policy Engine)
Basado en el algoritmo formal del Horizontal Pod Autoscaler (HPA) de Kubernetes
y las directrices de AWS Target Tracking.

Soporta decisión multi-criterio:
1. Criterio de Saturación de Recursos: CPUUtilization (AWS/EC2)
2. Criterio de Carga de Trabajo / Throughput: RequestCountPerTarget (AWS/ApplicationELB)

Algoritmo formal multi-métrica de Kubernetes HPA:
- desired_capacity = max(ceil(current * (metric_i / target_i))) para todas las métricas activas.
- Histéresis: Banda de tolerancia (+/- 10%) por métrica.
- Anti-Thrashing: Ventana de estabilización de 5 ciclos continuos (ambas métricas deben estar bajas).
- Cooldowns asimétricos: 120s para Scale-Up, 240s para Scale-Down.
- Límites estrictos del clúster: [1, 5] instancias.
"""
import math
import time
from typing import Any, Dict, Optional, Tuple
import config
from logger import DECISION_MAINTAIN, DECISION_INCREASE, DECISION_REDUCE

class HPAPolicyEngine:
    def __init__(
        self,
        target_cpu: float = config.TARGET_CPU_UTILIZATION,
        target_requests: float = config.TARGET_REQUEST_COUNT_PER_TARGET,
        tolerance: float = config.TOLERANCE_BAND,
        min_capacity: int = config.MIN_CAPACITY,
        max_capacity: int = config.MAX_CAPACITY,
        scale_up_cooldown: int = config.SCALE_UP_COOLDOWN_SECONDS,
        scale_down_cooldown: int = config.SCALE_DOWN_COOLDOWN_SECONDS,
        stabilization_cycles: int = config.SCALE_DOWN_STABILIZATION_CYCLES
    ):
        self.target_cpu = target_cpu
        self.target_requests = target_requests
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
        current_requests: Optional[float] = None,
        current_time: float = None
    ) -> Tuple[str, int, str, Dict[str, Any]]:
        """
        Evalúa las métricas y el estado actual para producir una decisión.
        Retorna: (decisión, nueva_capacidad_objetivo, justificación, estado_actualizado)
        """
        if current_time is None:
            current_time = time.time()

        updated_state = dict(state)
        updated_state["last_observed_cpu"] = current_cpu
        if current_requests is not None:
            updated_state["last_observed_requests"] = current_requests

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

        # 2. Umbrales para CPU
        target_cpu_ref = max(1.0, self.target_cpu)
        cpu_lower = target_cpu_ref * (1.0 - self.tolerance)
        cpu_upper = target_cpu_ref * (1.0 + self.tolerance)
        cpu_ratio = current_cpu / target_cpu_ref
        desired_by_cpu = math.ceil(current_capacity * cpu_ratio)

        # 3. Umbrales para RequestCountPerTarget (si está activa la métrica)
        has_requests_metric = current_requests is not None and config.ENABLE_MULTI_METRIC
        req_ratio = 1.0
        desired_by_req = current_capacity
        req_is_high = False
        req_is_low = True

        if has_requests_metric:
            target_req_ref = max(1.0, self.target_requests)
            req_lower = target_req_ref * (1.0 - self.tolerance)
            req_upper = target_req_ref * (1.0 + self.tolerance)
            req_ratio = current_requests / target_req_ref
            desired_by_req = math.ceil(current_capacity * req_ratio)
            req_is_high = current_requests > req_upper
            req_is_low = current_requests < req_lower

        cpu_is_high = current_cpu > cpu_upper
        cpu_is_low = current_cpu < cpu_lower

        # 4. Caso SCALE-OUT (Aumento de Capacidad)
        # Regla Kubernetes HPA Multi-métrica: max(ceil(current * (m_i / target_i)))
        if cpu_is_high or req_is_high:
            updated_state["scale_down_consecutive_low"] = 0

            # Determinar capacidad deseada máxima entre las métricas activas
            if has_requests_metric and req_is_high and cpu_is_high:
                raw_desired = max(desired_by_cpu, desired_by_req)
                trigger_cause = f"Ambos criterios superados: CPU ({current_cpu:.1f}% > {cpu_upper:.1f}%) y Tráfico ALB ({current_requests:.1f} > {req_upper:.1f} req/target)"
            elif cpu_is_high:
                raw_desired = desired_by_cpu
                trigger_cause = f"Saturación de cómputo: CPU ({current_cpu:.1f}%) supera el umbral superior ({cpu_upper:.1f}%)"
            else:
                raw_desired = desired_by_req
                trigger_cause = f"Alto volumen de peticiones ALB: ({current_requests:.1f} req/target) supera el umbral superior ({req_upper:.1f} req/target)"

            desired_capacity = max(self.min_capacity, min(self.max_capacity, raw_desired))

            if desired_capacity > current_capacity:
                justification = (
                    f"Scale-Out requerido: {trigger_cause}. "
                    f"Fórmula multi-métrica HPA calcula {raw_desired} instancias. "
                    f"Se escala de {current_capacity} a {desired_capacity} para proteger el SLO."
                )
                updated_state["cooldown_expires_at"] = current_time + self.scale_up_cooldown
                updated_state["last_action_timestamp"] = current_time
                updated_state["current_capacity"] = desired_capacity
                updated_state["last_decision"] = DECISION_INCREASE
                return DECISION_INCREASE, desired_capacity, justification, updated_state
            else:
                justification = (
                    f"{trigger_cause}, pero ya se alcanzó la capacidad máxima permitida "
                    f"({self.max_capacity} instancias)."
                )
                updated_state["last_decision"] = DECISION_MAINTAIN
                return DECISION_MAINTAIN, current_capacity, justification, updated_state

        # 5. Caso SCALE-IN (Reducción de Capacidad con Protección Multi-métrica)
        # Ambas métricas deben estar en baja demanda para permitir scale-in
        is_safe_to_scale_in = cpu_is_low and (req_is_low if has_requests_metric else True)

        if is_safe_to_scale_in:
            consecutive = updated_state.get("scale_down_consecutive_low", 0) + 1
            updated_state["scale_down_consecutive_low"] = consecutive

            # Verificar ventana de estabilización (300 segundos / 5 ciclos continuos)
            if consecutive < self.stabilization_cycles:
                metric_desc = f"CPU={current_cpu:.1f}% < {cpu_lower:.1f}%"
                if has_requests_metric:
                    metric_desc += f" y Requests={current_requests:.1f} < {req_lower:.1f}"
                justification = (
                    f"Baja utilización observada ({metric_desc}). "
                    f"Ventana de estabilización en progreso ({consecutive}/{self.stabilization_cycles} ciclos). "
                    f"Manteniendo capacidad para descartar variaciones transitorias y evitar flapping."
                )
                updated_state["last_decision"] = DECISION_MAINTAIN
                return DECISION_MAINTAIN, current_capacity, justification, updated_state

            # Ventana completada
            if current_capacity <= self.min_capacity:
                justification = (
                    f"Demanda baja sostenida ({consecutive} ciclos), pero ya se opera en la capacidad "
                    f"mínima permitida ({self.min_capacity} instancia)."
                )
                updated_state["last_decision"] = DECISION_MAINTAIN
                return DECISION_MAINTAIN, current_capacity, justification, updated_state

            # Reducción conservadora de 1 en 1
            desired_capacity = max(self.min_capacity, current_capacity - config.MAX_SCALE_DOWN_STEP)
            justification = (
                f"Sobreprovisionamiento sostenido durante {consecutive} ciclos continuos. "
                f"Métricas por debajo de la zona de confort. "
                f"Reduciendo de forma segura de {current_capacity} a {desired_capacity} instancias para optimizar costos."
            )
            updated_state["cooldown_expires_at"] = current_time + self.scale_down_cooldown
            updated_state["last_action_timestamp"] = current_time
            updated_state["scale_down_consecutive_low"] = 0
            updated_state["current_capacity"] = desired_capacity
            updated_state["last_decision"] = DECISION_REDUCE
            return DECISION_REDUCE, desired_capacity, justification, updated_state

        # 6. Zona de Confort (MAINTAIN_CAPACITY)
        updated_state["scale_down_consecutive_low"] = 0
        status_parts = [f"CPU actual ({current_cpu:.1f}%) en zona adecuada [{cpu_lower:.1f}%, {cpu_upper:.1f}%]"]
        if has_requests_metric:
            status_parts.append(f"Tráfico ALB ({current_requests:.1f} req/target) en rango [{req_lower:.1f}, {req_upper:.1f}]")
        justification = "; ".join(status_parts) + f". Capacidad {current_capacity} óptima."
        updated_state["last_decision"] = DECISION_MAINTAIN
        return DECISION_MAINTAIN, current_capacity, justification, updated_state
