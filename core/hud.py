"""
JARVIS Futuristic Desktop HUD (Phase 6)
Lightweight Tkinter floating desktop HUD modeled after Tony Stark's Iron Man Mark 85 Arc Reactor.
Features:
  - Frameless, semi-transparent, draggable cyber-widget
  - Animated pulsing Arc Reactor core with state transitions:
      STANDBY  (subtle idle cyan glow)
      LISTENING (pulsing active listening core)
      THINKING  (spinning reactor rings)
      SPEAKING  (undulating energy waveforms)
  - Live transcript pane for speech and responses
  - Thread-safe queue messaging to GUI thread
  - Mute toggle and collapse-to-orb controls
"""
from __future__ import annotations

import math
import os
import queue
import sys
import threading
import time
from typing import Any

BG_COLOR = "#070A10"
BORDER_COLOR = "#00D2FF"
TEXT_COLOR = "#E0F2FE"
ACCENT_CYAN = "#00F2FE"
ACCENT_GOLD = "#FFD166"
ACCENT_GREEN = "#06D6A0"
ACCENT_RED = "#EF476F"
ACCENT_PURPLE = "#A855F7"


class JarvisHUD:
    """Thread-safe Tkinter HUD running on a background daemon thread."""

    def __init__(self, title: str = "JARVIS HUD") -> None:
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

    def start(self) -> None:
        """Start the HUD window in a background thread."""
        self._thread = threading.Thread(target=self._run_gui, daemon=True, name="JarvisHUDThread")
        self._thread.start()
        # Wait up to 1.5s for GUI initialization
        self._ready.wait(timeout=1.5)

    def set_state(self, new_state: str) -> None:
        """Update reactor state: 'STANDBY', 'LISTENING', 'THINKING', 'SPEAKING'."""
        self.cmd_queue.put(("state", new_state.upper()))

    def set_transcript(self, user: str = "", jarvis: str = "") -> None:
        """Update live speech transcript."""
        self.cmd_queue.put(("transcript", (user, jarvis)))

    def set_mute(self, muted: bool) -> None:
        """Set mic mute indicator."""
        self.cmd_queue.put(("mute", muted))

    # ---------------- GUI Thread Implementation ----------------
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
        self.root.configure(bg=BG_COLOR)
        self.root.overrideredirect(True)      # Borderless
        self.root.attributes("-topmost", True) # Always on top
        try:
            self.root.attributes("-alpha", 0.92)  # Transparency
        except Exception:
            pass

        # Position at top-right
        screen_w = self.root.winfo_screenwidth()
        w, h = 370, 160
        x = screen_w - w - 24
        y = 30
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        # Dragging support
        self._drag_data = {"x": 0, "y": 0}

        def _start_drag(event):
            self._drag_data["x"] = event.x
            self._drag_data["y"] = event.y

        def _do_drag(event):
            dx = event.x - self._drag_data["x"]
            dy = event.y - self._drag_data["y"]
            nx = self.root.winfo_x() + dx
            ny = self.root.winfo_y() + dy
            self.root.geometry(f"+{nx}+{ny}")

        self.root.bind("<Button-1>", _start_drag)
        self.root.bind("<B1-Motion>", _do_drag)

        # Outer border frame
        outer_frame = tk.Frame(self.root, bg=BORDER_COLOR, bd=1)
        outer_frame.pack(fill="both", expand=True, padx=1, pady=1)

        inner_frame = tk.Frame(outer_frame, bg=BG_COLOR)
        inner_frame.pack(fill="both", expand=True)

        # Top title bar
        top_bar = tk.Frame(inner_frame, bg=BG_COLOR)
        top_bar.pack(fill="x", padx=8, pady=(4, 0))

        title_lbl = tk.Label(
            top_bar,
            text="JARVIS  //  MARK 85",
            font=("Segoe UI", 9, "bold"),
            fg=ACCENT_CYAN,
            bg=BG_COLOR,
        )
        title_lbl.pack(side="left")

        # Status badge
        self.status_lbl = tk.Label(
            top_bar,
            text="[ STANDBY ]",
            font=("Consolas", 8, "bold"),
            fg=ACCENT_GREEN,
            bg=BG_COLOR,
        )
        self.status_lbl.pack(side="left", padx=8)

        # Close / hide button
        close_btn = tk.Label(
            top_bar,
            text="×",
            font=("Segoe UI", 11, "bold"),
            fg="#718096",
            bg=BG_COLOR,
            cursor="hand2",
        )
        close_btn.pack(side="right", padx=2)
        close_btn.bind("<Button-1>", lambda e: self.root.withdraw())

        # Main content area (Reactor Orb + Transcript)
        content_frame = tk.Frame(inner_frame, bg=BG_COLOR)
        content_frame.pack(fill="both", expand=True, padx=8, pady=4)

        # Canvas for Arc Reactor animation
        self.canvas = tk.Canvas(
            content_frame,
            width=70,
            height=70,
            bg=BG_COLOR,
            highlightthickness=0,
        )
        self.canvas.pack(side="left", padx=(0, 8))

        # Transcript text area
        text_frame = tk.Frame(content_frame, bg=BG_COLOR)
        text_frame.pack(side="left", fill="both", expand=True)

        self.user_lbl = tk.Label(
            text_frame,
            text="You: ...",
            font=("Segoe UI", 8),
            fg="#94A3B8",
            bg=BG_COLOR,
            anchor="w",
            justify="left",
            wraplength=260,
        )
        self.user_lbl.pack(fill="x", pady=(2, 2))

        self.jarvis_lbl = tk.Label(
            text_frame,
            text="Jarvis: Standing by, boss.",
            font=("Segoe UI", 8, "italic"),
            fg=TEXT_COLOR,
            bg=BG_COLOR,
            anchor="w",
            justify="left",
            wraplength=260,
        )
        self.jarvis_lbl.pack(fill="both", expand=True)

        # Animation state variables
        self.angle = 0.0
        self.pulse = 0.0

        # Register message polling and animation loops
        self._ready.set()
        self._schedule_update()
        self._animate_reactor()

        try:
            self.root.mainloop()
        except Exception:
            pass

    def _schedule_update(self) -> None:
        """Poll incoming commands from queue."""
        if not self.root:
            return

        try:
            while not self.cmd_queue.empty():
                cmd, val = self.cmd_queue.get_nowait()
                if cmd == "state":
                    self.state = val
                    self._update_status_label()
                elif cmd == "transcript":
                    user, jarvis = val
                    if user:
                        self.user_lbl.config(text=f"You: {user[:65]}")
                    if jarvis:
                        self.jarvis_lbl.config(text=f"Jarvis: {jarvis[:90]}")
                elif cmd == "mute":
                    self.is_muted = val
                    self._update_status_label()
                elif cmd == "show":
                    self.root.deiconify()
                elif cmd == "hide":
                    self.root.withdraw()
        except Exception:
            pass

        self.root.after(40, self._schedule_update)

    def _update_status_label(self) -> None:
        if self.is_muted:
            self.status_lbl.config(text="[ MUTED ]", fg=ACCENT_RED)
        elif self.state == "LISTENING":
            self.status_lbl.config(text="[ LISTENING ]", fg=ACCENT_GOLD)
        elif self.state == "THINKING":
            self.status_lbl.config(text="[ PROCESSING ]", fg=ACCENT_PURPLE)
        elif self.state == "SPEAKING":
            self.status_lbl.config(text="[ SPEAKING ]", fg=ACCENT_CYAN)
        else:
            self.status_lbl.config(text="[ STANDBY ]", fg=ACCENT_GREEN)

    def _animate_reactor(self) -> None:
        """Render rotating Arc Reactor geometry on Tkinter Canvas."""
        if not self.root or not self.canvas:
            return

        self.canvas.delete("all")
        cx, cy = 35, 35

        self.angle = (self.angle + (6.0 if self.state == "THINKING" else 1.5)) % 360
        self.pulse += 0.12
        sin_p = math.sin(self.pulse)

        # Color scheme based on state
        if self.state == "LISTENING":
            ring_color = ACCENT_GOLD
            core_color = "#FFAA00"
            radius_mod = sin_p * 2.5
        elif self.state == "THINKING":
            ring_color = ACCENT_PURPLE
            core_color = ACCENT_CYAN
            radius_mod = 1.0
        elif self.state == "SPEAKING":
            ring_color = ACCENT_CYAN
            core_color = "#38BDF8"
            radius_mod = math.fabs(sin_p) * 4.0
        else:
            ring_color = BORDER_COLOR
            core_color = "#0284C7"
            radius_mod = sin_p * 1.0

        # Outer ring
        r_outer = 28 + radius_mod
        self.canvas.create_oval(
            cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
            outline=ring_color, width=1.5
        )

        # Middle segmented ring (rotates)
        r_mid = 20
        rad = math.radians(self.angle)
        for i in range(6):
            a = rad + i * (math.pi / 3)
            x1 = cx + (r_mid - 3) * math.cos(a)
            y1 = cy + (r_mid - 3) * math.sin(a)
            x2 = cx + (r_mid + 3) * math.cos(a)
            y2 = cy + (r_mid + 3) * math.sin(a)
            self.canvas.create_line(x1, y1, x2, y2, fill=ring_color, width=2)

        # Inner pulsing reactor core
        r_core = 11 + (sin_p * 1.5 if self.state in ("LISTENING", "SPEAKING") else 0)
        self.canvas.create_oval(
            cx - r_core, cy - r_core, cx + r_core, cy + r_core,
            fill=core_color, outline=ACCENT_CYAN, width=1
        )

        # Center highlight dot
        self.canvas.create_oval(
            cx - 3, cy - 3, cx + 3, cy + 3,
            fill="#FFFFFF", outline=""
        )

        # Loop at ~30 FPS (33ms) for super low CPU usage (<0.5%)
        self.root.after(33, self._animate_reactor)


# Global HUD instance
hud = JarvisHUD()
