"""
Generador de Carga Seguro para AWS Academy (Lightweight Load Tester)
Diseñado específicamente para NO exceder los límites ni activar alarmas de abuso en AWS Academy:
- Usa concurrencia controlada (hilos continuos).
- Solicita al endpoint /compute-heavy cálculos moderados que elevan la CPU de una t2.micro/t3.micro.
- Permite evidenciar claramente las transiciones:
    1. Carga sostenida -> INCREASE_CAPACITY
    2. Enfriamiento (Cooldown) -> MAINTAIN_CAPACITY
    3. Cese de carga y estabilización -> REDUCE_CAPACITY
"""
import argparse
import concurrent.futures
import time
import urllib.request
import sys

def make_request(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AcademySafeTester/1.0"})
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status == 200
    except Exception:
        return False

def worker_task(endpoint: str, end_time: float) -> int:
    success = 0
    while time.time() < end_time:
        if make_request(endpoint):
            success += 1
        time.sleep(0.01)
    return success

def generate_safe_cpu_stress(alb_url: str, duration_minutes: int = 3, concurrency: int = 10, compute_duration: float = 0.8):
    endpoint = f"{alb_url.rstrip('/')}/compute-heavy?duration={compute_duration}"
    print(f"\n[AWS Academy Safe Test] Iniciando estrés continuo hacia: {endpoint}")
    print(f"Duración: {duration_minutes} minutos | Concurrencia continua: {concurrency} hilos")
    print("Manteniendo peticiones continuas para saturar los núcleos y superar el 66% de CPU de forma segura.\n")

    start_time = time.time()
    end_time = start_time + (duration_minutes * 60)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker_task, endpoint, end_time) for _ in range(concurrency)]
        
        while time.time() < end_time:
            elapsed = int(time.time() - start_time)
            remaining = max(0, int(end_time - time.time()))
            print(f"\r  Progreso: {elapsed}s transcurridos | Restantes: {remaining}s...", end="")
            time.sleep(1)

        total_success = sum(f.result() for f in concurrent.futures.as_completed(futures))

    print(f"\n\n[Fase de Estrés Finalizada] Total peticiones exitosas: {total_success}")
    print("Ahora detén el tráfico y observa en los logs del Controller cómo evalúa la ventana de estabilización.")

def main():
    parser = argparse.ArgumentParser(description="Prueba de Carga Segura para AWS Academy")
    parser.add_argument("--alb-url", type=str, required=True, help="URL o DNS del ALB (ej. http://alb-cms-xxxx.us-east-1.elb.amazonaws.com)")
    parser.add_argument("--duration-min", type=int, default=3, help="Minutos de carga sostenida (default: 3)")
    parser.add_argument("--concurrency", type=int, default=10, help="Hilos concurrentes para saturar CPU (default: 10)")
    parser.add_argument("--calc-duration", type=float, default=0.8, help="Duración del cálculo en segundos por petición (default: 0.8)")
    args = parser.parse_args()

    url = args.alb_url
    if not url.startswith("http"):
        url = f"http://{url}"

    generate_safe_cpu_stress(url, duration_minutes=args.duration_min, concurrency=args.concurrency, compute_duration=args.calc_duration)

if __name__ == "__main__":
    main()
