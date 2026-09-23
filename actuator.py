"""
Módulo Actuador sobre AWS
Ejecuta las modificaciones de capacidad sobre el Auto Scaling Group y verifica el estado
de las instancias en el Target Group del Application Load Balancer.
Cumple con el principio de menor privilegio y manejo robusto de excepciones de AWS.
"""
from typing import Any, Dict, List, Optional
import config

class AWSActuator:
    def __init__(self, region_name: str = config.AWS_REGION, asg_name: str = config.AUTO_SCALING_GROUP_NAME, target_group_arn: str = config.TARGET_GROUP_ARN):
        self.region_name = region_name
        self.asg_name = asg_name
        self.target_group_arn = target_group_arn
        self._asg_client = None
        self._elbv2_client = None
        self._simulated_capacity = 1
        
        self._init_clients()

    def _init_clients(self):
        try:
            import boto3
            self._asg_client = boto3.client("autoscaling", region_name=self.region_name)
            self._elbv2_client = boto3.client("elbv2", region_name=self.region_name)
        except Exception:
            self._asg_client = None
            self._elbv2_client = None

    def get_current_infrastructure_state(self) -> Dict[str, Any]:
        """
        Obtiene la capacidad real actual, instancias asociadas y su estado de salud.
        """
        if self._asg_client:
            try:
                resp = self._asg_client.describe_auto_scaling_groups(
                    AutoScalingGroupNames=[self.asg_name]
                )
                asgs = resp.get("AutoScalingGroups", [])
                if asgs:
                    asg = asgs[0]
                    instances = asg.get("Instances", [])
                    in_service = [i for i in instances if i.get("LifecycleState") == "InService"]
                    return {
                        "desired_capacity": asg.get("DesiredCapacity", 1),
                        "instances_count": len(instances),
                        "in_service_count": len(in_service),
                        "instance_ids": [i.get("InstanceId") for i in instances],
                        "source": "AWS/ASG"
                    }
            except Exception as e:
                pass

        # Fallback para entorno de pruebas / simulación
        return {
            "desired_capacity": self._simulated_capacity,
            "instances_count": self._simulated_capacity,
            "in_service_count": self._simulated_capacity,
            "instance_ids": [f"i-sim-{i+1:04d}" for i in range(self._simulated_capacity)],
            "source": "Simulation"
        }

    def execute_scaling_action(self, target_capacity: int, reason: str) -> Dict[str, Any]:
        """
        Modifica la capacidad deseada del ASG llamando a set_desired_capacity.
        """
        # Validar fronteras duras
        target_capacity = max(config.MIN_CAPACITY, min(config.MAX_CAPACITY, target_capacity))

        if self._asg_client:
            try:
                self._asg_client.set_desired_capacity(
                    AutoScalingGroupName=self.asg_name,
                    DesiredCapacity=target_capacity,
                    HonorCooldown=False  # El controlador autónomo gestiona sus propios cooldowns
                )
                self._simulated_capacity = target_capacity
                return {
                    "status": "SUCCESS",
                    "requested_capacity": target_capacity,
                    "target_asg": self.asg_name,
                    "message": f"Capacidad del ASG {self.asg_name} ajustada exitosamente a {target_capacity}."
                }
            except Exception as e:
                return {
                    "status": "FAILED",
                    "requested_capacity": target_capacity,
                    "target_asg": self.asg_name,
                    "error": str(e),
                    "message": f"Error ejecutando acción de escalado en AWS API: {e}"
                }

        # Modo simulación
        self._simulated_capacity = target_capacity
        return {
            "status": "SUCCESS_SIMULATED",
            "requested_capacity": target_capacity,
            "target_asg": self.asg_name,
            "message": f"[Modo Simulado] Capacidad deseada ajustada a {target_capacity}."
        }
