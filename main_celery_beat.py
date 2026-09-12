import sys
import logging
import asyncio

from app.main.asgi import application  # sets up django implicitly

try:
    import uvloop
    event_loop = uvloop
except ImportError:
    event_loop = asyncio

from django.conf import settings

if settings.DEBUG:
    logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger(__name__)


async def _read_stream(stream, cb):
    while True:
        line = await stream.readline()
        if line:
            cb(line)
        else:
            break


async def _stream_subprocess(cmd, stdout_cb, stderr_cb):
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.gather(
        _read_stream(process.stdout, stdout_cb),
        _read_stream(process.stderr, stderr_cb),
    )
    return await process.wait()


async def run_celery_beat():
    logger.info("Starting Celery Beat")
    cmd = [
        sys.executable, "-m", "celery",
        "-A", "app.main",
        "beat",
        "--loglevel=info",
        "--scheduler", "django_celery_beat.schedulers:DatabaseScheduler",
    ]
    rc = await _stream_subprocess(
        cmd,
        lambda x: print(x.decode().strip()),
        lambda x: print(x.decode().strip()),
    )
    return rc


async def main():
    rc = await run_celery_beat()
    print(f"Celery Beat exited with return code: {rc}")


if __name__ == "__main__":
    try:
        event_loop.run(main())
    except KeyboardInterrupt:
        logger.info("Graceful shut down")
