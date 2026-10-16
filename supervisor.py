#!/usr/bin/env python3
"""
JARVIS Process Supervisor & Watchdog
Monitors jarvis.py, logs crashes to logs/supervisor.log,
and automatically restarts the assistant if it ever terminates unexpectedly.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, "supervisor.log")
handler = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
handler.setFormatter(formatter)

logger = logging.getLogger("jarvis_supervisor")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(handler)
    if sys.stdout is not None:
        logger.addHandler(logging.StreamHandler(sys.stdout))

is_gui_mode = "pythonw" in sys.executable.lower()
if is_gui_mode:
    VENV_PYTHON = os.path.join(BASE_DIR, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(VENV_PYTHON):
        VENV_PYTHON = os.path.join(BASE_DIR, "venv", "Scripts", "pythonw.exe")
else:
    VENV_PYTHON = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
    if not os.path.exists(VENV_PYTHON):
        VENV_PYTHON = os.path.join(BASE_DIR, "venv", "Scripts", "python.exe")

if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = sys.executable

JARVIS_SCRIPT = os.path.join(BASE_DIR, "jarvis.py")
JARVIS_ARGS = sys.argv[1:]


def run_supervisor() -> None:
    logger.info("=== JARVIS Supervisor Started ===")
    logger.info(f"Target Script: {JARVIS_SCRIPT}")
    logger.info(f"Python Executable: {VENV_PYTHON} (GUI mode: {is_gui_mode})")

    crash_times: list[float] = []

    while True:
        logger.info("Launching JARVIS instance with faulthandler...")
        cmd = [VENV_PYTHON, "-X", "faulthandler", "-u", JARVIS_SCRIPT, *JARVIS_ARGS]
        start_time = time.time()

        try:
            kwargs = {}
            if is_gui_mode and hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            stdout_path = os.path.join(LOG_DIR, "jarvis_stdout.log")
            stderr_path = os.path.join(LOG_DIR, "jarvis_stderr.log")

            with open(stdout_path, "a", encoding="utf-8", errors="replace") as out_f, \
                 open(stderr_path, "a", encoding="utf-8", errors="replace") as err_f:
                process = subprocess.Popen(
                    cmd,
                    cwd=BASE_DIR,
                    stdout=out_f,
                    stderr=err_f,
                    **kwargs
                )
                exit_code = process.wait()
            duration = time.time() - start_time

            logger.warning(
                f"JARVIS process exited with code {exit_code} after {duration:.1f}s"
            )

            # Clean exit via SystemExit / user quit
            if exit_code == 0:
                logger.info("JARVIS shut down normally. Supervisor exiting.")
                break

            # If user terminated supervisor via Ctrl+C
            if exit_code in (-2, 130, 3221225786):  # Windows STATUS_CONTROL_C_EXIT
                logger.info("Received termination signal. Stopping supervisor.")
                break

            # Handle unexpected crash
            now = time.time()
            crash_times.append(now)
            # Retain crashes in the last 60 seconds
            crash_times = [t for t in crash_times if now - t < 60]

            if len(crash_times) >= 5:
                logger.error("Rapid crash loop detected (>5 crashes in 60s). Pausing 10s before retry...")
                time.sleep(10)
            else:
                logger.info("Restarting JARVIS in 1.5 seconds...")
                time.sleep(1.5)

        except KeyboardInterrupt:
            logger.info("Supervisor stopped by user (Ctrl+C). Terminating child...")
            try:
                process.terminate()
            except Exception:
                pass
            break
        except Exception as e:
            logger.error(f"Supervisor encountered unexpected error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    if not Path(JARVIS_SCRIPT).is_file():
        logger.error("JARVIS entrypoint was not found: %s", JARVIS_SCRIPT)
        raise SystemExit(2)
    run_supervisor()
