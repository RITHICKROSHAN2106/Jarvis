#!/usr/bin/env python3
"""
JARVIS Power & Session Event Monitor (Windows 10/11)
Listens for system resume from sleep/standby (WM_POWERBROADCAST)
and workstation unlock / logon (WM_WTSSESSION_CHANGE).
Welcomes Tony Stark ("boss") whenever the system wakes up or is unlocked.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("jarvis.power")

# Win32 Constants
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMSUSPEND = 0x0004

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_UNLOCK = 0x0008
WTS_SESSION_LOGON = 0x0005
WTS_SESSION_LOCK = 0x0007
NOTIFY_FOR_THIS_SESSION = 0


class PowerEventManager:
    def __init__(self, on_welcome: Optional[Callable[[str], None]] = None) -> None:
        self.on_welcome = on_welcome
        self._thread: Optional[threading.Thread] = None
        self._hwnd: Optional[int] = None
        self._class_atom: Optional[int] = None
        self._hinst: Optional[int] = None
        self._last_welcome_time: float = 0.0
        self._debounce_seconds: float = 12.0
        self._running: bool = False

    def start(self) -> None:
        """Start the background Win32 message loop thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_message_loop, daemon=True, name="JarvisPowerEvents")
        self._thread.start()
        logger.info("Power and session event listener started.")

    def stop(self) -> None:
        """Stop listening and clean up Win32 resources."""
        self._running = False
        if self._hwnd:
            try:
                import win32gui
                import win32con
                import win32ts

                try:
                    win32ts.WTSUnRegisterSessionNotification(self._hwnd)
                except Exception:
                    pass
                win32gui.PostMessage(self._hwnd, win32con.WM_QUIT, 0, 0)
            except Exception as e:
                logger.debug(f"Error posting WM_QUIT to power window: {e}")

    def _trigger_welcome(self, reason: str) -> None:
        now = time.time()
        if now - self._last_welcome_time < self._debounce_seconds:
            logger.debug(f"Ignoring duplicate power event '{reason}' (debounced).")
            return
        self._last_welcome_time = now

        def _delayed_greet() -> None:
            # 1.2s delay to give Windows Audio service (Audiosrv) time to resume output devices
            time.sleep(1.2)
            logger.info(f"Triggering wake welcome greeting (reason: {reason})")
            if self.on_welcome:
                try:
                    self.on_welcome(reason)
                except Exception as e:
                    logger.error(f"Error in on_welcome callback: {e}", exc_info=True)

        t = threading.Thread(target=_delayed_greet, daemon=True, name="JarvisWakeGreet")
        t.start()

    def _run_message_loop(self) -> None:
        try:
            import win32gui
            import win32con
            import win32ts

            def wnd_proc(hwnd, msg, wparam, lparam):
                if msg == WM_POWERBROADCAST:
                    if wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                        logger.info(f"WM_POWERBROADCAST: Resume from sleep detected (wparam={hex(wparam)})")
                        self._trigger_welcome("wake_from_sleep")
                    elif wparam == PBT_APMSUSPEND:
                        logger.info("WM_POWERBROADCAST: System entering suspend/sleep.")

                elif msg == WM_WTSSESSION_CHANGE:
                    if wparam in (WTS_SESSION_UNLOCK, WTS_SESSION_LOGON):
                        ev_name = "unlock" if wparam == WTS_SESSION_UNLOCK else "logon"
                        logger.info(f"WM_WTSSESSION_CHANGE: Workstation {ev_name} detected.")
                        self._trigger_welcome(f"workstation_{ev_name}")

                elif msg == win32con.WM_DESTROY:
                    win32gui.PostQuitMessage(0)
                    return 0

                return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = wnd_proc
            wc.lpszClassName = f"JarvisPowerEvents_{id(self)}"
            self._hinst = wc.hInstance = win32gui.GetModuleHandle(None)

            try:
                self._class_atom = win32gui.RegisterClass(wc)
            except Exception:
                pass

            self._hwnd = win32gui.CreateWindow(
                self._class_atom or wc.lpszClassName,
                "JarvisPowerWindow",
                0,
                0, 0, 0, 0,
                0, 0,
                self._hinst,
                None,
            )

            try:
                win32ts.WTSRegisterSessionNotification(self._hwnd, NOTIFY_FOR_THIS_SESSION)
                logger.info(f"Registered session notification on HWND {self._hwnd}")
            except Exception as e:
                logger.warning(f"Could not register session notification: {e}")

            win32gui.PumpMessages()

        except Exception as e:
            logger.error(f"PowerEventManager message loop encountered error: {e}", exc_info=True)


power_events = PowerEventManager()
