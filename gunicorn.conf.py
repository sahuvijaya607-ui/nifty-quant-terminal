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
