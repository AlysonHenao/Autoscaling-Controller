"""
Aplicación Web CMS Ligera (Para la Golden AMI)
Diseñada para correr en t2.micro / t3.micro en AWS Academy sin consumir créditos en exceso.
"""
import math
import os
import socket
import time
from flask import Flask, jsonify, request

app = Flask(__name__)
HOSTNAME = socket.gethostname()

@app.route("/")
def index():
    return jsonify({
        "service": "CMS Production Node",
        "instance_host": HOSTNAME,
        "status": "online",
        "timestamp": time.time()
    })

@app.route("/health")
def health():
    """Health Check para el Application Load Balancer."""
    return jsonify({"status": "healthy", "host": HOSTNAME}), 200

@app.route("/compute-heavy")
def compute_heavy():
    """
    Simula procesamiento de CPU sin generar tráfico de red excesivo.
    Ideal para AWS Academy: consume ciclos de CPU localmente durante 'duration' segundos
    sin generar miles de peticiones hacia afuera.
    """
    duration = float(request.args.get("duration", 0.1))
    duration = min(duration, 1.0)  # Límite de seguridad
    
    start_time = time.time()
    val = 1.0001
    while (time.time() - start_time) < duration:
        val = math.sqrt(val * 1.0001) + math.sin(val)

    return jsonify({
        "status": "completed",
        "host": HOSTNAME,
        "duration_seconds": round(time.time() - start_time, 4)
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
