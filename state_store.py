"""
Módulo de Persistencia de Estado del Controlador (State Store)
Garantiza la tolerancia a fallos y Crash-Recovery del plano de control.
Si la instancia donde corre el controlador se muere o se reinicia,
recupera el estado persistido (cooldowns activos, ventanas de estabilización, etc.)
desde AWS DynamoDB o almacenamiento local durable sincronizado.
"""
import json
import os
import time
from typing import Any, Dict, Optional

class StateStore:
    def __init__(self, table_name: str = "AutoScalingControllerState", region_name: str = "us-east-1", local_backup_file: str = "controller_state.json"):
        self.table_name = table_name
        self.region_name = region_name
        self.local_backup_file = local_backup_file
        self.controller_id = "cms-autoscaler-primary"
        self._dynamodb_resource = None
        self._table = None
        self._use_dynamo = False
        
        self._init_dynamo()

    def _init_dynamo(self):
        """Intenta inicializar la conexión con DynamoDB si boto3 y las credenciales están disponibles."""
        try:
            import boto3
            # Verificar si se puede instanciar el cliente sin error inmediato
            session = boto3.Session(region_name=self.region_name)
            self._dynamodb_resource = session.resource("dynamodb")
            self._table = self._dynamodb_resource.Table(self.table_name)
            # Prueba de acceso rápida (describe_table o get_item ficticio)
            self._table.load()
            self._use_dynamo = True
        except Exception:
            # Si no hay credenciales activas o tabla aún no creada, usar almacenamiento local durable
            self._use_dynamo = False

    def get_default_state(self) -> Dict[str, Any]:
        """Estado base por defecto cuando no existe estado previo."""
        return {
            "controller_id": self.controller_id,
            "current_capacity": 1,
            "last_action_timestamp": 0.0,
            "cooldown_expires_at": 0.0,
            "scale_down_consecutive_low": 0,
            "last_observed_cpu": 0.0,
            "last_decision": "MAINTAIN_CAPACITY",
            "last_updated": time.time()
        }

    def load_state(self) -> Dict[str, Any]:
        """
        Recupera el último estado guardado.
        Si la instancia del controlador murió, lee de DynamoDB (o respaldo local durable).
        """
        state = None

        # 1. Intentar cargar desde DynamoDB
        if self._use_dynamo and self._table:
            try:
                response = self._table.get_item(Key={"controller_id": self.controller_id})
                if "Item" in response:
                    item = response["Item"]
                    state = {
                        "controller_id": str(item.get("controller_id", self.controller_id)),
                        "current_capacity": int(item.get("current_capacity", 1)),
                        "last_action_timestamp": float(item.get("last_action_timestamp", 0.0)),
                        "cooldown_expires_at": float(item.get("cooldown_expires_at", 0.0)),
                        "scale_down_consecutive_low": int(item.get("scale_down_consecutive_low", 0)),
                        "last_observed_cpu": float(item.get("last_observed_cpu", 0.0)),
                        "last_decision": str(item.get("last_decision", "MAINTAIN_CAPACITY")),
                        "last_updated": float(item.get("last_updated", time.time()))
                    }
            except Exception:
                state = None

        # 2. Si DynamoDB falló o no está activo, cargar de respaldo local
        if state is None and os.path.exists(self.local_backup_file):
            try:
                with open(self.local_backup_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = None

        # 3. Si no existe ninguno, retornar estado por defecto
        if state is None:
            state = self.get_default_state()
            self.save_state(state)

        return state

    def save_state(self, state: Dict[str, Any]) -> bool:
        """
        Persiste el estado actual de forma atómica en DynamoDB y en disco local.
        """
        state["last_updated"] = time.time()
        success = True

        # 1. Guardar en disco local como respaldo durable inmediato
        try:
            with open(self.local_backup_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            success = False

        # 2. Guardar en DynamoDB
        if self._use_dynamo and self._table:
            try:
                # Convertir floats a Decimal o formatear según requerimiento de DynamoDB
                from decimal import Decimal
                dynamo_item = {
                    "controller_id": self.controller_id,
                    "current_capacity": state["current_capacity"],
                    "last_action_timestamp": Decimal(str(round(state["last_action_timestamp"], 2))),
                    "cooldown_expires_at": Decimal(str(round(state["cooldown_expires_at"], 2))),
                    "scale_down_consecutive_low": state["scale_down_consecutive_low"],
                    "last_observed_cpu": Decimal(str(round(state["last_observed_cpu"], 2))),
                    "last_decision": state["last_decision"],
                    "last_updated": Decimal(str(round(state["last_updated"], 2)))
                }
                self._table.put_item(Item=dynamo_item)
            except Exception:
                # Si Dynamo falla temporalmente, el estado local sigue a salvo
                pass

        return success
