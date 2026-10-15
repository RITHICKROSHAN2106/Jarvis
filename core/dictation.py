"""
JARVIS Live Window Dictation Engine (Phase 5)
Provides continuous speech-to-typing into any active window (Notepad, Word, VS Code, Browser, etc.).
Features:
  - Spoken punctuation conversion ("period", "comma", "question mark", "new line", etc.)
  - Real-time sentence capitalization
  - Voice editing commands ("scratch that", "delete line", "select all", "undo", "paste")
  - Ultra-fast clipboard injection via pyperclip (zero dropped characters, symbols & Unicode support)
"""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from typing import Any, Callable

import pyautogui
import pyperclip

pyautogui.FAILSAFE = False

logger = logging.getLogger("jarvis")

PUNCTUATION_PATTERNS = [
    (r"\bnew\s+paragraph\b", "\n\n"),
    (r"\bnew\s+line\b", "\n"),
    (r"\b(period|full\s+stop)\b", "."),
    (r"\bcomma\b", ","),
    (r"\bquestion\s+mark\b", "?"),
    (r"\bexclamation\s+(mark|point)\b", "!"),
    (r"\bcolon\b", ":"),
    (r"\bsemicolon\b", ";"),
    (r"\b(dash|hyphen)\b", "-"),
    (r"\bquote\b", '"'),
    (r"\bend\s+quote\b", '"'),
]


def format_spoken_text(text: str) -> str:
    """Convert spoken punctuation tokens, clean spacing, and auto-capitalize sentences."""
    t = text
    for pat, rep in PUNCTUATION_PATTERNS:
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)

    # Clean up whitespace before punctuation: "word ." -> "word."
    t = re.sub(r"\s+([,.:;?!])", r"\1", t)
    # Ensure space after punctuation if followed by word: "word.word" -> "word. word"
    t = re.sub(r"([,.:;?!])([A-Za-z0-9])", r"\1 \2", t)
    # Clean whitespace around newlines
    t = re.sub(r"[ \t]*\n[ \t]*", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)

    # Capitalize after sentence boundaries (^, ., !, ?, \n)
    def _cap(match: re.Match) -> str:
        return match.group(1) + match.group(2).upper()

    t = re.sub(r"(^|[.!?\n]\s*)([a-z])", _cap, t)
    return t


def execute_editing_command(spoken: str) -> tuple[bool, str]:
    """
    Check if utterance is a voice editing command.
    Returns (handled, description).
    """
    cmd = spoken.lower().strip(" .!?,")

    # 1. Delete last word (Scratch that)
    if cmd in ("scratch that", "delete last word", "delete word", "erase word", "backspace word"):
        pyautogui.hotkey("ctrl", "backspace")
        return True, "Deleted word"

    # 2. Delete whole line
    if cmd in ("delete line", "clear line", "erase line"):
        pyautogui.hotkey("shift", "home")
        pyautogui.press("backspace")
        return True, "Cleared line"

    # 3. Select All
    if cmd in ("select all", "highlight all"):
        pyautogui.hotkey("ctrl", "a")
        return True, "Selected all"

    # 4. Copy
    if cmd in ("copy that", "copy this", "copy selection", "copy text"):
        pyautogui.hotkey("ctrl", "c")
        return True, "Copied"

    # 5. Paste
    if cmd in ("paste that", "paste clipboard", "paste here"):
        pyautogui.hotkey("ctrl", "v")
        return True, "Pasted"

    # 6. Navigation / Formatting
    if cmd in ("press enter", "hit enter"):
        pyautogui.press("enter")
        return True, "Enter"

    if cmd in ("press tab", "indent"):
        pyautogui.press("tab")
        return True, "Tab"

    if cmd in ("undo typing", "undo text"):
        pyautogui.hotkey("ctrl", "z")
        return True, "Undo"

    return False, ""


def type_text(text: str) -> None:
    """Inject formatted text into the current foreground window via clipboard paste."""
    if not text:
        return
    try:
        pyperclip.copy(text + " ")
        time.sleep(0.02)
        pyautogui.hotkey("ctrl", "v")
    except Exception as e:
        logger.error(f"[dictation typing error] {e}")


def is_dictation_start(cmd: str) -> bool:
    c = cmd.lower().strip(" .!?,")
    return any(p in c for p in (
        "start dictation", "dictate mode", "take dictation",
        "begin dictation", "enable dictation", "open dictation"
    ))


def is_dictation_stop(cmd: str) -> bool:
    c = cmd.lower().strip(" .!?,")
    return any(p in c for p in (
        "stop dictation", "exit dictation", "finish dictation",
        "end dictation", "disable dictation", "close dictation"
    ))


def run_dictation_session(
    q: queue.Queue,
    stt: Any,
    speak_func: Callable[[str], None],
    record_func: Callable[..., Any],
    transcribe_func: Callable[..., str],
    beep_func: Callable[..., None],
    user_title: str = "boss",
) -> None:
    """
    Live interactive dictation session.
    Continuously listens and streams text directly into the active window.
    """
    speak_func(f"Dictation mode active, {user_title}. I'm listening. Say 'stop dictation' to exit.")
    time.sleep(0.3)

    consecutive_empty = 0
    while True:
        audio = record_func(q, max_wait=12.0, end_silence=0.60)
        if audio is None:
            consecutive_empty += 1
            if consecutive_empty >= 5:  # ~1 minute of total silence
                speak_func(f"Dictation timed out, {user_title}. Returning to standby.")
                break
            continue

        consecutive_empty = 0
        text = transcribe_func(stt, audio)
        if not text or not text.strip():
            continue

        clean = text.strip()
        logger.info(f"[Dictation Audio Received]: '{clean}'")

        # Check for stop command
        if is_dictation_stop(clean):
            beep_func(600, 0.05)
            speak_func(f"Dictation ended, {user_title}.")
            break

        # Check for voice editing commands
        handled, desc = execute_editing_command(clean)
        if handled:
            beep_func(1100, 0.03)
            logger.info(f"[Dictation Edit Executed]: {desc}")
            continue

        # Format and inject text into the active window
        formatted = format_spoken_text(clean)
        type_text(formatted)
        beep_func(800, 0.02)
