# Gunicorn configuration file for Render Cloud Deployment
# Automatically read by Gunicorn when running 'gunicorn app:app'
import os

# Render dynamic port binding
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Strictly 1 worker to keep memory usage under 100 MB (Render 512MB limit)
workers = 1

# 4 lightweight threads to handle concurrent UI polls efficiently
threads = 4
worker_class = "gthread"

# Extended timeout for Kotak Neo API network calls
timeout = 120

# Automatic worker recycling to eliminate Python memory fragmentation
max_requests = 1000
max_requests_jitter = 100

def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s), launching autonomous background trading daemons...", worker.pid)
    try:
        from app import engine
        engine.start_background_workers()
        server.log.info("Autonomous trading daemons successfully started in worker %s", worker.pid)
    except Exception as e:
        server.log.error("Failed to start background daemons in post_fork: %s", e)

