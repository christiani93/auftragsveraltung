"""Gunicorn-Config fuer Production-Deployment auf HostPoint FlexServer.

Aufruf:
    gunicorn -c deploy/gunicorn.conf.py "app:create_app()"
"""
import multiprocessing
import os

# Bind nur auf localhost — HostPoint macht Reverse-Proxy mit SSL davor
bind = os.environ.get("AUFTRAGSVERWALTUNG_BIND", "127.0.0.1:8815")

# Worker-Anzahl: bei kleinem Server 2-3 reichen
workers = int(os.environ.get("AUFTRAGSVERWALTUNG_WORKERS",
                              min(4, multiprocessing.cpu_count() * 2 + 1)))

# Threads pro Worker fuer parallele I/O (WeasyPrint blockiert mal kurz)
threads = 2
worker_class = "gthread"

# Sicherheit + Stabilitaet
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100

# Logging
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")
loglevel = "info"
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s "%(f)s" %(D)sus'

# PID-File und Pfade
proc_name = "auftragsverwaltung"
