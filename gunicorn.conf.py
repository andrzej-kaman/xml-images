import threading

# Pobieramy funkcję workera i stałą z naszej głównej aplikacji
from app import generation_worker, NUM_WORKERS

def post_fork(server, worker):
    """
    Hook (zaczep) wykonywany po utworzeniu procesu roboczego przez Gunicorn.
    To jest prawidłowe miejsce do uruchamiania wątków tła.
    """
    server.log.info(f"Worker (PID: {worker.pid}) został utworzony.")

    # Uruchomienie wątków-workerów dla kolejki zadań w każdym procesie Gunicorna
    for i in range(NUM_WORKERS):
        worker_thread = threading.Thread(target=generation_worker, daemon=True)
        worker_thread.start()
        server.log.info(f"Uruchomiono wątek workera kolejki {i+1}/{NUM_WORKERS} w procesie PID: {worker.pid}")
