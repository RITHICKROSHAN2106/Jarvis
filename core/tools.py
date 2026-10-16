"""
JARVIS Tool Execution & Safety Architecture (Phase 4)
Manages Tier 1 (Safe), Tier 2 (Destructive / Confirmation), and Tier 3 (Blocked).
Integrates Recycle Bin deletions via send2trash, audit logging in logs/actions.log,
and an undo stack for reversible operations.
"""
from __future__ import annotations

import datetime as dt
import html.parser
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
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

    def tool_catalog(self) -> list[dict[str, Any]]:
        """Return stable JSON-compatible schemas for the tools exposed to the brain."""
        return [
            {"name": "read_file", "tier": self.get_tier("read_file"), "parameters": {"path": "string"}},
            {"name": "list_dir", "tier": self.get_tier("list_dir"), "parameters": {"path": "string"}},
            {"name": "find_files", "tier": self.get_tier("find_files"), "parameters": {"root": "string", "name": "string", "content": "string", "limit": "integer"}},
            {"name": "create_folder", "tier": self.get_tier("create_folder"), "parameters": {"path": "string"}},
            {"name": "open_path", "tier": self.get_tier("open_path"), "parameters": {"path": "string"}},
            {"name": "file_info", "tier": self.get_tier("file_info"), "parameters": {"path": "string"}},
            {"name": "list_running", "tier": self.get_tier("list_running"), "parameters": {}},
            {"name": "copy_file", "tier": self.get_tier("copy_file"), "parameters": {"source": "string", "destination": "string"}},
            {"name": "move_file", "tier": self.get_tier("move_file"), "parameters": {"source": "string", "destination": "string"}},
            {"name": "rename_file", "tier": self.get_tier("rename_file"), "parameters": {"path": "string", "new_name": "string"}},
            {"name": "append_file", "tier": self.get_tier("append_file"), "parameters": {"path": "string", "content": "string"}},
            {"name": "zip_files", "tier": self.get_tier("zip_files"), "parameters": {"source": "string", "archive": "string"}},
            {"name": "unzip_file", "tier": self.get_tier("unzip_file"), "parameters": {"archive": "string", "destination": "string"}},
            {"name": "kill_process", "tier": self.get_tier("kill_process"), "parameters": {"pid": "integer"}},
            {"name": "delete_file", "tier": self.get_tier("delete_file"), "parameters": {"path": "string"}},
            {"name": "run_powershell", "tier": self.get_tier("run_powershell"), "parameters": {"command": "string", "timeout_seconds": "integer"}},
            {"name": "screenshot", "tier": self.get_tier("screenshot"), "parameters": {"path": "string"}},
            {"name": "ui_inspect", "tier": self.get_tier("ui_inspect"), "parameters": {"title": "string"}},
            {"name": "ui_click", "tier": self.get_tier("ui_click"), "parameters": {"title": "string", "control": "string"}},
            {"name": "ui_type", "tier": self.get_tier("ui_type"), "parameters": {"title": "string", "control": "string", "text": "string"}},
            {"name": "browser_open", "tier": self.get_tier("browser_open"), "parameters": {"url": "string"}},
            {"name": "browser_search", "tier": self.get_tier("browser_search"), "parameters": {"query": "string"}},
            {"name": "browser_fetch", "tier": self.get_tier("browser_fetch"), "parameters": {"url": "string"}},
            {"name": "ocr_screenshot", "tier": self.get_tier("ocr_screenshot"), "parameters": {"path": "string"}},
            {"name": "system_status", "tier": self.get_tier("system_status"), "parameters": {}},
            {"name": "set_brightness", "tier": self.get_tier("set_brightness"), "parameters": {"percent": "integer"}},
            {"name": "wifi_toggle", "tier": self.get_tier("wifi_toggle"), "parameters": {"interface": "string", "enabled": "boolean"}},
            {"name": "bluetooth_toggle", "tier": self.get_tier("bluetooth_toggle"), "parameters": {"name": "string", "enabled": "boolean"}},
        ]

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

    def screenshot(self, path: str = "") -> str:
        target = os.path.abspath(path or os.path.join(BASE_DIR, "data", "screenshots", f"screenshot-{dt.datetime.now():%Y%m%d-%H%M%S}.png"))
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            import pyautogui
            pyautogui.screenshot(target)
            self.log_action("screenshot", 1, {"path": target}, "EXECUTED")
            return f"Screenshot saved to {target}, boss."
        except (OSError, ImportError) as exc:
            self.log_action("screenshot", 1, {"path": target}, "FAILED", str(exc))
            return f"Screenshot failed: {exc}"

    def _ui_window(self, title: str):
        try:
            from pywinauto import Desktop
        except ImportError:
            return None, "UI Automation is not installed. Install pywinauto in the project environment, boss."
        try:
            window = Desktop(backend="uia").window(title_re=title)
            if not window.exists(timeout=2):
                return None, f"No visible window matched '{title}', boss."
            return window, ""
        except (OSError, RuntimeError) as exc:
            return None, f"UI Automation could not inspect the window: {exc}"

    def ui_inspect(self, title: str) -> str:
        window, error = self._ui_window(title)
        if error:
            return error
        try:
            controls = []
            for control in window.descendants():
                name = control.window_text().strip()
                if name:
                    controls.append(f"{control.control_type()}:{name}")
                if len(controls) >= 80:
                    break
            self.log_action("ui_inspect", 1, {"title": title}, "EXECUTED", f"count={len(controls)}")
            return "\n".join(controls) or "No named controls found."
        except (OSError, RuntimeError) as exc:
            return f"UI inspection failed: {exc}"

    def ui_click(self, title: str, control: str) -> str:
        window, error = self._ui_window(title)
        if error:
            return error
        try:
            target = window.child_window(title=control)
            target.wait("visible", timeout=3)
            target.click_input()
            self.log_action("ui_click", 2, {"title": title, "control": control}, "EXECUTED")
            return f"Clicked '{control}', boss."
        except (OSError, RuntimeError) as exc:
            return f"UI click failed: {exc}"

    def ui_type(self, title: str, control: str, text: str) -> str:
        window, error = self._ui_window(title)
        if error:
            return error
        try:
            target = window.child_window(title=control)
            target.wait("visible", timeout=3)
            target.set_focus()
            target.type_keys(text, with_spaces=True)
            self.log_action("ui_type", 1, {"title": title, "control": control, "chars": len(text)}, "EXECUTED")
            return f"Typed into '{control}', boss."
        except (OSError, RuntimeError) as exc:
            return f"UI typing failed: {exc}"

    def browser_open(self, url: str) -> str:
        if not re.match(r"^https?://", url, re.I):
            return "Browser navigation requires an http or https URL, boss."
        try:
            import webbrowser
            webbrowser.open(url)
            self.log_action("browser_open", 1, {"url": url}, "EXECUTED")
            return f"Opened {url}, boss."
        except OSError as exc:
            return f"Browser launch failed: {exc}"

    def browser_search(self, query: str) -> str:
        from urllib.parse import quote_plus
        return self.browser_open("https://www.google.com/search?q=" + quote_plus(query))

    def browser_fetch(self, url: str) -> str:
        if not re.match(r"^https?://", url, re.I):
            return "Page fetching requires an http or https URL, boss."
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(1_000_000).decode("utf-8", errors="replace")
            class TextParser(html.parser.HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.parts = []
                    self.skip = 0
                def handle_starttag(self, tag, attrs):
                    if tag.lower() in {"script", "style", "noscript"}:
                        self.skip += 1
                def handle_endtag(self, tag):
                    if tag.lower() in {"script", "style", "noscript"} and self.skip:
                        self.skip -= 1
                def handle_data(self, data):
                    if not self.skip and data.strip():
                        self.parts.append(data.strip())
            parser = TextParser()
            parser.feed(raw)
            text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
            self.log_action("browser_fetch", 1, {"url": url}, "EXECUTED", f"chars={len(text)}")
            return text[:8000] or "The page contained no readable text."
        except (OSError, ValueError, TimeoutError) as exc:
            self.log_action("browser_fetch", 1, {"url": url}, "FAILED", str(exc))
            return f"Page fetch failed: {exc}"

    def ocr_screenshot(self, path: str) -> str:
        try:
            import pytesseract
            from PIL import Image
            text = pytesseract.image_to_string(Image.open(os.path.abspath(path)))
            self.log_action("ocr_screenshot", 1, {"path": path}, "EXECUTED", f"chars={len(text)}")
            return text[:8000] or "No text was detected."
        except ImportError:
            return "OCR is unavailable because pytesseract is not installed, boss."
        except (OSError, RuntimeError) as exc:
            return f"OCR failed: {exc}"

    def system_status(self) -> str:
        commands = {
            "wifi": "netsh wlan show interfaces",
            "battery": "Get-CimInstance Win32_Battery | Select-Object EstimatedChargeRemaining,BatteryStatus | Format-List",
            "bluetooth": "Get-PnpDevice -Class Bluetooth -Status OK | Select-Object FriendlyName,Status | Format-Table -AutoSize",
        }
        results = []
        for name, command in commands.items():
            try:
                result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=10, check=False)
                results.append(f"{name}:\n{(result.stdout or result.stderr).strip()[:1200]}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                results.append(f"{name}: unavailable ({exc})")
        self.log_action("system_status", 1, {}, "EXECUTED")
        return "\n\n".join(results)

    def set_brightness(self, percent: int) -> str:
        try:
            value = max(0, min(100, int(percent)))
            import screen_brightness_control as sbc
            sbc.set_brightness(value)
            self.log_action("set_brightness", 2, {"percent": value}, "EXECUTED")
            return f"Brightness set to {value} percent, boss."
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            self.log_action("set_brightness", 2, {"percent": percent}, "FAILED", str(exc))
            return f"Brightness control failed: {exc}"

    def wifi_toggle(self, interface: str, enabled: bool) -> str:
        state = "enabled" if enabled else "disabled"
        command = ["netsh", "interface", "set", "interface", f"name={interface}", f"admin={state}"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
            output = (result.stdout or result.stderr).strip()
            status = "EXECUTED" if result.returncode == 0 else "FAILED"
            self.log_action("wifi_toggle", 2, {"interface": interface, "enabled": enabled}, status, f"exit_code={result.returncode}")
            return output or f"Wi-Fi interface operation exit code {result.returncode}."
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Wi-Fi control failed: {exc}"

    def bluetooth_toggle(self, name: str, enabled: bool) -> str:
        action = "Enable-PnpDevice" if enabled else "Disable-PnpDevice"
        escaped = name.replace("'", "''")
        command = f"Get-PnpDevice -Class Bluetooth | Where-Object {{$_.FriendlyName -like '*{escaped}*'}} | {action} -Confirm:$false"
        try:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=15, check=False)
            output = (result.stdout or result.stderr).strip()
            status = "EXECUTED" if result.returncode == 0 else "FAILED"
            self.log_action("bluetooth_toggle", 2, {"name": name, "enabled": enabled}, status, f"exit_code={result.returncode}")
            return output or f"Bluetooth operation exit code {result.returncode}. It may require administrator approval."
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Bluetooth control failed: {exc}"

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

    def file_info(self, path: str) -> str:
        abs_path = os.path.abspath(path)
        try:
            stat = os.stat(abs_path)
            kind = "directory" if os.path.isdir(abs_path) else "file"
            self.log_action("file_info", 1, {"path": abs_path}, "EXECUTED")
            return (
                f"{kind}: {abs_path}\n"
                f"size: {stat.st_size} bytes\n"
                f"modified: {dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds')}"
            )
        except OSError as exc:
            self.log_action("file_info", 1, {"path": abs_path}, "FAILED", str(exc))
            return f"Could not inspect path: {exc}"

    def append_file(self, file_path: str, content: str) -> str:
        abs_path = os.path.abspath(file_path)
        if self.is_path_blocked(abs_path):
            self.log_action("append_file", 3, {"path": abs_path}, "BLOCKED", "Protected system path")
            return "Action blocked, boss. Cannot modify protected system paths."
        try:
            with open(abs_path, "a", encoding="utf-8") as handle:
                handle.write(content)
            self.log_action("append_file", 2, {"path": abs_path, "chars": len(content)}, "EXECUTED")
            return f"Appended {len(content)} characters to {os.path.basename(abs_path)}, boss."
        except OSError as exc:
            self.log_action("append_file", 2, {"path": abs_path}, "FAILED", str(exc))
            return f"Append failed: {exc}"

    def rename_file(self, path: str, new_name: str) -> str:
        source = os.path.abspath(path)
        destination = os.path.join(os.path.dirname(source), os.path.basename(new_name))
        if self.is_path_blocked(source) or self.is_path_blocked(destination):
            self.log_action("rename_file", 3, {"path": source, "new_name": new_name}, "BLOCKED", "Protected system path")
            return "Action blocked, boss. Protected system paths cannot be renamed."
        if os.path.exists(destination):
            return "Rename cancelled: destination already exists."
        try:
            os.rename(source, destination)
            self.undo_stack.append({"action": "rename_file", "source": source, "destination": destination, "timestamp": dt.datetime.now().isoformat()})
            self.log_action("rename_file", 2, {"source": source, "destination": destination}, "EXECUTED")
            return f"Renamed to {os.path.basename(destination)}, boss."
        except OSError as exc:
            self.log_action("rename_file", 2, {"source": source, "destination": destination}, "FAILED", str(exc))
            return f"Rename failed: {exc}"

    def undo_last_action(self) -> str:
        if not self.undo_stack:
            return "Nothing in the undo buffer to reverse, boss."
        action = self.undo_stack.pop()
        kind = action.get("action")
        try:
            if kind in {"move_file", "rename_file"}:
                source, destination = action["source"], action["destination"]
                if os.path.exists(destination) and not os.path.exists(source):
                    os.rename(destination, source)
                    self.log_action("undo", 2, action, "EXECUTED", f"reversed {kind}")
                    return f"Undid {kind}, boss."
            elif kind == "copy_file":
                destination = action["destination"]
                if os.path.exists(destination):
                    os.remove(destination)
                    self.log_action("undo", 2, action, "EXECUTED", "removed copied file")
                    return "Undid the copy, boss."
            elif kind == "delete_file":
                self.log_action("undo", 2, action, "UNAVAILABLE", "Recycle Bin restore requires shell UI")
                return "The item is in the Recycle Bin. Restore it there, boss."
            self.log_action("undo", 2, action, "FAILED", "state changed or source unavailable")
            return "I could not safely undo the last action, boss."
        except OSError as exc:
            self.log_action("undo", 2, action, "FAILED", str(exc))
            return f"Undo failed safely: {exc}"

    def zip_files(self, source: str, archive: str) -> str:
        src = os.path.abspath(source)
        dst = os.path.abspath(archive)
        if self.is_path_blocked(src) or self.is_path_blocked(dst):
            self.log_action("zip_files", 3, {"source": src, "archive": dst}, "BLOCKED", "Protected system path")
            return "Action blocked, boss. Protected system paths cannot be archived."
        if not os.path.exists(src):
            return f"Archive source does not exist: {source}"
        if os.path.isdir(dst):
            return "Archive cancelled: destination is a directory."
        if os.path.exists(dst):
            return "Archive cancelled: destination already exists."
        try:
            base = os.path.dirname(src) if os.path.isfile(src) else src
            with zipfile.ZipFile(dst, "x", zipfile.ZIP_DEFLATED) as archive_file:
                if os.path.isfile(src):
                    archive_file.write(src, os.path.basename(src))
                else:
                    for current, dirs, files in os.walk(src):
                        dirs[:] = [d for d in dirs if not self.is_path_blocked(os.path.join(current, d))]
                        for filename in files:
                            path = os.path.join(current, filename)
                            archive_file.write(path, os.path.relpath(path, base))
            self.log_action("zip_files", 1, {"source": src, "archive": dst}, "EXECUTED")
            return f"Created archive {dst}, boss."
        except (OSError, zipfile.BadZipFile) as exc:
            self.log_action("zip_files", 1, {"source": src, "archive": dst}, "FAILED", str(exc))
            return f"Archive failed: {exc}"

    def unzip_file(self, archive: str, destination: str) -> str:
        src = os.path.abspath(archive)
        dst = os.path.abspath(destination)
        if self.is_path_blocked(src) or self.is_path_blocked(dst):
            self.log_action("unzip_file", 3, {"archive": src, "destination": dst}, "BLOCKED", "Protected system path")
            return "Action blocked, boss. Protected system paths cannot be extracted there."
        try:
            os.makedirs(dst, exist_ok=True)
            with zipfile.ZipFile(src) as archive_file:
                root = os.path.realpath(dst)
                for member in archive_file.infolist():
                    target = os.path.realpath(os.path.join(dst, member.filename))
                    if target != root and not target.startswith(root + os.sep):
                        return "Extraction blocked: archive contains an unsafe path."
                    if os.path.exists(target):
                        return f"Extraction cancelled: destination already contains {member.filename}."
                archive_file.extractall(dst)
            self.log_action("unzip_file", 1, {"archive": src, "destination": dst}, "EXECUTED")
            return f"Extracted archive to {dst}, boss."
        except (OSError, zipfile.BadZipFile) as exc:
            self.log_action("unzip_file", 1, {"archive": src, "destination": dst}, "FAILED", str(exc))
            return f"Extraction failed: {exc}"

    def kill_process(self, pid: int) -> str:
        try:
            process_id = int(pid)
            if process_id <= 0 or process_id == os.getpid():
                return "I will not terminate this safety process, boss."
            details = subprocess.run(
                ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False,
            ).stdout.strip()
            if not details or details.upper().startswith("INFO:"):
                return f"No running process found for PID {process_id}."
            process_name = details.split(",", 1)[0].strip('"').casefold()
            protected_names = {"system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe", "winlogon.exe"}
            if process_name in protected_names or process_name in {"python.exe", "pythonw.exe"} and process_id == os.getppid():
                self.log_action("kill_process", 2, {"pid": process_id, "name": process_name}, "BLOCKED", "Protected process")
                return f"I will not terminate protected process {process_name}, boss."
            result = subprocess.run(["taskkill", "/PID", str(process_id), "/T", "/F"], capture_output=True, text=True, timeout=10, check=False)
            status = "EXECUTED" if result.returncode == 0 else "FAILED"
            self.log_action("kill_process", 2, {"pid": process_id, "name": process_name}, status, f"exit_code={result.returncode}")
            return (result.stdout or result.stderr or f"Process operation exit code {result.returncode}").strip()
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            self.log_action("kill_process", 2, {"pid": pid}, "FAILED", str(exc))
            return f"Could not terminate process: {exc}"

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
        elif action == "file_info":
            return self.file_info(args.get("path", ""))
        elif action == "copy_file":
            return self.copy_file(args.get("source", ""), args.get("destination", ""))
        elif action == "move_file":
            return self.move_file(args.get("source", ""), args.get("destination", ""))
        elif action == "rename_file":
            return self.rename_file(args.get("path", ""), args.get("new_name", ""))
        elif action == "append_file":
            return self.append_file(args.get("path", ""), args.get("content", ""))
        elif action == "zip_files":
            return self.zip_files(args.get("source", ""), args.get("archive", ""))
        elif action == "unzip_file":
            return self.unzip_file(args.get("archive", ""), args.get("destination", ""))
        elif action == "kill_process":
            return self.kill_process(args.get("pid", 0))
        elif action == "run_powershell":
            return self.run_powershell(args.get("command", ""), args.get("timeout_seconds", 30))
        elif action == "screenshot":
            return self.screenshot(args.get("path", ""))
        elif action == "ui_inspect":
            return self.ui_inspect(args.get("title", ""))
        elif action == "ui_click":
            return self.ui_click(args.get("title", ""), args.get("control", ""))
        elif action == "ui_type":
            return self.ui_type(args.get("title", ""), args.get("control", ""), args.get("text", ""))
        elif action == "browser_open":
            return self.browser_open(args.get("url", ""))
        elif action == "browser_search":
            return self.browser_search(args.get("query", ""))
        elif action == "browser_fetch":
            return self.browser_fetch(args.get("url", ""))
        elif action == "ocr_screenshot":
            return self.ocr_screenshot(args.get("path", ""))
        elif action == "system_status":
            return self.system_status()
        elif action == "set_brightness":
            return self.set_brightness(args.get("percent", 50))
        elif action == "wifi_toggle":
            return self.wifi_toggle(args.get("interface", "Wi-Fi"), bool(args.get("enabled", True)))
        elif action == "bluetooth_toggle":
            return self.bluetooth_toggle(args.get("name", ""), bool(args.get("enabled", True)))

        self.log_action(action, tier, args, "UNKNOWN")
        return f"Unknown tool '{action}', boss."


# Global safety and tool manager
tools = SafetyManager()
