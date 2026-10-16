"""Fullscreen cinematic JARVIS command-bridge HUD.

The public queue/state API is intentionally small so the audio loop can update
the UI without touching Tkinter from a worker thread.
"""
from __future__ import annotations

import math
import queue
import random
import threading
import time

BG = "#030711"
PANEL = "#071321"
CYAN = "#37e6ff"
CYAN_DIM = "#0b7897"
GOLD = "#ffc857"
PURPLE = "#a78bfa"
GREEN = "#36e0a0"
RED = "#ff5f76"
TEXT = "#e6f7ff"
MUTED = "#7194a6"


class JarvisHUD:
    """Thread-safe fullscreen Tkinter HUD with animated particles."""

    def __init__(self, title: str = "JARVIS COMMAND BRIDGE") -> None:
        self.title = title
        self.cmd_queue: queue.Queue = queue.Queue()
        self.state = "STANDBY"
        self.user_text = ""
        self.jarvis_text = "All systems operational. Standing by."
        self.is_muted = False
        self.is_collapsed = False
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.root = None
        self.canvas = None
        self.status_lbl = None
        self.user_lbl = None
        self.jarvis_lbl = None
        self._particles: list[dict[str, float]] = []
        self._phase = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_gui, daemon=True, name="JarvisHUDThread")
        self._thread.start()
        self._ready.wait(timeout=1.5)

    def set_state(self, new_state: str) -> None:
        self.cmd_queue.put(("state", new_state.upper()))

    def set_transcript(self, user: str = "", jarvis: str = "") -> None:
        self.cmd_queue.put(("transcript", (user, jarvis)))

    def set_mute(self, muted: bool) -> None:
        self.cmd_queue.put(("mute", muted))

    def _run_gui(self) -> None:
        try:
            import tkinter as tk
            from tkinter import font as tkfont
        except ImportError:
            print("[HUD] Tkinter not available, running headlessly.")
            self._ready.set()
            return

        self.root = tk.Tk()
        self.root.title(self.title)
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.bind("<Escape>", lambda _event: self.root.withdraw())
        self.root.bind("<F11>", lambda _event: self._toggle_fullscreen())

        self.width = self.root.winfo_screenwidth()
        self.height = self.root.winfo_screenheight()
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._particles = self._make_particles()

        self._font_display = tkfont.Font(family="Segoe UI Light", size=28)
        self._font_title = tkfont.Font(family="Consolas", size=11, weight="bold")
        self._font_body = tkfont.Font(family="Segoe UI", size=13)
        self._font_mono = tkfont.Font(family="Consolas", size=10)

        self._ready.set()
        self._schedule_update()
        self._animate()
        try:
            self.root.mainloop()
        except Exception:
            pass

    def _toggle_fullscreen(self) -> None:
        if self.root:
            current = bool(self.root.attributes("-fullscreen"))
            self.root.attributes("-fullscreen", not current)

    def _make_particles(self) -> list[dict[str, float]]:
        count = max(90, min(220, int((self.width * self.height) / 14000)))
        return [
            {
                "x": random.uniform(0, self.width),
                "y": random.uniform(0, self.height),
                "speed": random.uniform(0.08, 0.42),
                "size": random.choice((1, 1, 1, 2)),
                "phase": random.uniform(0, math.tau),
            }
            for _ in range(count)
        ]

    def _schedule_update(self) -> None:
        if not self.root:
            return
        try:
            while True:
                cmd, val = self.cmd_queue.get_nowait()
                if cmd == "state":
                    self.state = val
                elif cmd == "transcript":
                    user, jarvis = val
                    if user:
                        self.user_text = user
                    if jarvis:
                        self.jarvis_text = jarvis
                elif cmd == "mute":
                    self.is_muted = val
                elif cmd == "show":
                    self.root.deiconify()
                elif cmd == "hide":
                    self.root.withdraw()
        except queue.Empty:
            pass
        except Exception:
            pass
        self._update_status()
        self.root.after(40, self._schedule_update)

    def _update_status(self) -> None:
        if self.status_lbl:
            label, color = self._status_text()
            self.status_lbl.config(text=label, fg=color)

    def _status_text(self) -> tuple[str, str]:
        if self.is_muted:
            return "MUTED", RED
        return {
            "LISTENING": ("LISTENING", GOLD),
            "THINKING": ("PROCESSING", PURPLE),
            "SPEAKING": ("SPEAKING", CYAN),
        }.get(self.state, ("STANDBY", GREEN))

    def _accent(self) -> str:
        return self._status_text()[1]

    def _panel(self, x1: float, y1: float, x2: float, y2: float, title: str) -> None:
        accent = self._accent()
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=PANEL, outline="#123247", width=1)
        self.canvas.create_line(x1, y1, x1 + 86, y1, fill=accent, width=2)
        self.canvas.create_text(x1 + 18, y1 + 18, text=title, anchor="w", fill=accent, font=self._font_title)

    def _draw_reactor(self, cx: float, cy: float) -> None:
        accent = self._accent()
        self._phase += 0.045
        pulse = math.sin(self._phase * (2.0 if self.state != "STANDBY" else 0.7))
        spin = self._phase * (5.0 if self.state == "THINKING" else 1.0)

        for radius, color, width in ((210, "#082638", 1), (178, CYAN_DIM, 1), (142, accent, 2)):
            self.canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline=color, width=width)
        for i in range(12):
            angle = spin + i * math.tau / 12
            inner, outer = 154, 174
            self.canvas.create_line(
                cx + math.cos(angle) * inner,
                cy + math.sin(angle) * inner,
                cx + math.cos(angle) * outer,
                cy + math.sin(angle) * outer,
                fill=accent if i % 3 == 0 else "#1a6175",
                width=2,
            )
        glow = 62 + pulse * 7
        self.canvas.create_oval(cx - glow * 1.6, cy - glow * 1.6, cx + glow * 1.6, cy + glow * 1.6, outline="#0b526d", width=2)
        self.canvas.create_oval(cx - glow, cy - glow, cx + glow, cy + glow, fill="#08344b", outline=accent, width=2)
        self.canvas.create_oval(cx - 30, cy - 30, cx + 30, cy + 30, fill=accent, outline="#e9fdff", width=2)
        self.canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, fill="#ffffff", outline="")

    def _animate(self) -> None:
        if not self.root or not self.canvas:
            return
        self.canvas.delete("all")
        width, height = self.width, self.height
        self.canvas.create_rectangle(0, 0, width, height, fill=BG, outline="")
        for particle in self._particles:
            particle["y"] -= particle["speed"]
            if particle["y"] < -4:
                particle["y"] = height + 4
                particle["x"] = random.uniform(0, width)
            shimmer = (math.sin(self._phase + particle["phase"]) + 1) / 2
            color = "#1b6076" if shimmer < 0.7 else "#54d9ed"
            size = particle["size"]
            self.canvas.create_oval(particle["x"] - size, particle["y"] - size, particle["x"] + size, particle["y"] + size, fill=color, outline="")

        # scanlines and framing
        for y in range(0, height, 7):
            self.canvas.create_line(0, y, width, y, fill="#06111d")
        margin = 34
        self.canvas.create_rectangle(margin, margin, width - margin, height - margin, outline="#0c2a3b")
        self.canvas.create_text(margin + 24, margin + 24, text="JARVIS", anchor="w", fill=TEXT, font=self._font_display)
        self.canvas.create_text(margin + 26, margin + 62, text="PERSONAL INTELLIGENCE // COMMAND BRIDGE", anchor="w", fill=MUTED, font=self._font_mono)
        self.canvas.create_text(width - margin - 24, margin + 26, text=time.strftime("%H:%M:%S"), anchor="e", fill=CYAN, font=self._font_mono)
        self.canvas.create_text(width - margin - 24, margin + 50, text="MARK 85  /  ONLINE", anchor="e", fill=GREEN, font=self._font_mono)

        cx, cy = width / 2, height / 2 + 30
        self._draw_reactor(cx, cy)
        self.canvas.create_text(cx, cy + 245, text="J A R V I S", fill=TEXT, font=self._font_title)
        self.canvas.create_text(cx, cy + 268, text="VOICE INTERFACE ACTIVE", fill=self._accent(), font=self._font_mono)

        left_x, right_x = margin + 36, width - margin - 330
        self._panel(left_x, height - 255, left_x + 300, height - 82, "INCOMING // YOU")
        self.canvas.create_text(left_x + 18, height - 210, text=self.user_text[-125:] or "Awaiting voice input...", anchor="nw", fill=TEXT if self.user_text else MUTED, font=self._font_body, width=264)
        self._panel(right_x, height - 255, right_x + 294, height - 82, "OUTGOING // JARVIS")
        self.canvas.create_text(right_x + 18, height - 210, text=self.jarvis_text[-125:], anchor="nw", fill=TEXT, font=self._font_body, width=258)
        label, color = self._status_text()
        self.canvas.create_text(cx, height - 70, text=f"●  {label}", fill=color, font=self._font_title)
        self.root.after(33, self._animate)


hud = JarvisHUD()
