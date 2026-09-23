"""
Generador de Gráficas de Evidencias Experimentales
Lee el archivo controller_decisions.jsonl y produce un gráfico temporal
de CPUUtilization vs Capacidad de Instancias con anotaciones de decisiones.
"""
import json
import os
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def generate_chart():
    log_path = "controller_decisions.jsonl"
    if not os.path.exists(log_path):
        print(f"Error: Archivo {log_path} no encontrado.")
        return

    timestamps = []
    cpus = []
    capacities = []
    decisions = []
    actions = []

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                ts = datetime.fromisoformat(record["timestamp"])
                cpu = record["metrics"].get("CPUUtilization", 0.0)
                cap = record.get("existing_capacity", 1)
                dec = record.get("decision", "MAINTAIN_CAPACITY")
                act = record.get("action_requested", "")

                timestamps.append(ts)
                cpus.append(cpu)
                capacities.append(cap)
                decisions.append(dec)
                actions.append(act)
            except Exception as e:
                continue

    if not timestamps:
        print("No se encontraron registros válidos en el log.")
        return

    # Usar estilo limpio y profesional
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
    fig, ax1 = plt.subplots(figsize=(14, 7), dpi=300)

    # Eje 1: Utilización de CPU
    color_cpu = "#1f77b4"
    ax1.set_xlabel("Tiempo (UTC)", fontsize=12, fontweight="bold", labelpad=10)
    ax1.set_ylabel("Utilización de CPU (%)", color=color_cpu, fontsize=12, fontweight="bold")
    line1 = ax1.plot(timestamps, cpus, color=color_cpu, marker="o", linewidth=2.2, markersize=4, label="CPU Observada (%)")
    ax1.tick_params(axis="y", labelcolor=color_cpu)
    ax1.set_ylim(0, max(max(cpus) * 1.15, 80))

    # Líneas de umbral (Target 40%, Banda superior 44%, Banda inferior 36%)
    ax1.axhline(40.0, color="#2ca02c", linestyle="--", linewidth=1.5, alpha=0.8, label="CPU Target (40%)")
    ax1.axhline(44.0, color="#d62728", linestyle=":", linewidth=1.5, alpha=0.8, label="Umbral Superior Scale-Out (44%)")
    ax1.axhline(36.0, color="#ff7f0e", linestyle=":", linewidth=1.5, alpha=0.8, label="Umbral Inferior Scale-In (36%)")

    # Eje 2: Capacidad de Instancias
    ax2 = ax1.twinx()
    color_cap = "#2ca02c"
    ax2.set_ylabel("Capacidad de Instancias (EC2)", color=color_cap, fontsize=12, fontweight="bold")
    line2 = ax2.step(timestamps, capacities, color=color_cap, where="post", linewidth=2.5, label="Instancias en Clúster")
    ax2.tick_params(axis="y", labelcolor=color_cap)
    ax2.set_ylim(0, 6)
    ax2.set_yticks([1, 2, 3, 4, 5])

    # Anotaciones de decisiones clave (Scale-Out y Scale-In)
    for i, (ts, cpu, cap, dec, act) in enumerate(zip(timestamps, cpus, capacities, decisions, actions)):
        if dec == "INCREASE_CAPACITY":
            ax1.annotate(
                f"▲ Scale-Out\n({cpu:.1f}% CPU)",
                xy=(ts, cpu),
                xytext=(0, 25),
                textcoords="offset points",
                ha="center",
                fontsize=9,
                fontweight="bold",
                color="#b30000",
                arrowprops=dict(arrowstyle="->", color="#b30000", lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#ffe6e6", edgecolor="#b30000", alpha=0.9)
            )
        elif dec == "REDUCE_CAPACITY":
            ax1.annotate(
                f"▼ Scale-In\n(Estabilizado)",
                xy=(ts, cpu),
                xytext=(0, 30),
                textcoords="offset points",
                ha="center",
                fontsize=9,
                fontweight="bold",
                color="#006600",
                arrowprops=dict(arrowstyle="->", color="#006600", lw=1.5),
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#e6ffe6", edgecolor="#006600", alpha=0.9)
            )

    # Formateo de fecha y hora en el eje X
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    fig.autofmt_xdate()

    # Título y leyenda
    plt.title("Evidencia Experimental: Comportamiento Elástico del Auto-Scaling Controller (SI3016)\nControl Autónomo de Capacidad basado en Kubernetes HPA", fontsize=14, fontweight="bold", pad=15)
    
    # Combinar leyendas de ambos ejes
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", frameon=True, facecolor="white", edgecolor="#cccccc")

    plt.tight_layout()
    output_img = "evidencia_escalado.png"
    plt.savefig(output_img, dpi=300)
    plt.close()
    print(f"Gráfica generada exitosamente en: {output_img}")

if __name__ == "__main__":
    generate_chart()
