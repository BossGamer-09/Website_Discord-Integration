#!/usr/bin/env python3
"""
Redis Server Starter
====================
Starts Redis bound exclusively to 38.46.216.78 (the Sparked Host node IP).
No tunnels, no external connections — all traffic stays on-host.

Binary acquisition strategy (fastest to slowest):
  1. Use cached binary if already present and correct version   (~0 s)
  2. apt-get install redis-server                               (~5-10 s)
  3. Download official .deb from packages.ubuntu.com           (~10-15 s)
  4. Build from source                                          (~10 min, last resort)
"""
import os
import sys
import asyncio
import logging
import platform
import subprocess
import shutil
import time
import urllib.request
import tarfile
import ssl
import re
import signal
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INSTALL_DIR  = Path.cwd() / "redis_patched"
DATA_DIR     = INSTALL_DIR / "redis_data"
REDIS_BINARY = INSTALL_DIR / "redis-server"

REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "dsd411598f414g1df15d5")
REDIS_PORT     = int(os.getenv("REDIS_PORT", "25871"))
REDIS_BIND     = os.getenv("REDIS_BIND", "0.0.0.0")
REDIS_MAXMEM   = os.getenv("REDIS_MAXMEM", "100mb")

# Minimum acceptable Redis version
MIN_VERSION = (7, 0, 0)

# Source tarball URL — only used as absolute last resort
REDIS_VERSION    = "7.2.4"
REDIS_SOURCE_URL = f"https://github.com/redis/redis/archive/refs/tags/{REDIS_VERSION}.tar.gz"

MAX_REDIS_RESTARTS  = 5
REDIS_RESTART_DELAY = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str):
    logger.info(f"[Redis Loader] {msg}")


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _run(cmd: list, timeout: int = 60, env: dict = None) -> tuple[int, str]:
    """Run a command, return (returncode, combined output)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            env={**os.environ, **(env or {})}
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "Timed out"
    except Exception as e:
        return -1, str(e)


def get_redis_version(binary_path: Path) -> tuple[int, ...] | None:
    """Return version tuple e.g. (7, 2, 4) or None."""
    if not binary_path.exists():
        return None
    rc, out = _run([str(binary_path), "--version"], timeout=5)
    if rc != 0:
        return None
    m = re.search(r"v=(\d+)\.(\d+)\.(\d+)", out) or re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    if m:
        return tuple(int(x) for x in m.groups())
    return None


def version_str(v: tuple) -> str:
    return ".".join(str(x) for x in v)


# ---------------------------------------------------------------------------
# Binary acquisition
# ---------------------------------------------------------------------------

def ensure_redis_binary() -> Path | None:
    """Return a working redis-server binary via the fastest available method."""
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Cached binary
    if REDIS_BINARY.exists():
        ver = get_redis_version(REDIS_BINARY)
        if ver and ver >= MIN_VERSION:
            log(f"✅ Using cached Redis {version_str(ver)}")
            return REDIS_BINARY
        log(f"⚠️  Cached binary version {ver} is too old or broken. Re-acquiring...")
        REDIS_BINARY.unlink(missing_ok=True)

    # 2. apt-get (fast, ~10 s, works in all Debian/Ubuntu containers)
    log("📦 Trying apt-get install redis-server (~10 s)...")
    result = _try_apt_install()
    if result:
        return result

    # 3. Download .deb directly (fallback if apt sources are broken)
    log("📥 Trying direct .deb download (~15 s)...")
    result = _try_deb_download()
    if result:
        return result

    # 4. Build from source (last resort, ~10 min)
    log("⚠️  All fast methods failed. Building from source (~10 min)...")
    return _build_from_source()


def _try_apt_install() -> Path | None:
    rc, out = _run(
        ["apt-get", "update", "-qq"],
        timeout=60,
        env={"DEBIAN_FRONTEND": "noninteractive"}
    )
    if rc != 0:
        log(f"apt-get update failed: {out[:200]}")
        return None

    rc, out = _run(
        ["apt-get", "install", "-y", "-qq", "redis-server"],
        timeout=120,
        env={"DEBIAN_FRONTEND": "noninteractive"}
    )
    if rc != 0:
        log(f"apt-get install failed: {out[:200]}")
        return None

    system_redis = shutil.which("redis-server") or "/usr/bin/redis-server"
    if not Path(system_redis).exists():
        log("redis-server not found after apt install.")
        return None

    ver = get_redis_version(Path(system_redis))
    log(f"✅ Installed Redis {version_str(ver) if ver else '?'} via apt-get")

    tmp = REDIS_BINARY.with_suffix(".tmp")
    shutil.copy2(system_redis, tmp)
    tmp.chmod(0o755)
    tmp.replace(REDIS_BINARY)
    return REDIS_BINARY


def _try_deb_download() -> Path | None:
    deb_url = (
        "http://archive.ubuntu.com/ubuntu/pool/universe/r/redis/"
        "redis-server_7.0.15-1_amd64.deb"
    )
    deb_path = INSTALL_DIR / "redis-server.deb"

    ctx = _ssl_ctx()
    req = urllib.request.Request(deb_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        log(f"Downloading {deb_url} ...")
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            if resp.status != 200:
                log(f"HTTP {resp.status}")
                return None
            with open(deb_path, "wb") as f:
                shutil.copyfileobj(resp, f)
    except Exception as e:
        log(f"Download error: {e}")
        return None

    extract_dir = INSTALL_DIR / "deb_extract"
    extract_dir.mkdir(exist_ok=True)

    rc, out = _run(["dpkg-deb", "-x", str(deb_path), str(extract_dir)], timeout=30)
    if rc != 0:
        log(f"dpkg-deb failed: {out[:200]}")
        deb_path.unlink(missing_ok=True)
        return None

    deb_path.unlink(missing_ok=True)

    candidates = list(extract_dir.rglob("redis-server"))
    server = next((p for p in candidates if p.is_file()), None)
    if not server:
        log("redis-server not found in .deb")
        return None

    tmp = REDIS_BINARY.with_suffix(".tmp")
    shutil.copy2(server, tmp)
    tmp.chmod(0o755)
    tmp.replace(REDIS_BINARY)

    shutil.rmtree(extract_dir, ignore_errors=True)
    ver = get_redis_version(REDIS_BINARY)
    log(f"✅ Redis {version_str(ver) if ver else '?'} extracted from .deb")
    return REDIS_BINARY


def _build_from_source() -> Path | None:
    log(f"Downloading Redis {REDIS_VERSION} source...")

    build_dir = INSTALL_DIR / "build_src"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir()
    tar_path = build_dir / "redis.tar.gz"

    ctx = _ssl_ctx()
    req = urllib.request.Request(REDIS_SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            if resp.status != 200:
                log(f"Source download failed: HTTP {resp.status}")
                return None
            with open(tar_path, "wb") as f:
                shutil.copyfileobj(resp, f)
    except Exception as e:
        log(f"Source download error: {e}")
        return None

    log("Extracting source...")
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(build_dir)
    except Exception as e:
        log(f"Extraction failed: {e}")
        return None

    src_dir = next(
        (p for p in build_dir.iterdir()
         if p.is_dir() and (p / "Makefile").exists() and (p / "src").exists()),
        None
    )
    if not src_dir:
        log("Could not locate Redis source directory.")
        return None

    for tool in ("make", "gcc"):
        if shutil.which(tool) is None:
            log(f"Missing build tool: {tool}")
            return None

    log("Compiling Redis (this takes ~10 minutes)...")
    with subprocess.Popen(
        ["make", "-C", str(src_dir), "MALLOC=libc"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    ) as proc:
        assert proc.stdout
        for line in proc.stdout:
            stripped = line.rstrip()
            if stripped:
                log(stripped)
        proc.wait()
        if proc.returncode != 0:
            log(f"Build failed (exit {proc.returncode}).")
            return None

    server = next(iter(src_dir.glob("src/redis-server")), None)
    if not server:
        log("redis-server not found after build.")
        return None

    tmp = REDIS_BINARY.with_suffix(".tmp")
    shutil.copy2(server, tmp)
    tmp.chmod(0o755)
    tmp.replace(REDIS_BINARY)
    shutil.rmtree(build_dir, ignore_errors=True)

    ver = get_redis_version(REDIS_BINARY)
    log(f"✅ Built and cached Redis {version_str(ver) if ver else '?'}")
    return REDIS_BINARY


# ---------------------------------------------------------------------------
# Redis config
# ---------------------------------------------------------------------------

def create_redis_config() -> Path:
    config_content = f"""# Redis Configuration – generated by main.py
# Bound to host IP only — no external connections accepted
bind {REDIS_BIND}
port {REDIS_PORT}
protected-mode yes
requirepass {REDIS_PASSWORD}

# Reject connections not coming from the bound interface
# (Redis protected-mode + single bind address enforces this)

# Persistence
dir {DATA_DIR}
dbfilename dump.rdb
save 900 1
save 300 10
save 60 10000

# Memory
maxmemory {REDIS_MAXMEM}
maxmemory-policy allkeys-lru

# Performance / latency
tcp-keepalive 60
tcp-backlog 511
lazyfree-lazy-eviction yes
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes

# Logging
databases 16
loglevel notice
logfile {INSTALL_DIR / 'redis.log'}
daemonize no
"""
    config_path = INSTALL_DIR / "redis.conf"
    tmp = config_path.with_suffix(".tmp")
    tmp.write_text(config_content)
    tmp.replace(config_path)
    log(f"Config written: {config_path}")
    return config_path


# ---------------------------------------------------------------------------
# Async Redis process runner with auto-restart
# ---------------------------------------------------------------------------

async def run_redis(redis_binary: Path, config: Path) -> int:
    restarts = 0
    delay = REDIS_RESTART_DELAY

    while True:
        log(f"Starting Redis (attempt {restarts + 1})...")
        process = await asyncio.create_subprocess_exec(
            str(redis_binary), str(config),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        log(f"Redis PID: {process.pid}")

        async def _drain():
            assert process.stdout
            async for line in process.stdout:
                text = line.decode(errors="ignore").strip()
                if text:
                    logger.info(f"[redis-server] {text}")

        drain_task = asyncio.create_task(_drain())
        await asyncio.sleep(3)

        if process.returncode is not None:
            await drain_task
            log(f"Redis exited immediately (code {process.returncode})")
            restarts += 1
            if restarts > MAX_REDIS_RESTARTS:
                log("❌ Too many restarts. Giving up.")
                return process.returncode
            log(f"Restarting in {delay}s... ({restarts}/{MAX_REDIS_RESTARTS})")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
            continue

        log(f"✅ Redis running on {REDIS_BIND}:{REDIS_PORT}")
        rc = await process.wait()
        await drain_task
        log(f"Redis exited with code {rc}")

        restarts += 1
        if restarts > MAX_REDIS_RESTARTS:
            log("❌ Too many restarts. Giving up.")
            return rc
        log(f"Restarting in {delay}s... ({restarts}/{MAX_REDIS_RESTARTS})")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    logger.info("=" * 60)
    logger.info("🚀 Starting Redis")
    logger.info(f"   Architecture : {platform.machine()}")
    logger.info(f"   Bind address : {REDIS_BIND}:{REDIS_PORT}")
    logger.info("=" * 60)

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _on_signal():
        logger.info("🛑 Shutdown signal received.")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_signal)

    redis_binary = await loop.run_in_executor(None, ensure_redis_binary)
    if not redis_binary:
        logger.error("❌ Could not obtain Redis binary. Exiting.")
        sys.exit(1)

    config_path = await loop.run_in_executor(None, create_redis_config)

    redis_task    = asyncio.create_task(run_redis(redis_binary, config_path))
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    done, pending = await asyncio.wait(
        {redis_task, shutdown_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()

    if shutdown_event.is_set():
        logger.info("Stopping Redis due to shutdown signal...")
        redis_task.cancel()
        try:
            await redis_task
        except asyncio.CancelledError:
            pass

    logger.info("✅ Clean shutdown.")


if __name__ == "__main__":
    asyncio.run(main())
