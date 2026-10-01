"""
Bucle Principal del Auto-Scaling Controller (Main Control Loop)
Ejecuta el ciclo continuo de retroalimentación:
observe -> analyze -> decide -> act -> observe again

Garantiza:
- Control multi-métrica formal (Kubernetes HPA con CPU y RequestCountPerTarget del ALB).
- Explicabilidad y registro estructurado según el reto SI3016.
- Tolerancia a caídas mediante persistencia en DynamoDB (Crash-Recovery).
- Control autónomo de capacidad (1 a 5 instancias) sin políticas gestionadas por AWS.
"""
import argparse
import sys
import time
from typing import Optional

import config
from actuator import AWSActuator
from logger import ControllerLogger, DECISION_MAINTAIN, DECISION_INCREASE, DECISION_REDUCE
from monitor import CloudWatchMonitor
from policy import HPAPolicyEngine
from state_store import StateStore

def run_controller_cycle(
    monitor: CloudWatchMonitor,
    policy: HPAPolicyEngine,
    actuator: AWSActuator,
    state_store: StateStore,
    logger: ControllerLogger,
    simulated_cpu: Optional[float] = None,
    simulated_req: Optional[float] = None
) -> dict:
    """
    Ejecuta un ciclo individual del bucle MAPE-K.
    """
    cycle_start_time = time.time()

    # 1. OBSERVE (Monitorear métricas agregadas e infraestructura)
    all_metrics = monitor.get_all_metrics(simulated_cpu=simulated_cpu, simulated_req=simulated_req)
    cpu_data = all_metrics["cpu"]
    req_data = all_metrics["requests"]

    infra_state = actuator.get_current_infrastructure_state()
    persisted_state = state_store.load_state()

    # Sincronizar capacidad conocida entre infraestructura real y estado guardado
    current_capacity = infra_state.get("desired_capacity", persisted_state.get("current_capacity", 1))
    persisted_state["current_capacity"] = current_capacity

    current_cpu = cpu_data["cpu_utilization"]
    current_req = req_data.get("requests_per_target")

    # 2 & 3. ANALYZE & DECIDE (Motor HPA Multi-métrica + Anti-Sobreprovisionamiento)
    decision, target_capacity, justification, updated_state = policy.evaluate(
        current_cpu=current_cpu,
        current_capacity=current_capacity,
        state=persisted_state,
        current_requests=current_req,
        current_time=cycle_start_time
    )

    # 4. ACT (Actuar sobre la infraestructura si la decisión lo amerita)
    action_requested = "NO_ACTION"
    action_result = "CAPACITY_UNCHANGED"

    if decision in (DECISION_INCREASE, DECISION_REDUCE) and target_capacity != current_capacity:
        action_requested = f"SET_DESIRED_CAPACITY_{target_capacity}"
        act_res = actuator.execute_scaling_action(target_capacity, reason=justification)
        action_result = act_res.get("status", "UNKNOWN")
        if action_result in ("SUCCESS", "SUCCESS_SIMULATED"):
            updated_state["current_capacity"] = target_capacity
    else:
        action_requested = f"KEEP_CAPACITY_{current_capacity}"
        action_result = "MAINTAINED"

    # 5. PERSIST STATE (Salvar en DynamoDB para Crash Recovery)
    state_store.save_state(updated_state)

    # 6. AUDIT LOG (Registrar evento estructurado exigido por el reto)
    observation_interval = {
        "start": cpu_data["interval_start"],
        "end": cpu_data["interval_end"],
        "period_seconds": config.SAMPLE_PERIOD_SECONDS,
        "sample_count": cpu_data["sample_count"],
        "is_estimated": cpu_data["is_estimated"]
    }

    metrics_payload = {
        "CPUUtilization": current_cpu,
        "CPU_source": cpu_data["source"]
    }
    if current_req is not None:
        metrics_payload["RequestCountPerTarget"] = current_req
        metrics_payload["Requests_source"] = req_data.get("source", "AWS/CloudWatch_ALB")

    log_entry = logger.log_cycle(
        decision=decision,
        justification=justification,
        metrics=metrics_payload,
        observation_interval=observation_interval,
        existing_capacity=current_capacity,
        existing_state=infra_state,
        action_requested=action_requested,
        action_result=action_result,
        extra_metadata={
            "target_cpu": config.TARGET_CPU_UTILIZATION,
            "target_requests": config.TARGET_REQUEST_COUNT_PER_TARGET,
            "tolerance_band": config.TOLERANCE_BAND,
            "stabilization_counter": updated_state.get("scale_down_consecutive_low", 0),
            "cooldown_remaining_sec": max(0, int(updated_state.get("cooldown_expires_at", 0) - cycle_start_time))
        }
    )

    return log_entry

def main():
    parser = argparse.ArgumentParser(description="Auto-Scaling Controller Autónomo Multi-Métrica (SI3016)")
    parser.add_argument("--once", action="store_true", help="Ejecuta un único ciclo y termina")
    parser.add_argument("--interval", type=int, default=config.EVALUATION_INTERVAL_SECONDS, help="Segundos entre ciclos")
    parser.add_argument("--simulate-cpu", type=float, default=None, help="Valor de CPU simulado para pruebas locales")
    parser.add_argument("--simulate-requests", type=float, default=None, help="Valor de peticiones/target simulado para pruebas")
    args = parser.parse_args()

    logger = ControllerLogger()
    logger.info("Iniciando Auto-Scaling Controller Autónomo Multi-Métrica para CMS Web Cluster...")
    logger.info(
        f"Target CPU: {config.TARGET_CPU_UTILIZATION}% | Target Req/Target: {config.TARGET_REQUEST_COUNT_PER_TARGET} | "
        f"Tolerancia: +/-{int(config.TOLERANCE_BAND*100)}% | Rango instancias: [{config.MIN_CAPACITY}, {config.MAX_CAPACITY}]"
    )

    monitor = CloudWatchMonitor()
    policy = HPAPolicyEngine()
    actuator = AWSActuator()
    state_store = StateStore()

    # Cargar y verificar estado inicial
    initial_state = state_store.load_state()
    logger.info(
        f"Estado inicial recuperado (Crash Recovery Check): Capacidad={initial_state['current_capacity']}, "
        f"CooldownHasta={initial_state['cooldown_expires_at']}"
    )

    try:
        while True:
            run_controller_cycle(
                monitor=monitor,
                policy=policy,
                actuator=actuator,
                state_store=state_store,
                logger=logger,
                simulated_cpu=args.simulate_cpu,
                simulated_req=args.simulate_requests
            )

            if args.once:
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        logger.info("Detención ordenada solicitada por el usuario (SIGINT). Estado preservado en DynamoDB.")
        sys.exit(0)

if __name__ == "__main__":
    main()
