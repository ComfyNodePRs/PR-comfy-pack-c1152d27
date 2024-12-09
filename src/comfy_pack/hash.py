import os
import json
import asyncio
import sys
from typing import Dict, List
from datetime import datetime
import fcntl
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from .const import SHA_CACHE_FILE
import subprocess


CALC_CMD = """
import hashlib
import sys

filepath = sys.argv[1]
chunk_size = int(sys.argv[2])

sha256 = hashlib.sha256()
with open(filepath, "rb") as f:
    for chunk in iter(lambda: f.read(chunk_size), b""):
        sha256.update(chunk)
print(sha256.hexdigest())
"""


def calculate_sha256_worker(filepath: str, chunk_size: int = 4 * 1024 * 1024) -> str:
    """Calculate SHA-256 in a separate process"""
    result = subprocess.run(
        [sys.executable, "-c", CALC_CMD, filepath, str(chunk_size)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def get_sha256(filepath: str) -> str:
    return batch_get_sha256([filepath])[filepath]


def async_get_sha256(filepath: str) -> str:
    return asyncio.run(async_batch_get_sha256([filepath]))[filepath]


def batch_get_sha256(filepaths: List[str]) -> Dict[str, str]:
    return asyncio.run(async_batch_get_sha256(filepaths))


async def async_batch_get_sha256(filepaths: List[str]) -> Dict[str, str]:
    # Load cache
    cache = {}
    if SHA_CACHE_FILE.exists():
        try:
            with SHA_CACHE_FILE.open("r") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # Initialize process pool
    max_workers = max(1, (os.cpu_count() or 1))

    # Process files
    results = {}
    async with asyncio.Lock():
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            loop = asyncio.get_event_loop()

            for filepath in filepaths:
                if not os.path.exists(filepath):
                    results[filepath] = None
                    continue

                # Get file info
                stat = os.stat(filepath)
                current_size = stat.st_size
                current_time = stat.st_birthtime

                # Check cache
                cache_entry = cache.get(filepath)
                if cache_entry:
                    if (
                        cache_entry["size"] == current_size
                        and cache_entry["birthtime"] == current_time
                    ):
                        results[filepath] = cache_entry["sha256"]
                        continue

                # Calculate new SHA
                calc_func = partial(calculate_sha256_worker, filepath)
                sha256 = await loop.run_in_executor(pool, calc_func)

                # Update cache and results
                cache[filepath] = {
                    "sha256": sha256,
                    "size": current_size,
                    "birthtime": current_time,
                    "last_verified": datetime.now().isoformat(),
                }
                results[filepath] = sha256

    # Save cache
    try:
        with SHA_CACHE_FILE.open("w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(cache, f, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (IOError, OSError):
        pass

    return results
