"""
Módulo Sensor / Monitor de CloudWatch
Consulta métricas de telemetría:
1. CPU (AWS/EC2 CPUUtilization) promedio del Auto Scaling Group.
2. RequestCountPerTarget (AWS/ApplicationELB) peticiones por destino en el ALB.
Implementa tolerancia a métricas faltantes, retrasadas o anómalas (Hold-Last-Known).
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import config

class CloudWatchMonitor:
    def __init__(self, region_name: str = config.AWS_REGION, asg_name: str = config.AUTO_SCALING_GROUP_NAME):
        self.region_name = region_name
        self.asg_name = asg_name
        self._cw_client = None
        self._asg_client = None
        self._last_valid_cpu = None
        self._last_valid_req = None
        self._target_group_dim = None

        self._init_clients()

    def _init_clients(self):
        try:
            import boto3
            self._cw_client = boto3.client("cloudwatch", region_name=self.region_name)
            self._asg_client = boto3.client("autoscaling", region_name=self.region_name)
        except Exception:
            self._cw_client = None
            self._asg_client = None

    def _resolve_target_group_dimension(self) -> Optional[str]:
        """Obtiene la dimensión TargetGroup para CloudWatch AWS/ApplicationELB."""
        if self._target_group_dim:
            return self._target_group_dim

        # 1. Configuración explícita en config.py
        if config.ALB_TARGET_GROUP_DIMENSION:
            self._target_group_dim = config.ALB_TARGET_GROUP_DIMENSION
            return self._target_group_dim

        # 2. Extraer de TARGET_GROUP_ARN si existe
        if config.TARGET_GROUP_ARN and "targetgroup/" in config.TARGET_GROUP_ARN:
            self._target_group_dim = "targetgroup/" + config.TARGET_GROUP_ARN.split("targetgroup/")[1]
            return self._target_group_dim

        # 3. Intentar auto-descubrir desde el Auto Scaling Group
        if self._asg_client:
            try:
                resp = self._asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[self.asg_name])
                asgs = resp.get("AutoScalingGroups", [])
                if asgs and asgs[0].get("TargetGroupARNs"):
                    tg_arn = asgs[0]["TargetGroupARNs"][0]
                    if "targetgroup/" in tg_arn:
                        self._target_group_dim = "targetgroup/" + tg_arn.split("targetgroup/")[1]
                        return self._target_group_dim
            except Exception:
                pass

        return None

    def get_cpu_utilization(self, simulated_value: Optional[float] = None) -> Dict[str, Any]:
        """
        Obtiene la utilización de CPU promedio del grupo en los últimos periodos.
        Soporta inyección de valores simulados para pruebas offline y tests automatizados.
        """
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(minutes=5)

        if simulated_value is not None:
            self._last_valid_cpu = simulated_value
            return {
                "cpu_utilization": simulated_value,
                "metric_name": config.METRIC_CPU_NAME,
                "unit": "Percent",
                "source": "Simulation",
                "is_estimated": False,
                "interval_start": start_time.isoformat(),
                "interval_end": now.isoformat(),
                "sample_count": 1
            }

        if self._cw_client:
            try:
                response = self._cw_client.get_metric_data(
                    MetricDataQueries=[
                        {
                            "Id": "asg_cpu",
                            "MetricStat": {
                                "Metric": {
                                    "Namespace": config.METRIC_CPU_NAMESPACE,
                                    "MetricName": config.METRIC_CPU_NAME,
                                    "Dimensions": [
                                        {
                                            "Name": "AutoScalingGroupName",
                                            "Value": self.asg_name
                                        }
                                    ]
                                },
                                "Period": config.SAMPLE_PERIOD_SECONDS,
                                "Stat": config.STATISTIC_CPU
                            },
                            "ReturnData": True
                        }
                    ],
                    StartTime=start_time,
                    EndTime=now,
                    ScanBy="TimestampDescending"
                )

                results = response.get("MetricDataResults", [])
                if results and len(results[0].get("Values", [])) > 0:
                    latest_cpu = float(results[0]["Values"][0])
                    self._last_valid_cpu = latest_cpu
                    return {
                        "cpu_utilization": round(latest_cpu, 2),
                        "metric_name": config.METRIC_CPU_NAME,
                        "unit": "Percent",
                        "source": "AWS/CloudWatch",
                        "is_estimated": False,
                        "interval_start": start_time.isoformat(),
                        "interval_end": now.isoformat(),
                        "sample_count": len(results[0]["Values"])
                    }
            except Exception:
                pass

        # Fallback de resiliencia (Hold-Last-Known)
        fallback_cpu = self._last_valid_cpu if self._last_valid_cpu is not None else config.TARGET_CPU_UTILIZATION
        return {
            "cpu_utilization": round(fallback_cpu, 2),
            "metric_name": config.METRIC_CPU_NAME,
            "unit": "Percent",
            "source": "Fallback_HoldLastKnown",
            "is_estimated": True,
            "interval_start": start_time.isoformat(),
            "interval_end": now.isoformat(),
            "sample_count": 0
        }

    def get_requests_per_target(self, simulated_value: Optional[float] = None) -> Dict[str, Any]:
        """
        Obtiene la métrica RequestCountPerTarget del ALB en el último período de 60s.
        """
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(minutes=5)

        if simulated_value is not None:
            self._last_valid_req = simulated_value
            return {
                "requests_per_target": simulated_value,
                "metric_name": config.METRIC_REQ_NAME,
                "unit": "Count",
                "source": "Simulation",
                "is_estimated": False,
                "is_active": True
            }

        if not config.ENABLE_MULTI_METRIC:
            return {
                "requests_per_target": None,
                "metric_name": config.METRIC_REQ_NAME,
                "unit": "Count",
                "source": "Disabled",
                "is_estimated": False,
                "is_active": False
            }

        tg_dim = self._resolve_target_group_dimension()
        if not tg_dim or not self._cw_client:
            return {
                "requests_per_target": self._last_valid_req,
                "metric_name": config.METRIC_REQ_NAME,
                "unit": "Count",
                "source": "Fallback_HoldLastKnown" if self._last_valid_req is not None else "NoTargetGroupConfigured",
                "is_estimated": True,
                "is_active": self._last_valid_req is not None
            }

        try:
            response = self._cw_client.get_metric_data(
                MetricDataQueries=[
                    {
                        "Id": "alb_requests",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": config.METRIC_REQ_NAMESPACE,
                                "MetricName": config.METRIC_REQ_NAME,
                                "Dimensions": [
                                    {
                                        "Name": "TargetGroup",
                                        "Value": tg_dim
                                    }
                                ]
                            },
                            "Period": config.SAMPLE_PERIOD_SECONDS,
                            "Stat": config.STATISTIC_REQ
                        },
                        "ReturnData": True
                    }
                ],
                StartTime=start_time,
                EndTime=now,
                ScanBy="TimestampDescending"
            )

            results = response.get("MetricDataResults", [])
            if results and len(results[0].get("Values", [])) > 0:
                latest_req = float(results[0]["Values"][0])
                self._last_valid_req = latest_req
                return {
                    "requests_per_target": round(latest_req, 2),
                    "metric_name": config.METRIC_REQ_NAME,
                    "unit": "Count",
                    "source": "AWS/CloudWatch_ALB",
                    "is_estimated": False,
                    "is_active": True
                }
        except Exception:
            pass

        return {
            "requests_per_target": self._last_valid_req,
            "metric_name": config.METRIC_REQ_NAME,
            "unit": "Count",
            "source": "Fallback_HoldLastKnown" if self._last_valid_req is not None else "Unavailable",
            "is_estimated": True,
            "is_active": self._last_valid_req is not None
        }

    def get_all_metrics(
        self,
        simulated_cpu: Optional[float] = None,
        simulated_req: Optional[float] = None
    ) -> Dict[str, Any]:
        """Obtiene el conjunto multi-métrica para el ciclo de control."""
        cpu_data = self.get_cpu_utilization(simulated_value=simulated_cpu)
        req_data = self.get_requests_per_target(simulated_value=simulated_req)
        return {
            "cpu": cpu_data,
            "requests": req_data
        }
