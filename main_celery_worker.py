import logging

from gevent import monkey
monkey.patch_all()

logging.basicConfig(level=logging.INFO)

from celery.apps.worker import Worker
from app.main.celery import app

Worker(
    app=app,
    loglevel='INFO',
    pool_cls='gevent',
    concurrency=200,
).start()
