"""
Configuración del Auto-Scaling Controller
Basado en las especificaciones del reto SI3016 y los principios de Kubernetes HPA / AWS Target Tracking.
"""
import os

# --- AWS Configuration ---
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AUTO_SCALING_GROUP_NAME = os.getenv("ASG_NAME", "asg-tc-cluster")
TARGET_GROUP_ARN = os.getenv("TARGET_GROUP_ARN", "")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "AutoScalingControllerState")

# --- Scaling Bounds (Restricción del Reto: 1 a 5 instancias) ---
MIN_CAPACITY = 1
MAX_CAPACITY = 5

# --- Metric Configuration (Criterio Único: CPU) ---
METRIC_NAME = "CPUUtilization"
METRIC_NAMESPACE = "AWS/EC2"
STATISTIC = "Average"
SAMPLE_PERIOD_SECONDS = 60          # Período de muestreo en CloudWatch (1 minuto)
EVALUATION_INTERVAL_SECONDS = 60    # Frecuencia de ciclo del controlador

# --- HPA & Target Tracking Policy Parameters ---
# Basado en Kubernetes Horizontal Pod Autoscaler (HPA) y AWS Target Tracking.
# Objetivo nominal de diseño: 70.0% (antes del codo de saturación de Kleinrock).
# Sobrescribible por variable de entorno (ej. TARGET_CPU=40.0 para pruebas de laboratorio).
TARGET_CPU_UTILIZATION = float(os.getenv("TARGET_CPU", "70.0"))
TOLERANCE_BAND = 0.10               # Banda de tolerancia (+/- 10% alrededor del target: ej. 63% - 77%)


# --- Anti-Overprovisioning & Stabilization Guards ---
# Previene oscilaciones (thrashing) y sobreprovisionamiento innecesario
SCALE_UP_COOLDOWN_SECONDS = 120     # Enfriamiento tras aumentar capacidad (permite boot de Golden AMI)
SCALE_DOWN_COOLDOWN_SECONDS = 240   # Enfriamiento tras reducir capacidad

# Ventana de estabilización para Scale-Down (Inspirada en Kubernetes HPA Stabilization Window: 300s / 5 periodos)
# La demanda baja debe mantenerse durante N ciclos consecutivos antes de decidir REDUCE_CAPACITY
SCALE_DOWN_STABILIZATION_CYCLES = 5  # 5 minutos de baja carga comprobada

# Paso máximo de escalado descendente para proteger disponibilidad (reducción conservadora de 1 en 1)
MAX_SCALE_DOWN_STEP = 1
