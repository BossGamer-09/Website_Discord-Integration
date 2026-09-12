import sys
import logging
import asyncio
import os

from django.conf import settings
from asgiref.sync import async_to_sync, sync_to_async

from app.tunnel import create_website_tunnel, create_redis_client_tunnel

from app.main.asgi import application  # sets up django implicitly


try:
    import uvloop
    event_loop = uvloop
except ImportError:
    event_loop = asyncio

if settings.DEBUG:
    logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger(__name__)


async def tunnel_service():
    logger.info("Initialising tunnels...")
    try:
        redis_tunnel = None
        token = os.getenv('WEBSITE_REDIS_TOKEN')
        if token:
            # create_redis_client_tunnel returns a RedisClientTunnel instance (not async)
            redis_tunnel = create_redis_client_tunnel(token)
            if redis_tunnel and await redis_tunnel.initialize():
                await redis_tunnel.start()
                logger.info("✅ Redis client tunnel started for website.")

        website_tunnel = await create_website_tunnel()
        if await website_tunnel.initialize():
            await website_tunnel.start()
            logger.info("✅ Website tunnel started.")

        return (website_tunnel, redis_tunnel)
    except Exception as e:
        logger.exception(f"Background service failed: {e}")
        return (None, None)


async def _read_stream(stream, cb):
    while True:
        line = await stream.readline()
        if line:
            cb(line)
        else:
            break

async def _stream_subprocess(cmd, stdout_cb, stderr_cb):  
    process = await asyncio.create_subprocess_exec(*cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    await asyncio.gather(
        _read_stream(process.stdout, stdout_cb),
        _read_stream(process.stderr, stderr_cb)
    )
    return await process.wait()


async def run_daphne_server():
    logger.info("Starting Daphne ASGI Server @ {}:{}".format(settings.DAPHNE_IFACE, settings.DAPHNE_PORT))

    cmd = [sys.executable, "-m", "daphne", "-b", str(settings.DAPHNE_IFACE), "-p", str(settings.DAPHNE_PORT), "--proxy-headers", "app.main.asgi:application", "-v2"]

    rc = await _stream_subprocess(cmd, lambda x: print(x.decode().strip()), lambda x: print(x.decode().strip()))
    return rc


async def main():
    async with asyncio.TaskGroup() as tg:
        tunnel_task = tg.create_task(tunnel_service())
        daphne_task = tg.create_task(run_daphne_server())

    print(f"Daphne exited with return code: {daphne_task.result()} [{tunnel_task.result()}]")


if __name__ == "__main__":
    try:
        event_loop.run(main())
    except KeyboardInterrupt:
        logger.info("Graceful shut down")
