import sys
import logging
import asyncio
import os
import platform
import ssl
import urllib.request
from pathlib import Path

from django.conf import settings
from asgiref.sync import sync_to_async

from app.main.asgi import application  # sets up django implicitly

try:
    import uvloop
    event_loop = uvloop
except ImportError:
    event_loop = asyncio

if settings.DEBUG:
    logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal website-only Cloudflare tunnel
# ---------------------------------------------------------------------------

_CF_BINARY  = Path.cwd() / 'cloudflared'
_CF_CONFIG  = Path.cwd() / 'cloudflared_website.yaml'


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _ensure_cloudflared() -> bool:
    if _CF_BINARY.exists():
        return True
    machine = platform.machine().lower()
    if 'x86_64' in machine or 'amd64' in machine:
        url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64'
    elif 'aarch64' in machine or 'arm64' in machine:
        url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64'
    else:
        url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-386'

    logger.info(f"Downloading cloudflared from {url}...")
    req = urllib.request.Request(url, headers={'User-Agent': 'cloudflared-manager/1.0'})
    try:
        with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=60) as resp:
            if resp.status != 200:
                logger.error(f"Download failed: HTTP {resp.status}")
                return False
            tmp = _CF_BINARY.with_suffix('.tmp')
            tmp.write_bytes(resp.read())
            tmp.chmod(0o755)
            tmp.rename(_CF_BINARY)
        logger.info("cloudflared downloaded.")
        return True
    except Exception as e:
        logger.error(f"cloudflared download error: {e}")
        return False


def _write_cf_config():
    config = f"""loglevel: info
transport-loglevel: warn

ingress:
  - hostname: blightveil.org
    service: http://127.0.0.1:{settings.DAPHNE_PORT}
    originRequest:
      http2Origin: true
      keepAliveConnections: 100
      keepAliveTimeout: 90s
      connectTimeout: 10s
      noTLSVerify: true
  - hostname: www.blightveil.org
    service: http://127.0.0.1:{settings.DAPHNE_PORT}
    originRequest:
      http2Origin: true
      keepAliveConnections: 100
      keepAliveTimeout: 90s
      connectTimeout: 10s
      noTLSVerify: true
  - service: http_status:404
"""
    tmp = _CF_CONFIG.with_suffix('.tmp')
    tmp.write_text(config)
    tmp.replace(_CF_CONFIG)


async def _pipe_reader(stream, prefix):
    try:
        async for line in stream:
            text = line.decode(errors='ignore').strip()
            if text:
                logger.info(f"{prefix} {text}")
    except Exception:
        pass


_tunnel_stop = asyncio.Event()


async def _spawn_cloudflared(token: str) -> asyncio.subprocess.Process | None:
    env = os.environ.copy()
    env['TUNNEL_TOKEN'] = token
    proc = await asyncio.create_subprocess_exec(
        str(_CF_BINARY),
        '--edge-ip-version', 'auto',
        'tunnel', '--config', str(_CF_CONFIG), 'run',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        env=env,
    )
    asyncio.create_task(_pipe_reader(proc.stdout, '[cloudflared]'))
    asyncio.create_task(_pipe_reader(proc.stderr, '[cloudflared]'))
    await asyncio.sleep(3)
    if proc.returncode is not None:
        logger.error(f"❌ cloudflared exited immediately (rc={proc.returncode})")
        return None
    logger.info(f"✅ Website tunnel running (PID {proc.pid}) → blightveil.org")
    return proc


async def run_website_tunnel():
    """
    Runs cloudflared with auto-restart.
    When cloudflared self-updates it exits cleanly — we restart it so the
    new binary takes over. Exponential back-off on rapid failures.
    """
    global _tunnel_stop
    _tunnel_stop = asyncio.Event()

    token = os.getenv('WEBSITE_TUNNEL_TOKEN', '').strip()
    if not token:
        logger.error("❌ WEBSITE_TUNNEL_TOKEN not set — website will not be publicly accessible.")
        return

    if not await asyncio.get_running_loop().run_in_executor(None, _ensure_cloudflared):
        logger.error("❌ Could not obtain cloudflared binary.")
        return

    await asyncio.get_running_loop().run_in_executor(None, _write_cf_config)

    delay = 5
    while not _tunnel_stop.is_set():
        proc = await _spawn_cloudflared(token)
        if proc is None:
            if _tunnel_stop.is_set():
                break
            logger.warning(f"Retrying cloudflared in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
            continue

        delay = 5  # reset back-off on successful start
        await proc.wait()

        if _tunnel_stop.is_set():
            break

        rc = proc.returncode
        if rc == 0:
            # Clean exit = self-update completed, restart immediately with new binary
            logger.info("cloudflared exited cleanly (self-update). Restarting with new binary...")
            await asyncio.sleep(1)
        else:
            logger.warning(f"cloudflared exited (rc={rc}). Restarting in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def stop_website_tunnel():
    _tunnel_stop.set()


# ---------------------------------------------------------------------------
# Daphne + management
# ---------------------------------------------------------------------------

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


async def run_daphne_server():
    logger.info("Starting Daphne ASGI Server @ {}:{}".format(settings.DAPHNE_IFACE, settings.DAPHNE_PORT))
    cmd = [
        sys.executable, "-m", "daphne",
        "-b", str(settings.DAPHNE_IFACE),
        "-p", str(settings.DAPHNE_PORT),
        "--proxy-headers",
        "app.main.asgi:application",
        "-v2",
    ]
    rc = await _stream_subprocess(
        cmd,
        lambda x: print(x.decode().strip()),
        lambda x: print(x.decode().strip()),
    )
    return rc


@sync_to_async
def run_management():
    logger.info(">> Starting django Management")
    from django.core.management import call_command

    logger.info(">> Running Migrations")
    call_command('migrate', interactive=False, verbosity=2)

    logger.info(">> Compress Static")
    call_command('compress', force=True)

    logger.info(">> Collect Static")
    call_command('collectstatic', ignore=['*.scss'], interactive=False, verbosity=2)

    logger.info(">> Create Superusers")
    call_command('createsu')

    logger.info(">> Create Preferences")
    call_command('createpref')

    logger.info(">> Create Celery Scheduled Tasks")
    call_command('createsched')


async def main():
    await run_management()

    tunnel_task = asyncio.create_task(run_website_tunnel())

    try:
        rc = await run_daphne_server()
        print(f"Daphne exited with return code: {rc}")
    finally:
        await stop_website_tunnel()
        tunnel_task.cancel()
        try:
            await tunnel_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        event_loop.run(main())
    except KeyboardInterrupt:
        logger.info("Graceful shut down")
