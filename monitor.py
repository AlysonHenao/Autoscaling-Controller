"""
Módulo Sensor / Monitor de CloudWatch
Consulta la métrica de CPU (AWS/EC2 CPUUtilization) promedio del Auto Scaling Group.
Implementa tolerancia a métricas faltantes, retrasadas o anómalas.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import config

class CloudWatchMonitor:
    def __init__(self, region_name: str = config.AWS_REGION, asg_name: str = config.AUTO_SCALING_GROUP_NAME):
        self.region_name = region_name
        self.asg_name = asg_name
        self._cw_client = None
        self._last_valid_cpu = None

        self._init_client()

    def _init_client(self):
        try:
            import boto3
            self._cw_client = boto3.client("cloudwatch", region_name=self.region_name)
        except Exception:
            self._cw_client = None

    def get_cpu_utilization(self, simulated_value: Optional[float] = None) -> Dict[str, Any]:
        """
        Obtiene la utilización de CPU promedio del grupo en los últimos periodos.
        Soporta inyección de valores simulados para pruebas offline y tests automatizados.
        """
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(minutes=5)

        # Si se pasa un valor simulado (para experimentación y pruebas)
        if simulated_value is not None:
            self._last_valid_cpu = simulated_value
            return {
                "cpu_utilization": simulated_value,
                "metric_name": config.METRIC_NAME,
                "unit": "Percent",
                "source": "Simulation",
                "is_estimated": False,
                "interval_start": start_time.isoformat(),
                "interval_end": now.isoformat(),
                "sample_count": 1
            }

        # Consulta real a AWS CloudWatch
        if self._cw_client:
            try:
                response = self._cw_client.get_metric_data(
                    MetricDataQueries=[
                        {
                            "Id": "asg_cpu",
                            "MetricStat": {
                                "Metric": {
                                    "Namespace": config.METRIC_NAMESPACE,
                                    "MetricName": config.METRIC_NAME,
                                    "Dimensions": [
                                        {
                                            "Name": "AutoScalingGroupName",
                                            "Value": self.asg_name
                                        }
                                    ]
                                },
                                "Period": config.SAMPLE_PERIOD_SECONDS,
                                "Stat": config.STATISTIC
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
                        "metric_name": config.METRIC_NAME,
                        "unit": "Percent",
                        "source": "AWS/CloudWatch",
                        "is_estimated": False,
                        "interval_start": start_time.isoformat(),
                        "interval_end": now.isoformat(),
                        "sample_count": len(results[0]["Values"])
                    }
            except Exception as e:
                # Manejo de fallos en la llamada a AWS CloudWatch
                pass

        # Manejo de métricas ausentes o retrasadas (resiliencia)
        # Nunca tomar decisiones destructivas por falta de datos
        fallback_cpu = self._last_valid_cpu if self._last_valid_cpu is not None else config.TARGET_CPU_UTILIZATION
        return {
            "cpu_utilization": round(fallback_cpu, 2),
            "metric_name": config.METRIC_NAME,
            "unit": "Percent",
            "source": "Fallback_HoldLastKnown",
            "is_estimated": True,
            "interval_start": start_time.isoformat(),
            "interval_end": now.isoformat(),
            "sample_count": 0
        }
