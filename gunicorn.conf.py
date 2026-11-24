# Gunicorn configuration file for Render Cloud Deployment
# Automatically read by Gunicorn when running 'gunicorn app:app'
import os
import gc
import ctypes

# Set glibc allocator environment variables to prevent multi-arena fragmentation on Linux
os.environ["MALLOC_ARENA_MAX"] = "2"
os.environ["MALLOC_TRIM_THRESHOLD_"] = "131072"

# Render dynamic port binding
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Strictly 1 worker to prevent concurrent worker memory multiplication
workers = 1

# 4 lightweight threads to handle concurrent UI polls efficiently
threads = 4
worker_class = "gthread"

# Extended timeout for Kotak Neo API network calls
timeout = 120

# CRITICAL FIX FOR 512MB LIMIT:
# NEVER recycle workers automatically with max_requests > 0!
# In Gunicorn, worker recycling spawns a replacement worker process CONCURRENTLY before killing the old worker.
# Since 1 worker takes ~220 MB, two workers at once consume ~450-500 MB, which breaches Render's 512 MB ceiling!
# Instead, keep max_requests = 0 (infinite) and rely on periodic gc.collect() and malloc_trim(0).
max_requests = 0
max_requests_jitter = 0

def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s), launching autonomous background trading daemons...", worker.pid)
    try:
        from app import engine
        engine.start_background_workers()
        server.log.info("Autonomous trading daemons successfully started in worker %s", worker.pid)
    except Exception as e:
        server.log.error("Failed to start background daemons in post_fork: %s", e)
