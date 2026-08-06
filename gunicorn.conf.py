# Loaded automatically by gunicorn; the start command stays `gunicorn app:app`.
#
# Threaded workers so the /admin/logs/stream SSE response can hold a thread
# without blocking every other request (the default is a single sync worker).
# workers stays at 1 on purpose: APScheduler runs inside the web process and
# a second worker would double-fire the weekly scraper.
worker_class = "gthread"
workers = 1
threads = 4
