"""Run locally on 127.0.0.1:8001. Never include this service in deployment."""

from ytu_scheduler.main import create_app

app = create_app(local_owner=True)
