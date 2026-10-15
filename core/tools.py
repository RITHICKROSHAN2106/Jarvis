"""
JARVIS Tool Execution & Safety Architecture (Phase 4)
Manages Tier 1 (Safe), Tier 2 (Destructive / Confirmation), and Tier 3 (Blocked).
Integrates Recycle Bin deletions via send2trash, audit logging in logs/actions.log,
and an undo stack for reversible operations.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import shutil
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Callable

import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# ----------------- Action Audit Logger -----------------
AUDIT_LOG_FILE = os.path.join(LOGS_DIR, "actions.log")
audit_logger = logging.getLogger("jarvis_actions")
audit_logger.setLevel(logging.INFO)
if not audit_logger.handlers:
    _handler = RotatingFileHandler(
        AUDIT_LOG_FILE, maxBytes=3 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _formatter = logging.Formatter("[%(asctime)s] %(message)s")
    _handler.setFormatter(_formatter)
    audit_logger.addHandler(_handler)


class SafetyManager:
    """Evaluates commands and tools against safety tiers."""

    def __init__(self, config_path: str = CONFIG_PATH) -> None:
        self.config_path = config_path
        self.config = self._load_config()
        self.undo_stack: list[dict[str, Any]] = []

    def _load_config(self) -> dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                print(f"[!] Warning: failed to parse {self.config_path}: {e}")
        return {
            "safety": {
                "confirmation_timeout_seconds": 5.0,
                "tier1_safe": ["open_app", "open_url", "read_file", "list_dir", "get_system_stats"],
                "tier2_confirm": ["delete_file", "close_app", "kill_process", "shutdown", "write_file"],
                "tier3_blocked": ["format_drive", "delete_system_dir", "mass_delete"],
            },
            "blocked_paths": ["C:\\Windows", "C:\\Program Files"],
            "blocked_command_patterns": [r"(?i)rmdir\s+/[sS]", r"(?i)del\s+/[fF]", r"(?i)format\s+[a-zA-Z]:"],
        }

    def get_tier(self, action_name: str) -> int:
        safety = self.config.get("safety", {})
        tier3 = safety.get("tier3_blocked", [])
        if action_name in tier3:
            return 3
        tier2 = safety.get("tier2_confirm", [])
        if action_name in tier2:
            return 2
        return 1

    def is_path_blocked(self, target_path: str) -> bool:
        norm = os.path.normcase(os.path.abspath(target_path))
        for b in self.config.get("blocked_paths", []):
            b_norm = os.path.normcase(os.path.abspath(b))
            if norm == b_norm or norm.startswith(b_norm + os.sep):
                return True
        return False

    def is_command_blocked(self, command: str) -> bool:
        for pat in self.config.get("blocked_command_patterns", []):
            if re.search(pat, command):
                return True
        return False

    def log_action(
        self, action: str, tier: int, args: dict[str, Any], status: str, details: str = ""
    ) -> None:
        """Write audit entry to logs/actions.log."""
        msg = f"[TIER {tier}] ACTION={action} STATUS={status} ARGS={args} DETAILS={details}"
        audit_logger.info(msg)

    # ------------------ Tool Implementations ------------------
    def delete_file(self, file_path: str) -> str:
        """Safe file deletion: sends to Recycle Bin via send2trash (Tier 2)."""
        abs_path = os.path.abspath(file_path)
        if self.is_path_blocked(abs_path):
            self.log_action("delete_file", 3, {"path": abs_path}, "BLOCKED", "Path in blocked list")
            return "Action blocked, boss. Cannot delete system-protected files."

        if not os.path.exists(abs_path):
            return f"File does not exist: {file_path}"

        try:
            import send2trash

            send2trash.send2trash(abs_path)
            self.undo_stack.append({
                "action": "delete_file",
                "path": abs_path,
                "timestamp": dt.datetime.now().isoformat(),
            })
            self.log_action("delete_file", 2, {"path": abs_path}, "EXECUTED", "Moved to Recycle Bin")
            return f"Moved {os.path.basename(abs_path)} to the Recycle Bin, boss."
        except Exception as e:
            self.log_action("delete_file", 2, {"path": abs_path}, "FAILED", str(e))
            return f"Failed to delete file: {e}"

    def write_file(self, file_path: str, content: str) -> str:
        """Create or write a file (Tier 2)."""
        abs_path = os.path.abspath(file_path)
        if self.is_path_blocked(abs_path):
            self.log_action("write_file", 3, {"path": abs_path}, "BLOCKED", "Protected system path")
            return "Action blocked, boss. Cannot modify system-protected directories."

        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.log_action("write_file", 2, {"path": abs_path, "chars": len(content)}, "EXECUTED")
            return f"Wrote {len(content)} characters to {os.path.basename(abs_path)}, boss."
        except Exception as e:
            self.log_action("write_file", 2, {"path": abs_path}, "FAILED", str(e))
            return f"Failed to write file: {e}"

    def read_file(self, file_path: str, max_chars: int = 2000) -> str:
        """Read text from file (Tier 1)."""
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return f"File does not exist: {file_path}"

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars)
            self.log_action("read_file", 1, {"path": abs_path}, "EXECUTED")
            return content
        except Exception as e:
            self.log_action("read_file", 1, {"path": abs_path}, "FAILED", str(e))
            return f"Error reading file: {e}"

    def list_dir(self, dir_path: str = ".") -> str:
        """List directory contents (Tier 1)."""
        abs_path = os.path.abspath(dir_path)
        if not os.path.exists(abs_path):
            return f"Directory does not exist: {dir_path}"

        try:
            entries = os.listdir(abs_path)[:30]
            summary = ", ".join(entries) if entries else "Empty directory"
            self.log_action("list_dir", 1, {"path": abs_path}, "EXECUTED")
            return f"Contents of {os.path.basename(abs_path) or abs_path}: {summary}"
        except Exception as e:
            self.log_action("list_dir", 1, {"path": abs_path}, "FAILED", str(e))
            return f"Error listing directory: {e}"

    def create_folder(self, folder_path: str) -> str:
        abs_path = os.path.abspath(folder_path)
        if self.is_path_blocked(abs_path):
            self.log_action("create_folder", 3, {"path": abs_path}, "BLOCKED", "Protected system path")
            return "Action blocked, boss. Cannot create folders in protected system paths."
        try:
            os.makedirs(abs_path, exist_ok=True)
            self.log_action("create_folder", 1, {"path": abs_path}, "EXECUTED")
            return f"Folder ready: {abs_path}"
        except OSError as exc:
            self.log_action("create_folder", 1, {"path": abs_path}, "FAILED", str(exc))
            return f"Failed to create folder: {exc}"

    def find_files(self, root: str = ".", name: str = "", content: str = "", limit: int = 50) -> str:
        """Find files by name and optional text, bounded to avoid scanning the whole disk."""
        base = os.path.abspath(root)
        if self.is_path_blocked(base):
            self.log_action("find_files", 3, {"root": base}, "BLOCKED", "Protected system path")
            return "Action blocked, boss. That search root is protected."
        if not os.path.isdir(base):
            return f"Directory does not exist: {root}"
        needle = name.casefold()
        text_needle = content.casefold()
        found: list[str] = []
        try:
            for current, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if not self.is_path_blocked(os.path.join(current, d))]
                for filename in files:
                    if needle and needle not in filename.casefold():
                        continue
                    path = os.path.join(current, filename)
                    if text_needle:
                        try:
                            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                                if text_needle not in handle.read(1_000_000).casefold():
                                    continue
                        except (OSError, UnicodeError):
                            continue
                    found.append(path)
                    if len(found) >= max(1, min(int(limit), 100)):
                        break
                if len(found) >= max(1, min(int(limit), 100)):
                    break
            self.log_action("find_files", 1, {"root": base, "name": name, "content": bool(content)}, "EXECUTED")
            return "\n".join(found) if found else "No matching files found."
        except OSError as exc:
            self.log_action("find_files", 1, {"root": base}, "FAILED", str(exc))
            return f"File search failed: {exc}"

    def list_running(self) -> str:
        try:
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False,
            )
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            self.log_action("list_running", 1, {}, "EXECUTED", f"count={len(lines)}")
            return "\n".join(lines[:50]) if lines else "No running processes reported."
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log_action("list_running", 1, {}, "FAILED", str(exc))
            return f"Could not list running processes: {exc}"

    def open_path(self, path: str) -> str:
        abs_path = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
        if not os.path.exists(abs_path):
            return f"Path does not exist: {path}"
        try:
            os.startfile(abs_path)
            self.log_action("open_path", 1, {"path": abs_path}, "EXECUTED")
            return f"Opened {abs_path}, boss."
        except (AttributeError, OSError) as exc:
            self.log_action("open_path", 1, {"path": abs_path}, "FAILED", str(exc))
            return f"Could not open path: {exc}"

    def copy_file(self, source: str, destination: str) -> str:
        src = os.path.abspath(source)
        dst = os.path.abspath(destination)
        if self.is_path_blocked(src) or self.is_path_blocked(dst):
            self.log_action("copy_file", 3, {"source": src, "destination": dst}, "BLOCKED", "Protected system path")
            return "Action blocked, boss. Protected system paths cannot be copied to or from."
        try:
            if os.path.exists(dst):
                return "Copy cancelled: destination already exists."
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            self.undo_stack.append({"action": "copy_file", "source": src, "destination": dst, "timestamp": dt.datetime.now().isoformat()})
            self.log_action("copy_file", 2, {"source": src, "destination": dst}, "EXECUTED")
            return f"Copied {os.path.basename(src)} to {dst}, boss."
        except OSError as exc:
            self.log_action("copy_file", 2, {"source": src, "destination": dst}, "FAILED", str(exc))
            return f"Copy failed: {exc}"

    def move_file(self, source: str, destination: str) -> str:
        src = os.path.abspath(source)
        dst = os.path.abspath(destination)
        if self.is_path_blocked(src) or self.is_path_blocked(dst):
            self.log_action("move_file", 3, {"source": src, "destination": dst}, "BLOCKED", "Protected system path")
            return "Action blocked, boss. Protected system paths cannot be moved."
        try:
            if os.path.exists(dst):
                return "Move cancelled: destination already exists."
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            self.undo_stack.append({"action": "move_file", "source": src, "destination": dst, "timestamp": dt.datetime.now().isoformat()})
            self.log_action("move_file", 2, {"source": src, "destination": dst}, "EXECUTED")
            return f"Moved {os.path.basename(src)} to {dst}, boss."
        except OSError as exc:
            self.log_action("move_file", 2, {"source": src, "destination": dst}, "FAILED", str(exc))
            return f"Move failed: {exc}"

    def get_system_stats(self) -> str:
        """Return basic CPU, memory, and disk health (Tier 1)."""
        try:
            total, used, free = shutil.disk_usage(os.path.abspath(os.sep))
            disk_pct = int((used / total) * 100)
            self.log_action("get_system_stats", 1, {}, "EXECUTED")
            return f"Primary storage drive is at {disk_pct}% capacity with {free // (2**30)} gigabytes free, boss."
        except Exception as e:
            return f"System telemetry unavailable: {e}"

    def run_powershell(self, command: str, timeout_seconds: int = 30) -> str:
        """Run a bounded, non-elevated PowerShell command with policy enforcement."""
        command = str(command or "").strip()
        if not command:
            return "No PowerShell command was provided, boss."
        if self.is_command_blocked(command):
            self.log_action("run_powershell", 3, {"command": command}, "BLOCKED", "Command policy violation")
            return "Action blocked, boss. That command matches a protected or destructive pattern."

        timeout = max(1, min(int(timeout_seconds), 60))
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
            )
            output = (result.stdout or "").strip()
            error = (result.stderr or "").strip()
            combined = output if output else error
            if len(combined) > 4000:
                combined = combined[:4000] + "\n[output truncated]"
            status = "EXECUTED" if result.returncode == 0 else "FAILED"
            self.log_action(
                "run_powershell",
                2,
                {"command": command, "timeout_seconds": timeout},
                status,
                f"exit_code={result.returncode}",
            )
            return f"PowerShell exit code {result.returncode}:\n{combined or '(no output)'}"
        except subprocess.TimeoutExpired:
            self.log_action("run_powershell", 2, {"command": command}, "FAILED", "Timed out")
            return f"PowerShell command timed out after {timeout} seconds, boss."
        except OSError as exc:
            self.log_action("run_powershell", 2, {"command": command}, "FAILED", str(exc))
            return f"PowerShell could not be started, boss: {exc}"

    def execute_tool(
        self,
        action: str,
        args: dict[str, Any],
        confirm_callback: Callable[[str], bool] | None = None,
    ) -> str:
        """Route tool with tier validation and safety enforcement."""
        tier = self.get_tier(action)

        # Tier 3: Blocked
        if tier == 3:
            self.log_action(action, 3, args, "BLOCKED", "Policy violation")
            return f"Action blocked, boss. Safety protocols strictly forbid '{action}'."

        # Tier 2: Confirmation required
        if tier == 2:
            prompt_msg = f"Confirmation required: '{action}' with {args}. Proceed, boss?"
            if confirm_callback:
                confirmed = confirm_callback(prompt_msg)
                if not confirmed:
                    self.log_action(action, 2, args, "CANCELLED", "User denied confirmation")
                    return f"Action cancelled, boss."
            self.log_action(action, 2, args, "CONFIRMED")

        # Execution
        if action == "delete_file":
            return self.delete_file(args.get("path", ""))
        elif action == "write_file":
            return self.write_file(args.get("path", ""), args.get("content", ""))
        elif action == "read_file":
            return self.read_file(args.get("path", ""))
        elif action == "list_dir":
            return self.list_dir(args.get("path", "."))
        elif action == "get_system_stats":
            return self.get_system_stats()
        elif action == "create_folder":
            return self.create_folder(args.get("path", ""))
        elif action == "find_files":
            return self.find_files(args.get("root", "."), args.get("name", ""), args.get("content", ""), args.get("limit", 50))
        elif action == "list_running":
            return self.list_running()
        elif action == "open_path":
            return self.open_path(args.get("path", ""))
        elif action == "copy_file":
            return self.copy_file(args.get("source", ""), args.get("destination", ""))
        elif action == "move_file":
            return self.move_file(args.get("source", ""), args.get("destination", ""))
        elif action == "run_powershell":
            return self.run_powershell(args.get("command", ""), args.get("timeout_seconds", 30))

        self.log_action(action, tier, args, "UNKNOWN")
        return f"Unknown tool '{action}', boss."


# Global safety and tool manager
tools = SafetyManager()
