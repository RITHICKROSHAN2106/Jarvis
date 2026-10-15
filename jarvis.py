#!/usr/bin/env python3
"""
JARVIS - a Tony Stark style voice assistant for Windows 11.

Wakes on:  "Jarvis" (wake word)  OR  a double clap.
Pipeline:  mic -> wake/clap -> record -> Whisper (speech-to-text, local)
           -> built-in system & app control or LLM brain with action execution
           -> spoken reply (text-to-speech)

Capabilities:
  * Full app opening: Chrome, Edge, Calculator, Notepad, Word, Excel, PowerPoint,
                      VS Code, WhatsApp, Spotify, Settings, File Explorer, Task Manager, etc.
  * In-app control:
      - Type in Notepad ("write in notepad ...")
      - Math in Calculator ("calculate 88 plus 45 in calculator")
      - Search YouTube & Google ("play ... on youtube", "search for ...")
      - Window management (maximize, minimize, close, switch, show desktop, screenshot)
      - Audio & media control (volume up/down, mute, pause/play, next/prev track)
      - App closing ("close calculator", "close notepad", "close chrome", etc.)
  * System power control:
      - Shutdown ("shut down the system", "turn off the system", "shut the system")
      - Cancel shutdown ("cancel shutdown", "abort shutdown")
      - Restart ("restart computer", "reboot")
      - Lock screen ("lock screen", "lock computer")
      - Sleep ("put computer to sleep")

Run:          python jarvis.py
Tune claps:   python jarvis.py --levels
Test brain:   python jarvis.py --test-llm
List models:  python jarvis.py --models
"""
from __future__ import annotations

import argparse
import datetime as dt
import faulthandler
import html
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import io
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
import webbrowser
import hashlib
from collections import deque
from rapidfuzz import process, fuzz

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Guard against GUI/pythonw mode where sys.stdout/sys.stderr may be None
if sys.stdout is None:
    try:
        sys.stdout = open(os.path.join(LOG_DIR, "jarvis_stdout.log"), "a", encoding="utf-8", errors="replace")
    except Exception:
        sys.stdout = io.StringIO()

if sys.stderr is None:
    try:
        sys.stderr = open(os.path.join(LOG_DIR, "jarvis_stderr.log"), "a", encoding="utf-8", errors="replace")
    except Exception:
        sys.stderr = io.StringIO()

faulthandler.enable()

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Setup structured rotating logging to logs/jarvis.log
log_file = os.path.join(LOG_DIR, "jarvis.log")
log_handler = RotatingFileHandler(log_file, maxBytes=3 * 1024 * 1024, backupCount=5, encoding="utf-8")
log_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
log_handler.setFormatter(log_formatter)

logger = logging.getLogger("jarvis")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(log_handler)
    if sys.stdout and hasattr(sys.stdout, "write"):
        try:
            logger.addHandler(logging.StreamHandler(sys.stdout))
        except Exception:
            pass

import numpy as np
import sounddevice as sd
import pyautogui
import pyperclip
import yaml

pyautogui.FAILSAFE = False

# Import persistent memory & identity core, safety tools, and power event monitor
from core.memory import memory
from core.tools import tools
from core import dictation
from core.hud import hud
from core.power_events import power_events

# On Windows, sync any user environment variables (from setx) that might not be in the current process env
if platform.system() == "Windows":
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as _rkey:
            _idx = 0
            while True:
                try:
                    _k, _v, _ = winreg.EnumValue(_rkey, _idx)
                    if _k not in os.environ and _v:
                        os.environ[_k] = str(_v)
                    _idx += 1
                except OSError:
                    break
    except Exception:
        pass

# =============================== CONFIG ===============================
NAME = "Jarvis"
USER_TITLE = memory.title               # always "boss"
USER_NICKNAME = memory.nickname         # "Tony Stark"
USER_REALNAME = memory.real_name         # "Roshan" (only spoken when asked)

LLM_PROVIDER = os.environ.get("JARVIS_PROVIDER", "groq")
PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",              # free key: https://console.groq.com/keys
        "model": "openai/gpt-oss-20b",          # fast active model on Groq
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",            # free key: https://aistudio.google.com/apikey
        "model": "gemini-2.5-flash-lite",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key_env": None,
        "model": "qwen2.5:1.5b",
    },
    "anthropic": {
        "key_env": "ANTHROPIC_API_KEY",
        "model": "claude-haiku-5-5",
    },
}

WHISPER_MODEL = os.environ.get("JARVIS_WHISPER_MODEL", "small")  # multilingual local fallback
GROQ_STT_MODEL = os.environ.get("JARVIS_GROQ_STT_MODEL", "whisper-large-v3-turbo")

ENABLE_WAKE_WORD = True
ENABLE_CLAP = True
ENABLE_AUTO_GREETING = os.environ.get("JARVIS_AUTO_GREETING", "0") == "1"

# Wake word
WAKE_MODELS = ["hey_jarvis"]       # model recognizes both the short and long spoken forms
WAKE_THRESHOLD = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.25"))  # was 0.018 = fired on almost any sound
WAKE_CONFIRM_HITS = 2              # need this many above-threshold frames...
WAKE_CONFIRM_WINDOW = 4            # ...within this many recent frames (80 ms each)

# Quiet-behaviour settings (these stop him talking when nobody asked)
POST_SPEECH_HOLD = 1.0             # seconds he stays deaf after speaking (stops him hearing his own voice)
MIN_SPEECH_FRAMES = 3              # a click or cough is not speech; need ~0.25 s of speech-like sound
FIRST_WAIT = 8.0                   # seconds to wait for a command after waking
FOLLOWUP_WAIT = 5.0                # seconds to keep listening for a follow-up after a reply
QUIET_STANDBY = True               # "that's all" -> soft chime instead of saying "Standing by."
GREETING_COOLDOWN_HOURS = float(os.environ.get("JARVIS_GREETING_COOLDOWN_HOURS", "4"))

# Double clap tuning
CLAP_MIN_PEAK = 0.30               # a clap must be at least this loud (0.0 - 1.0)
CLAP_RATIO = 6.0                   # ...and this many times louder than background noise
CLAP_MIN_GAP = 0.20                # seconds between the two claps (min)
CLAP_MAX_GAP = 0.80                # seconds between the two claps (max)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as _config_file:
        _voice_config = (yaml.safe_load(_config_file) or {}).get("voice", {})
except (OSError, yaml.YAMLError, AttributeError):
    _voice_config = {}

VOICE_ENGINE = os.environ.get("JARVIS_VOICE_ENGINE", _voice_config.get("engine", "sapi")).lower()
VOICE_NAME = os.environ.get("JARVIS_VOICE_NAME", _voice_config.get("name", "en-GB-RyanNeural"))
VOICE_HINT = os.environ.get("JARVIS_SAPI_VOICE", "david")  # preferred Windows voice fallback
VOICE_RATE = os.environ.get("JARVIS_VOICE_RATE", _voice_config.get("rate", "+0%"))
VOICE_PITCH = os.environ.get("JARVIS_VOICE_PITCH", _voice_config.get("pitch", "+0Hz"))
VOICE_VOLUME = os.environ.get("JARVIS_VOICE_VOLUME", _voice_config.get("volume", "+0%"))
VOICE_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    _voice_config.get("cache_dir", os.path.join("data", "tts-cache")),
)
SPEECH_RATE = 175

SAMPLE_RATE = 16000
FRAME = 1280                       # 80 ms of audio per step
FRAME_SEC = FRAME / SAMPLE_RATE

SLEEP_PHRASES = ("that's all", "that is all", "thank you", "thanks", "go to sleep", "never mind", "stand by")
QUIT_PHRASES = (
    "quit jarvis", "exit jarvis", "shut down jarvis", "shutdown jarvis",
    "stop jarvis", "close jarvis"
)
pending_power_action: str | None = None

SYSTEM_PROMPT = (
    f"You are {NAME}, a brilliant, witty, high-tech AI assistant modeled after Tony Stark's JARVIS. "
    "Your replies are spoken aloud, so answer in one short sentence of plain spoken text: no markdown, no emojis, no code blocks, no JSON. "
    "Reply in the user's detected language, including mixed-language speech, unless the user explicitly requests another language. "
    "Do not repeat your previous reply. If the user's request is unclear, ask one brief clarifying question instead of guessing. "
    f"Address the user as '{USER_TITLE}'. "
    "All computer actions are executed by your system core. For actions not handled by a built-in command, emit one action tag using exactly one of: "
    "[ACTION: open_app APP], [ACTION: open_url URL], [ACTION: type TEXT], [ACTION: click X Y], "
    "[ACTION: double_click X Y], [ACTION: right_click X Y], [ACTION: move X Y], [ACTION: scroll_up], "
    "[ACTION: scroll_down], [ACTION: press KEY], [ACTION: volume_up], [ACTION: volume_down], [ACTION: mute], "
    "[ACTION: close_app APP], [ACTION: shutdown], [ACTION: restart], [ACTION: lock], or [ACTION: sleep]. "
    "Never invent coordinates. Current-information questions may include fresh web snippets; treat them as potentially incomplete and say when verification is needed. "
    "Only say an action is done if you emitted an action tag for it. "
    "Never add filler such as 'Got it' or 'All systems nominal'. "
    "If the user's words are noise, a stray fragment, or clearly not addressed to you, reply with exactly [IGNORE] and nothing else."
)

# Common websites
SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "claude": "https://claude.ai",
    "chatgpt": "https://chatgpt.com",
    "maps": "https://maps.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "spotify": "https://open.spotify.com",
    "netflix": "https://www.netflix.com",
    "reddit": "https://www.reddit.com",
    "wikipedia": "https://www.wikipedia.org",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "linkedin": "https://www.linkedin.com",
}
# ======================================================================


_GREETING_STAMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "last_greeting.txt")


def should_greet() -> bool:
    """Greet at most once per GREETING_COOLDOWN_HOURS so he doesn't chatter on every unlock."""
    try:
        with open(_GREETING_STAMP, encoding="utf-8") as f:
            if time.time() - float(f.read().strip()) < GREETING_COOLDOWN_HOURS * 3600:
                return False
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(_GREETING_STAMP), exist_ok=True)
        with open(_GREETING_STAMP, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass
    return True


_instance_lock = None  # keeps the lock alive for the life of the process


def acquire_single_instance() -> bool:
    """Only one Jarvis may run. Two copies hear each other's voice and talk over each other."""
    global _instance_lock
    try:
        if platform.system() == "Windows":
            import win32api
            import win32event
            import winerror

            _instance_lock = win32event.CreateMutex(None, False, "JARVIS_VOICE_ASSISTANT_SINGLE_INSTANCE")
            return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS
        import fcntl

        _instance_lock = open(os.path.join(LOG_DIR, "jarvis.lock"), "w")
        fcntl.flock(_instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError) as e:
        if platform.system() != "Windows":
            return False  # lock file is held by another running copy
        logger.warning(f"Single-instance check failed ({e}); continuing anyway.")
        return True
    except Exception as e:
        logger.warning(f"Single-instance check unavailable ({e}); continuing anyway.")
        return True


def to_float(frame: np.ndarray) -> np.ndarray:
    return frame.astype(np.float32) / 32768.0


# ------------------------------ Triggers ------------------------------
class DoubleClapDetector:
    """Detects two sharp loud sounds 0.2-0.8 s apart."""

    def __init__(self) -> None:
        self.t = 0.0
        self.baseline = 0.01
        self.first_clap_t: float | None = None
        self.cooldown_until = 0.0

    def reset(self) -> None:
        self.first_clap_t = None
        self.cooldown_until = self.t + 1.0

    def process(self, frame: np.ndarray) -> bool:
        x = to_float(frame)
        peak = float(np.max(np.abs(x)))
        rms = float(np.sqrt(np.mean(x * x)))
        self.t += FRAME_SEC

        is_clap = peak >= CLAP_MIN_PEAK and peak >= CLAP_RATIO * self.baseline
        if not is_clap:
            self.baseline = 0.97 * self.baseline + 0.03 * max(rms, 1e-4)

        if self.t < self.cooldown_until or not is_clap:
            if self.first_clap_t is not None and self.t - self.first_clap_t > CLAP_MAX_GAP:
                self.first_clap_t = None
            return False

        if self.first_clap_t is None:
            self.first_clap_t = self.t
            return False

        gap = self.t - self.first_clap_t
        if gap < CLAP_MIN_GAP:
            return False
        if gap <= CLAP_MAX_GAP:
            self.first_clap_t = None
            self.cooldown_until = self.t + 1.5
            return True
        self.first_clap_t = self.t
        return False


class WakeWord:
    """Offline wake-word detection using openWakeWord."""

    def __init__(self) -> None:
        from openwakeword.model import Model

        try:
            self.model = Model(wakeword_models=WAKE_MODELS, inference_framework="onnx")
        except Exception:
            from openwakeword.utils import download_models

            print("Downloading wake-word models (first run only)...")
            download_models()
            self.model = Model(wakeword_models=WAKE_MODELS, inference_framework="onnx")
        self.recent: deque = deque(maxlen=WAKE_CONFIRM_WINDOW)
        self.last_score = 0.0

    def heard(self, frame: np.ndarray) -> bool:
        scores = self.model.predict(frame)
        self.last_score = max(scores.values(), default=0.0)
        self.recent.append(self.last_score >= WAKE_THRESHOLD)
        # one lucky spike is not enough; a real "Hey Jarvis" keeps the score up for several frames
        return sum(self.recent) >= WAKE_CONFIRM_HITS

    def reset(self) -> None:
        self.recent.clear()
        if hasattr(self.model, "reset"):
            self.model.reset()


class EchoGuard:
    """Makes Jarvis deaf while he talks and for a moment afterwards, so he never hears himself."""

    def __init__(self, hold: float = POST_SPEECH_HOLD) -> None:
        self.hold = hold
        self.was_busy = False
        self.deaf_until = 0.0

    def check(self, busy: bool, now: float) -> tuple[bool, bool]:
        """Returns (skip_this_frame, speech_just_ended)."""
        if busy:
            self.was_busy = True
            return True, False
        if self.was_busy:
            self.was_busy = False
            self.deaf_until = now + self.hold
            return True, True
        return now < self.deaf_until, False


# ------------------------------- Audio --------------------------------
def drain(q: queue.Queue) -> None:
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


def beep(freq: float = 880, dur: float = 0.08) -> None:
    def _play():
        try:
            if platform.system() == "Windows":
                import winsound

                winsound.Beep(int(freq), int(dur * 1000))
                return
            t = np.linspace(0, dur, int(SAMPLE_RATE * dur), False)
            tone = (0.25 * np.sin(2 * np.pi * freq * t) * np.hanning(len(t))).astype(np.float32)
            sd.play(tone, SAMPLE_RATE)
            sd.wait()
        except Exception:
            pass

    threading.Thread(target=_play, daemon=True).start()


# ------------------------------ TTS Worker ------------------------------
class TTSWorker:
    """Persistent TTS worker with optional neural Edge voice and offline fallback."""

    def __init__(self, voice_hint: str = VOICE_HINT, rate: int = 1) -> None:
        self.q: queue.Queue = queue.Queue()
        self.voice_hint = voice_hint
        self.rate = rate
        self._busy = threading.Event()
        self._last_spoken = ""
        self._last_spoken_at = 0.0
        self.thread = threading.Thread(target=self._run, daemon=True, name="TTSWorker")
        self.thread.start()

    def _run(self) -> None:
        voice = None
        if platform.system() == "Windows":
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                voice = win32com.client.Dispatch("SAPI.SpVoice")
                voice.Rate = self.rate
                if self.voice_hint:
                    voices = voice.GetVoices()
                    for i in range(voices.Count):
                        v = voices.Item(i)
                        if self.voice_hint.lower() in v.GetDescription().lower():
                            voice.Voice = v
                            break
            except Exception as e:
                logger.warning(f"SAPI TTS initialization failed: {e}. Falling back to pyttsx3.")
                voice = None

        edge_enabled = VOICE_ENGINE in {"edge", "edge-tts", "neural"}
        if edge_enabled:
            logger.info("Neural TTS enabled: %s (%s, %s, %s)", VOICE_NAME, VOICE_RATE, VOICE_PITCH, VOICE_VOLUME)

        while True:
            item = self.q.get()
            if item is None:
                break
            text, block_event = item
            self._busy.set()
            clean_text = (
                text.replace("\u2011", "-")
                .replace("\u2013", "-")
                .replace("\u2014", "--")
                .replace("’", "'")
                .replace("‘", "'")
                .replace("“", '"')
                .replace("”", '"')
                .replace("…", "...")
                .strip()
            )
            if clean_text:
                try:
                    print(f"{NAME}: {clean_text}", flush=True)
                except Exception:
                    pass

                if edge_enabled and self._speak_edge(clean_text):
                    pass
                elif voice is not None:
                    try:
                        voice.Speak(clean_text, 0)
                    except Exception as e:
                        logger.error(f"[SAPI speak error] {e}")
                else:
                    try:
                        import pyttsx3
                        engine = pyttsx3.init()
                        engine.setProperty("rate", SPEECH_RATE)
                        engine.say(clean_text)
                        engine.runAndWait()
                        engine.stop()
                    except Exception as e:
                        logger.error(f"[pyttsx3 speak error] {e}")

            if block_event:
                block_event.set()
            self.q.task_done()
            if self.q.empty():
                self._busy.clear()

    @staticmethod
    def _speak_edge(text: str) -> bool:
        """Synthesize and play one sentence; return False so the caller can use SAPI."""
        try:
            import asyncio
            from edge_tts import Communicate
            from playsound3 import playsound

            os.makedirs(VOICE_CACHE_DIR, exist_ok=True)
            digest = hashlib.sha256(
                f"{VOICE_NAME}|{VOICE_RATE}|{VOICE_PITCH}|{VOICE_VOLUME}|{text}".encode("utf-8")
            ).hexdigest()
            audio_path = os.path.join(VOICE_CACHE_DIR, f"{digest}.mp3")

            async def synthesize() -> None:
                await Communicate(
                    text,
                    VOICE_NAME,
                    rate=VOICE_RATE,
                    pitch=VOICE_PITCH,
                    volume=VOICE_VOLUME,
                ).save(audio_path)

            if not os.path.exists(audio_path):
                asyncio.run(synthesize())
            playsound(audio_path, block=True)
            return True
        except Exception as exc:
            logger.warning("Neural TTS failed; using local fallback: %s", exc)
            return False

    def is_busy(self) -> bool:
        return self._busy.is_set()

    def speak(self, text: str, block: bool = True) -> None:
        if not text or not text.strip():
            return
        normalized = re.sub(r"\s+", " ", text.strip()).lower()
        now = time.monotonic()
        if normalized == self._last_spoken and now - self._last_spoken_at < 4.0:
            logger.info("Suppressed duplicate spoken response.")
            return
        self._last_spoken = normalized
        self._last_spoken_at = now
        block_event = threading.Event() if block else None
        self.q.put((text, block_event))
        if block and block_event:
            block_event.wait()

    def wait(self) -> None:
        self.q.join()


tts_worker = TTSWorker()


def speak(text: str, block: bool = True) -> None:
    """Spoken reply via persistent TTS worker."""
    tts_worker.speak(text, block=block)


def record_utterance(q: queue.Queue, max_wait=10.0, max_len=15.0, end_silence=0.55) -> np.ndarray | None:
    """Record until the user stops talking. Returns float32 mono audio, or None if nobody really spoke."""
    preroll: deque = deque(maxlen=10)
    chunks: list[np.ndarray] = []
    noise: list[float] = []
    thresh = 0.012
    started = False
    voiced = 0  # grows on speech-like frames, shrinks on quiet ones: a single click never reaches MIN_SPEECH_FRAMES
    waited = duration = silent = 0.0

    while True:
        x = to_float(q.get())
        rms = float(np.sqrt(np.mean(x * x)))
        peak = float(np.max(np.abs(x)))

        if not started:
            if len(noise) < 5:
                noise.append(rms)
                thresh = max(0.010, min(0.022, 1.4 * min(noise)))
            loud = (rms >= thresh and peak >= 0.038) or peak >= 0.052
            voiced = voiced + 1 if loud else max(0, voiced - 1)
            preroll.append(x)
            if voiced >= MIN_SPEECH_FRAMES:
                started = True
                print("[Hearing speech...]", flush=True)
                chunks.extend(preroll)
            else:
                waited += FRAME_SEC
                if waited >= max_wait:
                    return None
        else:
            chunks.append(x)
            duration += FRAME_SEC
            silent = silent + FRAME_SEC if (rms < thresh and peak < 0.028) else 0.0
            if silent >= end_silence or duration >= max_len:
                beep(1200, 0.02)
                break

    return np.concatenate(chunks)


STT_VOCAB = (
    "Jarvis", "Chrome", "Edge", "Brave", "Calculator", "Notepad", "VS Code", "WhatsApp", "Spotify",
    "YouTube", "Excel", "Word", "PowerPoint", "volume", "screenshot", "shutdown", "restart",
)

# Things Whisper "hears" in silence/noise. Never treat these as commands.
_NOISE_PHRASES = {
    "you", "bye", "bye bye", "thank you", "thanks", "thank you very much", "thanks for watching",
    "okay", "ok", "oh", "uh", "um", "hmm", "mm", "ah", "huh", "bop", "boop", "beep", "yeah", "yep",
    "yes", "no", "so", "the", "a", "i", "and", "right",
}
_SHORT_REPLIES = {"yes", "no", "yeah", "yep", "okay", "ok", "confirm", "cancel", "do it", "go ahead", "stop", "right"}
_NOISE_REGEX = re.compile(r"(thanks? for watching|subtitles? by|amara\.org|like and subscribe|www\.|\.com\b|字幕|ご視聴)", re.I)
_MULTILINGUAL_NOISE_PHRASES = {
    "धन्यवाद", "धन्यवाद देखने के लिए", "வணக்கம்", "நன்றி", "பார்த்ததற்கு நன்றி",
    "ధన్యవాదాలు", "നന്ദി", "ಧನ್ಯವಾದಗಳು",
}


def segment_is_junk(no_speech_prob, avg_logprob, compression_ratio) -> bool:
    """Whisper's own standard rules for 'this segment is probably not real speech'."""
    no_speech_prob = no_speech_prob or 0.0
    avg_logprob = avg_logprob or 0.0
    compression_ratio = compression_ratio or 0.0
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return True
    return compression_ratio > 2.4  # repetitive gibberish


def is_noise_transcript(text: str, allow_short_replies: bool = False, dictation: bool = False) -> bool:
    """True if a transcript is almost certainly a Whisper hallucination rather than a command."""
    t = re.sub(r"[^\w\s']", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return True
    if text.strip().lower() in {phrase.lower() for phrase in _MULTILINGUAL_NOISE_PHRASES}:
        return True
    if len(re.sub(r"[\W\d_]", "", t)) < 2 or _NOISE_REGEX.search(text):
        return True
    words = t.split()
    vocab = {w.lower() for v in STT_VOCAB for w in v.split()}
    if len(words) >= 3 and sum(w in vocab for w in words) / len(words) >= 0.8:
        return True  # Whisper just read its own vocabulary prompt back
    if dictation:
        return t in {"you", "bop", "boop"}
    if allow_short_replies and t in _SHORT_REPLIES:
        return False
    return t in _NOISE_PHRASES


_groq_stt_client = None
last_detected_language = ""


def get_groq_stt_client():
    global _groq_stt_client
    if _groq_stt_client is None:
        groq_key = os.environ.get("GROQ_API_KEY")
        if groq_key:
            from openai import OpenAI
            _groq_stt_client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=groq_key)
    return _groq_stt_client


def transcribe(stt, audio: np.ndarray) -> str:
    """Hybrid multilingual STT with cloud language detection and local fallback."""
    global last_detected_language
    prompt = ", ".join(STT_VOCAB)  # vocabulary only: instruction-style prompts make Whisper invent words from noise

    if float(np.sqrt(np.mean(np.square(audio)))) < 0.006:
        logger.info("Ignored near-silent audio frame.")
        return ""

    # 1. Try Groq Cloud Whisper first for ultra-fast STT (~800ms)
    groq_client = get_groq_stt_client()
    if groq_client:
        try:
            int_audio = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(int_audio.tobytes())
            buf.seek(0)
            buf.name = "audio.wav"

            t0 = time.perf_counter()
            resp = groq_client.audio.transcriptions.create(
                model=GROQ_STT_MODEL,
                file=buf,
                prompt=prompt,
                temperature=0.0,
                response_format="verbose_json",
            )
            lat = (time.perf_counter() - t0) * 1000
            last_detected_language = str(getattr(resp, "language", "") or "").strip().lower()
            text = (getattr(resp, "text", "") or "").strip()
            segments = getattr(resp, "segments", None) or []
            if segments:  # drop segments Whisper itself is unsure about (the usual source of invented words)
                kept = []
                for seg in segments:
                    get = (lambda k, seg=seg: seg.get(k)) if isinstance(seg, dict) else (lambda k, seg=seg: getattr(seg, k, None))
                    if segment_is_junk(get("no_speech_prob"), get("avg_logprob"), get("compression_ratio")):
                        logger.info(f"Dropped low-confidence segment: '{get('text')}'")
                        continue
                    kept.append((get("text") or "").strip())
                text = " ".join(k for k in kept if k).strip()
            logger.info(
                f"Groq Cloud STT ({lat:.0f}ms, language={last_detected_language or 'unknown'}): '{text}'"
            )
            return text
        except Exception as e:
            logger.warning(f"Groq STT failed ({e}), falling back to local Whisper...")

    # 2. Fallback to local faster-whisper
    t0 = time.perf_counter()
    segments, info = stt.transcribe(
        audio,
        language=None,
        beam_size=4,
        initial_prompt=prompt,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        no_speech_threshold=0.6,
        condition_on_previous_text=False,
    )
    lat = (time.perf_counter() - t0) * 1000
    text = " ".join(
        seg.text.strip()
        for seg in segments
        if not segment_is_junk(seg.no_speech_prob, seg.avg_logprob, seg.compression_ratio)
    ).strip()
    detected = getattr(info, "language", "") or "auto"
    last_detected_language = str(detected).lower()
    logger.info(f"Local Whisper STT ({lat:.0f}ms, language={last_detected_language}): '{text}'")
    return text


def normalize_command_text(text: str) -> str:
    """Correct frequent speech-recognition variants before local command matching."""
    normalized = re.sub(r"\s+", " ", text.strip())
    replacements = (
        (r"\b(javis|javiss|jarvis's|jarvises)\b", "Jarvis"),
        (r"\b(chrome|chrom)\b", "Chrome"),
        (r"\b(what's app|what sap|what app)\b", "WhatsApp"),
        (r"\b(v\s*scode|vs code)\b", "VS Code"),
        (r"\b(calci|calcu|calc)\b", "Calculator"),
        (r"\b(not pad|not bad|note pad)\b", "Notepad"),
        (r"\b(double tap)\b", "double click"),
        (r"\b(right tap)\b", "right click"),
        (r"\b(play pause|play/pause)\b", "playpause"),
    )
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


# ----------------------- Windows Automation Core -----------------------
def get_registered_app_path(name: str) -> str | None:
    """Look up the exact executable path for any Windows application via the registry."""
    if platform.system() != "Windows":
        return None
    name_map = {
        "chrome": "chrome.exe",
        "google chrome": "chrome.exe",
        "browser": "chrome.exe",
        "edge": "msedge.exe",
        "microsoft edge": "msedge.exe",
        "word": "WINWORD.EXE",
        "winword": "WINWORD.EXE",
        "ms word": "WINWORD.EXE",
        "microsoft word": "WINWORD.EXE",
        "excel": "EXCEL.EXE",
        "ms excel": "EXCEL.EXE",
        "microsoft excel": "EXCEL.EXE",
        "powerpoint": "POWERPNT.EXE",
        "ppt": "POWERPNT.EXE",
        "vs code": "Code.exe",
        "vscode": "Code.exe",
        "code": "Code.exe",
        "notepad": "notepad.exe",
        "task manager": "taskmgr.exe",
        "taskmgr": "taskmgr.exe",
        "cmd": "cmd.exe",
        "terminal": "wt.exe",
        "paint": "mspaint.exe",
    }
    clean = name.lower().strip()
    exe_name = name_map.get(clean, f"{clean}.exe")
    import winreg

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}") as key:
                val, _ = winreg.QueryValueEx(key, "")
                if val and os.path.exists(val):
                    return val
        except OSError:
            pass
    return None


def bring_to_front(app_hint: str) -> bool:
    """Ensure the target app window is restored and forced to the active top-most foreground."""
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        user32.AllowSetForegroundWindow(-1)
        h_desk = user32.OpenDesktopW("Default", 0, False, 0x10000000)
        found = []
        hint = app_hint.lower()

        def _cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, buf, 512)
                title = buf.value
                if title and hint in title.lower():
                    found.append((hwnd, title))
            return True

        proc = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)(_cb)
        user32.EnumDesktopWindows(h_desk, proc, 0)
        for hwnd, _ in found:
            # Simulate Alt keypress to unlock SetForegroundWindow
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            # Force window to top of Z-order
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0040)
            time.sleep(0.04)
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0040)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.SwitchToThisWindow(hwnd, True)
        return len(found) > 0
    except Exception as e:
        print(f"[focus error] {e}", flush=True)
        return False


def open_app(name: str) -> bool:
    """Universal app opener for Windows 11 with fallback chains and speech aliases."""
    clean = name.lower().strip()
    system = platform.system()

    if system == "Windows":
        # Phonetic / speech recognition aliases for Chrome
        if clean in ("chrome", "google chrome", "browser", "crawl", "crow", "clone", "chrom", "crome"):
            cpath = get_registered_app_path("chrome")
            if cpath:
                subprocess.Popen([cpath, "--new-window"])
                time.sleep(0.6)
                bring_to_front("Chrome")
                return True
            subprocess.Popen("start chrome", shell=True)
            time.sleep(0.6)
            bring_to_front("Chrome")
            return True

        if clean in ("calc", "calculator", "calci", "calcu"):
            subprocess.Popen("start calc.exe", shell=True)
            time.sleep(0.5)
            bring_to_front("Calculator")
            return True

        if clean in ("notepad", "notes", "note pad", "not pad", "not bad"):
            subprocess.Popen("start notepad.exe", shell=True)
            time.sleep(0.4)
            bring_to_front("Notepad")
            return True

        if clean in ("file explorer", "explorer", "my computer", "this pc", "files"):
            subprocess.Popen("explorer.exe")
            time.sleep(0.4)
            bring_to_front("Explorer")
            return True

        if clean in ("downloads",):
            subprocess.Popen("explorer.exe shell:Downloads")
            time.sleep(0.4)
            bring_to_front("Downloads")
            return True

        if clean in ("documents", "my documents"):
            subprocess.Popen("explorer.exe shell:Personal")
            time.sleep(0.4)
            bring_to_front("Documents")
            return True

        if clean in ("desktop",):
            subprocess.Popen("explorer.exe shell:Desktop")
            time.sleep(0.4)
            bring_to_front("Desktop")
            return True

        if clean in ("settings", "windows settings", "pc settings", "setting"):
            subprocess.Popen("start ms-settings:", shell=True)
            time.sleep(0.5)
            bring_to_front("Settings")
            return True

        if clean in ("camera", "webcam"):
            subprocess.Popen("start microsoft.windows.camera:", shell=True)
            time.sleep(0.5)
            bring_to_front("Camera")
            return True

        if clean in ("task manager", "taskmgr", "monitor", "the monitor", "activity monitor"):
            subprocess.Popen("start taskmgr.exe", shell=True)
            time.sleep(0.5)
            bring_to_front("Task Manager")
            return True

        if clean in ("whatsapp",):
            subprocess.Popen("start whatsapp:", shell=True)
            time.sleep(0.5)
            bring_to_front("WhatsApp")
            return True

        if clean in ("spotify",):
            subprocess.Popen("start spotify:", shell=True)
            time.sleep(0.5)
            bring_to_front("Spotify")
            return True

        if clean in ("edge", "microsoft edge"):
            epath = get_registered_app_path("edge")
            if epath:
                subprocess.Popen([epath])
                time.sleep(0.5)
                bring_to_front("Edge")
                return True
            subprocess.Popen("start msedge", shell=True)
            time.sleep(0.5)
            bring_to_front("Edge")
            return True

        p = get_registered_app_path(clean)
        if p:
            subprocess.Popen([p])
            time.sleep(0.5)
            bring_to_front(clean)
            return True

        try:
            subprocess.Popen(f'start "" "{clean}"', shell=True)
            time.sleep(0.4)
            bring_to_front(clean)
            return True
        except Exception:
            return False

    return False


def open_url(url: str) -> bool:
    """Open URL in Chrome or default browser and bring to front."""
    if platform.system() == "Windows":
        cpath = get_registered_app_path("chrome")
        if cpath:
            try:
                subprocess.Popen([cpath, "--new-window", url])
                time.sleep(0.7)
                bring_to_front("Chrome")
                bring_to_front("YouTube")
                return True
            except Exception:
                pass
        try:
            os.startfile(url)
            time.sleep(0.7)
            bring_to_front("YouTube")
            bring_to_front("Chrome")
            return True
        except Exception:
            pass
    try:
        webbrowser.open(url, new=2)
        return True
    except Exception:
        return False


def play_song(query: str) -> str:
    """Find the top YouTube video for the song query and autoplay it immediately."""
    import urllib.request
    import urllib.parse
    import re

    clean_query = query.strip()
    for p in ("a song called ", "a song by ", "the song ", "a song ", "some music ", "music ", "song "):
        if clean_query.lower().startswith(p):
            clean_query = clean_query[len(p):].strip()

    if not clean_query or clean_query.lower() in ("a song", "some music", "music", "song"):
        clean_query = "top hits music"

    encoded = urllib.parse.quote_plus(clean_query + " song")
    search_url = f"https://www.youtube.com/results?search_query={encoded}"
    video_url = search_url

    try:
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        vids = re.findall(r"/watch\?v=([a-zA-Z0-9_-]{11})", html)
        if vids:
            video_url = f"https://www.youtube.com/watch?v={vids[0]}"
    except Exception as e:
        print(f"[yt search error] {e}")

    open_url(video_url)
    return f"Playing {clean_query} for you, {USER_TITLE}."


def control_pointer(action: str, x: int | None = None, y: int | None = None, amount: int = 3) -> str:
    """Perform explicit mouse actions requested by voice or the language core."""
    if action in ("click", "double_click", "right_click", "move") and (x is None or y is None):
        return "Please provide screen coordinates, boss."
    if action == "click":
        pyautogui.click(x, y)
        return f"Clicked at {x}, {y}, {USER_TITLE}."
    if action == "double_click":
        pyautogui.doubleClick(x, y, interval=0.12)
        return f"Double-clicked at {x}, {y}, {USER_TITLE}."
    if action == "right_click":
        pyautogui.rightClick(x, y)
        return f"Right-clicked at {x}, {y}, {USER_TITLE}."
    if action == "move":
        pyautogui.moveTo(x, y, duration=0.15)
        return f"Moved the pointer to {x}, {y}, {USER_TITLE}."
    if action == "scroll_up":
        pyautogui.scroll(abs(amount))
        return f"Scrolled up, {USER_TITLE}."
    if action == "scroll_down":
        pyautogui.scroll(-abs(amount))
        return f"Scrolled down, {USER_TITLE}."
    return "Unknown pointer action."


def type_active_window(text: str) -> str:
    """Paste text into the current foreground window without dropping characters."""
    clean = text.strip()
    if not clean:
        return f"Tell me what to type, {USER_TITLE}."
    pyperclip.copy(clean)
    pyautogui.hotkey("ctrl", "v")
    return f"Typed that into the active window, {USER_TITLE}."


def press_key(key: str) -> str:
    """Press a named keyboard key in the current foreground window."""
    aliases = {"escape": "esc", "return": "enter", "space bar": "space", "back space": "backspace"}
    clean = aliases.get(key.lower().strip(), key.lower().strip())
    allowed = {"enter", "esc", "tab", "space", "backspace", "delete", "home", "end", "up", "down", "left", "right"}
    if clean not in allowed:
        return f"I can press enter, escape, tab, space, backspace, delete, home, end, or an arrow key, {USER_TITLE}."
    pyautogui.press(clean)
    return f"Pressed {clean}, {USER_TITLE}."


def needs_live_context(text: str) -> bool:
    """Identify questions whose answer is likely to change after training time."""
    terms = ("live", "latest", "current", "today", "right now", "news", "weather", "score", "result", "event", "schedule")
    lowered = text.lower()
    return any(term in lowered for term in terms)


def fetch_live_context(query: str) -> str:
    """Fetch a few current search snippets for time-sensitive questions."""
    search_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    try:
        request = urllib.request.Request(search_url, headers={"User-Agent": "JARVIS/1.0"})
        with urllib.request.urlopen(request, timeout=5) as response:
            page = response.read().decode("utf-8", errors="replace")
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</div>', page, re.S)
        cleaned = []
        for first, second in snippets[:5]:
            text = re.sub(r"<[^>]+>", " ", first or second)
            text = re.sub(r"\s+", " ", html.unescape(text)).strip()
            if text:
                cleaned.append(text)
        return "\n".join(f"- {item}" for item in cleaned)
    except Exception as exc:
        logger.warning("Live search failed: %s", exc)
        return ""



def close_app(name: str) -> bool:
    """Close a known Windows app and report whether a process was actually terminated."""
    clean = re.sub(r"\s+(app|application|program|window)$", "", name.lower().strip())
    if platform.system() == "Windows":
        proc_map = {
            "calculator": ["CalculatorApp.exe", "calc.exe"],
            "calc": ["CalculatorApp.exe", "calc.exe"],
            "notepad": ["notepad.exe"],
            "chrome": ["chrome.exe"],
            "google chrome": ["chrome.exe"],
            "browser": ["chrome.exe", "msedge.exe"],
            "edge": ["msedge.exe"],
            "microsoft edge": ["msedge.exe"],
            "word": ["WINWORD.EXE"],
            "winword": ["WINWORD.EXE"],
            "excel": ["EXCEL.EXE"],
            "powerpoint": ["POWERPNT.EXE"],
            "ppt": ["POWERPNT.EXE"],
            "code": ["Code.exe"],
            "vscode": ["Code.exe"],
            "vs code": ["Code.exe"],
            "whatsapp": ["WhatsApp.exe"],
            "spotify": ["Spotify.exe"],
            "explorer": ["explorer.exe"],
            "file explorer": ["explorer.exe"],
            "task manager": ["Taskmgr.exe"],
            "taskmgr": ["Taskmgr.exe"],
            "terminal": ["WindowsTerminal.exe", "wt.exe"],
            "paint": ["mspaint.exe"],
            "photos": ["Microsoft.Photos.exe"],
            "vlc": ["vlc.exe"],
            "zoom": ["Zoom.exe"],
            "teams": ["ms-teams.exe", "Teams.exe"],
        }
        targets = proc_map.get(clean)
        if not targets:
            return False
        closed = False
        for tgt in targets:
            result = subprocess.run(
                ["taskkill.exe", "/f", "/im", tgt],
                capture_output=True,
                text=True,
                check=False,
            )
            closed = closed or result.returncode == 0
        return closed
    return False


def type_in_calc(text: str) -> str:
    """Perform math directly in the Windows Calculator app by typing keypresses."""
    try:
        open_app("calculator")
        time.sleep(0.4)
        bring_to_front("Calculator")
        s = text.lower()
        for word, sym in (
            ("plus", "+"), ("add", "+"), ("minus", "-"), ("subtract", "-"),
            ("times", "*"), ("multiplied by", "*"), ("into", "*"),
            ("divided by", "/"), ("divide by", "/"), ("over", "/")
        ):
            s = s.replace(word, sym)
        tokens = re.findall(r"[\d\+\-\*\/\.]+", s)
        expr = "".join(tokens)
        if expr:
            pyautogui.press("esc")
            time.sleep(0.1)
            pyautogui.typewrite(expr + "=", interval=0.08)
            ans = eval(expr)
            return f"Calculated {expr} in Calculator. The answer is {ans}, {USER_TITLE}."
    except Exception as e:
        print(f"[calc error] {e}")
    return f"Opened Calculator for you, {USER_TITLE}."


def type_in_notepad(text: str) -> str:
    """Type dictated text directly into Notepad."""
    try:
        open_app("notepad")
        time.sleep(0.4)
        bring_to_front("Notepad")
        time.sleep(0.2)
        pyautogui.typewrite(text + "\n", interval=0.03)
        return f"Written into Notepad, {USER_TITLE}."
    except Exception as e:
        print(f"[notepad error] {e}")
        return f"Opened Notepad for you, {USER_TITLE}."


def control_window(action: str) -> str:
    """Window management actions."""
    if action == "maximize":
        pyautogui.hotkey("win", "up")
        return f"Window maximized, {USER_TITLE}."
    if action == "minimize":
        pyautogui.hotkey("win", "down")
        return f"Window minimized, {USER_TITLE}."
    if action == "close":
        pyautogui.hotkey("alt", "f4")
        return f"Closed active window, {USER_TITLE}."
    if action == "close_tab":
        pyautogui.hotkey("ctrl", "w")
        return f"Closed the active tab, {USER_TITLE}."
    if action == "new_tab":
        pyautogui.hotkey("ctrl", "t")
        return f"Opened a new tab, {USER_TITLE}."
    if action == "back":
        pyautogui.hotkey("alt", "left")
        return f"Went back, {USER_TITLE}."
    if action == "forward":
        pyautogui.hotkey("alt", "right")
        return f"Went forward, {USER_TITLE}."
    if action == "refresh":
        pyautogui.press("f5")
        return f"Refreshed the active page, {USER_TITLE}."
    if action == "find":
        pyautogui.hotkey("ctrl", "f")
        return f"Opened find in the active app, {USER_TITLE}."
    if action == "save":
        pyautogui.hotkey("ctrl", "s")
        return f"Saved the active document, {USER_TITLE}."
    if action == "copy":
        pyautogui.hotkey("ctrl", "c")
        return f"Copied the selection, {USER_TITLE}."
    if action == "paste":
        pyautogui.hotkey("ctrl", "v")
        return f"Pasted the clipboard, {USER_TITLE}."
    if action == "select_all":
        pyautogui.hotkey("ctrl", "a")
        return f"Selected all, {USER_TITLE}."
    if action == "switch":
        pyautogui.hotkey("alt", "tab")
        return f"Switched window, {USER_TITLE}."
    if action == "desktop":
        pyautogui.hotkey("win", "d")
        return f"Showing desktop, {USER_TITLE}."
    if action == "screenshot":
        pyautogui.hotkey("win", "prtsc")
        return f"Screenshot captured, {USER_TITLE}."
    return "Action completed."
def find_in_active_window(query: str) -> str:
    """Open the active app's find box and enter a spoken search term."""
    clean = query.strip()
    pyautogui.hotkey("ctrl", "f")
    if clean:
        type_active_window(clean)
        pyautogui.press("enter")
        return f"Searched the active app for {clean}, {USER_TITLE}."
    return f"Opened find in the active app, {USER_TITLE}."


def control_volume(action: str) -> str:
    """Volume and media controls."""
    if action == "up":
        pyautogui.press("volumeup", presses=5)
        return f"Volume increased, {USER_TITLE}."
    if action == "down":
        pyautogui.press("volumedown", presses=5)
        return f"Volume decreased, {USER_TITLE}."
    if action == "mute":
        pyautogui.press("volumemute")
        return f"Toggled mute, {USER_TITLE}."
    if action == "playpause":
        pyautogui.press("playpause")
        return f"Toggled playback, {USER_TITLE}."
    if action == "next":
        pyautogui.press("nexttrack")
        return f"Next track, {USER_TITLE}."
    if action == "prev":
        pyautogui.press("prevtrack")
        return f"Previous track, {USER_TITLE}."
    return "Volume adjusted."


def system_power(action: str, confirmed: bool = False) -> str:
    """Windows system power actions."""
    if platform.system() != "Windows":
        return "Power commands are only supported on Windows."

    if action in {"shutdown", "restart", "lock", "sleep", "hibernate", "logoff"} and not confirmed:
        return request_power_confirmation(action)

    if action == "shutdown":
        subprocess.Popen("shutdown /s /t 20", shell=True)
        return f"Initiating system shutdown in 20 seconds, {USER_TITLE}. Say 'cancel shutdown' to abort."
    if action == "cancel_shutdown":
        subprocess.Popen("shutdown /a", shell=True)
        return f"System shutdown aborted, {USER_TITLE}."
    if action == "restart":
        subprocess.Popen("shutdown /r /t 20", shell=True)
        return f"Restarting system in 20 seconds, {USER_TITLE}."
    if action == "lock":
        subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
        return f"Workstation locked, {USER_TITLE}."
    if action == "sleep":
        subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
        return f"Putting computer to sleep, {USER_TITLE}."
    if action == "hibernate":
        subprocess.Popen(["shutdown.exe", "/h"])
        return f"Hibernating the computer, {USER_TITLE}."
    if action == "logoff":
        subprocess.Popen(["shutdown.exe", "/l"])
        return f"Signing out, {USER_TITLE}."
    return "Command executed."


def request_power_confirmation(action: str) -> str:
    """Stage a system-changing action until the next voice turn confirms it."""
    global pending_power_action
    pending_power_action = action
    labels = {
        "shutdown": "shut down the computer",
        "restart": "restart the computer",
        "lock": "lock the workstation",
        "sleep": "put the computer to sleep",
        "hibernate": "hibernate the computer",
        "logoff": "sign out of Windows",
    }
    return f"Confirmation required to {labels.get(action, action)}, {USER_TITLE}. Say confirm or cancel."


def handle_power_confirmation(text: str) -> str | None:
    """Resolve a staged power action from a concise confirmation or cancellation."""
    global pending_power_action
    if pending_power_action is None:
        return None
    normalized = re.sub(r"[^a-z ]", "", text.lower()).strip()
    if re.search(r"\b(confirm|confirmed|yes|proceed)\b", normalized) or "do it" in normalized:
        action = pending_power_action
        pending_power_action = None
        return system_power(action, confirmed=True)
    if re.search(r"\b(cancel|no|abort|stop)\b", normalized):
        pending_power_action = None
        return f"Power action cancelled, {USER_TITLE}."
    pending_power_action = None
    return f"I did not hear a confirmation, {USER_TITLE}. The pending action was cancelled."


def execute_action(action_str: str) -> str | None:
    """Execute an action requested either locally or by the LLM Brain action tags."""
    action_str = action_str.strip()
    parts = action_str.split(maxsplit=1)
    verb = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if verb == "open_app":
        open_app(arg)
        return f"Opening {arg}, {USER_TITLE}."
    if verb == "open_url":
        open_url(arg)
        return f"Opening {arg}, {USER_TITLE}."
    if verb == "search_google":
        open_url("https://www.google.com/search?q=" + urllib.parse.quote_plus(arg))
        return f"Searching Google for {arg}, {USER_TITLE}."
    if verb == "search_youtube":
        open_url("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(arg))
        return f"Pulling up {arg} on YouTube, {USER_TITLE}."
    if verb == "type_notepad":
        return type_in_notepad(arg)
    if verb in ("type", "type_text"):
        return type_active_window(arg)
    if verb in ("click", "double_click", "right_click", "move"):
        coords = re.findall(r"-?\d+", arg)
        if len(coords) < 2:
            return "Please provide x and y screen coordinates, boss."
        return control_pointer(verb, int(coords[0]), int(coords[1]))
    if verb in ("scroll_up", "scroll_down"):
        return control_pointer(verb)
    if verb == "press":
        return press_key(arg)
    if verb == "calc_math":
        return type_in_calc(arg)
    if verb == "close_app":
        return f"Closed {arg}, {USER_TITLE}." if close_app(arg) else f"I could not find {arg} running, {USER_TITLE}."
    if verb == "close_window":
        return control_window("close")
    if verb in ("close_tab", "new_tab", "back", "forward", "refresh", "find", "save", "copy", "paste", "select_all"):
        return find_in_active_window(arg) if verb == "find" else control_window(verb)
    if verb == "maximize":
        return control_window("maximize")
    if verb == "minimize":
        return control_window("minimize")
    if verb == "volume_up":
        return control_volume("up")
    if verb == "volume_down":
        return control_volume("down")
    if verb == "mute":
        return control_volume("mute")
    if verb == "shutdown":
        return system_power("shutdown")
    if verb == "cancel_shutdown":
        return system_power("cancel_shutdown")
    if verb == "restart":
        return system_power("restart")
    if verb == "delete_file":
        return tools.delete_file(arg)
    if verb == "read_file":
        return tools.read_file(arg)
    if verb == "write_file":
        fparts = arg.split(maxsplit=1)
        fpath = fparts[0] if fparts else ""
        fcontent = fparts[1] if len(fparts) > 1 else ""
        return tools.write_file(fpath, fcontent)
    if verb == "list_dir":
        return tools.list_dir(arg or ".")
    if verb == "lock":
        return system_power("lock")
    if verb == "sleep":
        return system_power("sleep")
    if verb == "hibernate":
        return system_power("hibernate")
    if verb in ("logoff", "sign_out"):
        return system_power("logoff")
    if verb == "system_stats":
        return tools.get_system_stats()
    return None


def action_is_requested(user_text: str, action_str: str) -> bool:
    """Require the user's words to support any LLM-generated computer action."""
    user = user_text.lower()
    parts = action_str.lower().split(maxsplit=1)
    verb = parts[0] if parts else ""
    requirements = {
        "open_app": ("open", "launch", "start", "run"),
        "open_url": ("open", "go to", "visit", "navigate"),
        "type": ("type", "write", "enter", "dictate"),
        "type_text": ("type", "write", "enter", "dictate"),
        "click": ("click", "tap"),
        "double_click": ("double click", "double-click"),
        "right_click": ("right click", "right-click"),
        "move": ("move", "cursor"),
        "press": ("press", "hit", "key"),
        "scroll_up": ("scroll up", "scroll upward"),
        "scroll_down": ("scroll down", "scroll downward"),
        "close_app": ("close", "quit", "exit", "stop"),
        "close_window": ("close",),
        "close_tab": ("close",),
        "volume_up": ("volume up", "louder", "increase volume"),
        "volume_down": ("volume down", "quieter", "decrease volume"),
        "mute": ("mute",),
        "shutdown": ("shutdown", "shut down"),
        "restart": ("restart", "reboot"),
        "lock": ("lock",),
        "sleep": ("sleep",),
    }
    required_phrases = requirements.get(verb)
    return bool(required_phrases and any(phrase in user for phrase in required_phrases))


def run_builtin(text: str) -> str | None:
    """Comprehensive, fast local commands. Returns reply or None for LLM Brain."""
    # Deterministic Identity Check (Zero LLM latency)
    identity_reply = memory.check_deterministic_identity(text)
    if identity_reply:
        return identity_reply

    # Memory commands (remember that..., forget..., what do you remember)
    mem_reply = memory.handle_memory_command(text)
    if mem_reply:
        return mem_reply

    t = text.lower().strip(" .!?,")
    now = dt.datetime.now()

    # Safety Tools & System Stats
    if any(p in t for p in ("system stats", "system status", "disk space", "storage space", "hardware stats")):
        return tools.get_system_stats()

    # Read file
    for prefix in ("read file ", "show file ", "view file "):
        if t.startswith(prefix):
            target_path = text[len(prefix):].strip()
            return tools.read_file(target_path)

    # Safe delete (Recycle Bin via send2trash)
    for prefix in ("trash file ", "delete file ", "remove file ", "send to trash "):
        if t.startswith(prefix):
            target_path = text[len(prefix):].strip()
            return tools.delete_file(target_path)

    # Undo
    if t in ("undo", "undo that", "undo last action"):
        if tools.undo_stack:
            last = tools.undo_stack.pop()
            return f"Last action was {last.get('action')} on {os.path.basename(last.get('path', ''))}. The item is safe in your Recycle Bin, {USER_TITLE}."
        return f"Nothing in the undo buffer to reverse, {USER_TITLE}."

    # HUD Controls
    if any(p in t for p in ("show hud", "open hud", "display hud", "bring up hud")):
        hud.cmd_queue.put(("show", True))
        return f"Displaying heads-up display, {USER_TITLE}."
    if any(p in t for p in ("hide hud", "close hud", "minimize hud")):
        hud.cmd_queue.put(("hide", True))
        return f"Hiding heads-up display, {USER_TITLE}."
    if any(p in t for p in ("mute mic", "mute microphone", "mute voice")):
        hud.set_mute(True)
        return f"Microphone muted on HUD, {USER_TITLE}."
    if any(p in t for p in ("unmute mic", "unmute microphone", "unmute voice")):
        hud.set_mute(False)
        return f"Microphone active, {USER_TITLE}."

    # Time / Date
    if "what time" in t or t in ("time", "the time") or ("time" in t and ("what" in t or "tell" in t)):
        return f"It's {now:%I:%M %p}, {USER_TITLE}."
    if "date" in t and ("what" in t or "today" in t):
        return f"Today is {now:%A, %d %B %Y}."

    # 1. System Power & Lock
    if any(p in t for p in (
        "shut down the system", "turn off the system", "shut the system",
        "shut down computer", "turn off computer", "power off computer",
        "power off laptop", "shutdown laptop", "shut down my pc",
        "shut down my laptop", "turn off my pc", "shutdown system", "power off system"
    )):
        return system_power("shutdown")

    if any(p in t for p in ("abort shutdown", "cancel shutdown", "stop shutdown", "don't shut down", "do not shut down")):
        return system_power("cancel_shutdown")

    if any(p in t for p in ("restart computer", "reboot computer", "restart the system", "restart system", "reboot system")):
        return system_power("restart")

    if any(p in t for p in ("lock screen", "lock the screen", "lock computer", "lock pc", "lock workstation")):
        return system_power("lock")

    if any(p in t for p in ("put computer to sleep", "sleep mode", "sleep computer")):
        return system_power("sleep")
    if any(p in t for p in ("hibernate computer", "hibernate the computer", "hibernate system")):
        return system_power("hibernate")
    if any(p in t for p in ("sign out", "log off", "logoff", "sign me out")):
        return system_power("logoff")

    # 2. Window Controls
    if any(p in t for p in ("maximize window", "maximize", "full window")):
        return control_window("maximize")
    if any(p in t for p in ("minimize window", "minimize")):
        return control_window("minimize")
    if any(p in t for p in ("close this window", "close window", "close current window", "close active window", "close this app")):
        return control_window("close")
    if any(p in t for p in ("close tab", "close this tab", "close current tab", "close browser tab")):
        return control_window("close_tab")
    if any(p in t for p in ("new tab", "open new tab", "create new tab")):
        return control_window("new_tab")
    if t in ("go back", "back", "browser back", "previous page"):
        return control_window("back")
    if t in ("go forward", "forward", "browser forward", "next page"):
        return control_window("forward")
    if t in ("refresh", "refresh page", "reload", "reload page"):
        return control_window("refresh")
    if t.startswith("find "):
        return find_in_active_window(text[5:])
    if t in ("save", "save file", "save document"):
        return control_window("save")
    if t in ("copy", "copy that", "copy selection"):
        return control_window("copy")
    if t in ("paste", "paste that", "paste here"):
        return control_window("paste")
    if t in ("select all", "select everything"):
        return control_window("select_all")
    if any(p in t for p in ("switch window", "switch app", "next window")):
        return control_window("switch")
    if any(p in t for p in ("show desktop", "minimize all windows", "minimize all")):
        return control_window("desktop")
    if any(p in t for p in ("take screenshot", "take a screenshot", "screenshot")):
        return control_window("screenshot")

    # 3. Volume & Media Controls
    if any(p in t for p in ("volume up", "increase volume", "louder", "sound up")):
        return control_volume("up")
    if any(p in t for p in ("volume down", "decrease volume", "lower volume", "quieter", "sound down")):
        return control_volume("down")
    if any(p in t for p in ("mute", "unmute", "toggle mute", "silence")):
        return control_volume("mute")
    if any(p in t for p in ("pause", "pause music", "pause video", "resume", "resume music", "resume video", "stop music", "stop video", "stop playback", "toggle playback")):
        return control_volume("playpause")
    if any(p in t for p in ("next song", "next track", "skip song")):
        return control_volume("next")
    if any(p in t for p in ("previous song", "previous track")):
        return control_volume("prev")

    # 4. Direct pointer and keyboard controls. Coordinates are explicit to avoid accidental clicks.
    pointer_match = re.search(r"\b(double\s+click|right\s+click|click|tap)\s+(?:at|on)?\s*(\d+)\s*(?:,|and|by|\s)\s*(\d+)\b", t)
    if pointer_match:
        action = pointer_match.group(1).replace(" ", "_")
        if action == "tap":
            action = "click"
        return control_pointer(action, int(pointer_match.group(2)), int(pointer_match.group(3)))

    move_match = re.search(r"\b(?:move|move mouse|move pointer)\s+(?:to\s+)?(\d+)\s*(?:,|and|by|\s)\s*(\d+)\b", t)
    if move_match:
        return control_pointer("move", int(move_match.group(1)), int(move_match.group(2)))
    if t in ("scroll up", "scroll upward"):
        return control_pointer("scroll_up")
    if t in ("scroll down", "scroll downward"):
        return control_pointer("scroll_down")
    if (t.startswith("type ") or t.startswith("write ")) and not any(
        t.startswith(prefix) for prefix in ("type in notepad ", "write in notepad ", "type in calculator ", "write in calculator ")
    ):
        return type_active_window(text.split(maxsplit=1)[1])
    for prefix in ("press ", "hit "):
        if t.startswith(prefix):
            return press_key(t[len(prefix):])

    # 5. Closing specific apps
    if any(t.startswith(p) for p in ("close ", "exit ", "kill ", "stop ")):
        target = t.split(maxsplit=1)[1].strip(" .!?,")
        if close_app(target):
            return f"Closed {target}, {USER_TITLE}."
        if target.endswith(" tab") or target in ("tab", "browser tab"):
            return control_window("close_tab")
        return f"I could not find {target} running, {USER_TITLE}."

    # 5. Type / calculate inside Calculator or any spoken math with digits
    math_words = ("plus", "add", "minus", "subtract", "times", "multiplied by", "into", "divided by", "divide by", "over")
    has_math = any(w in t for w in math_words)
    has_digits = bool(re.search(r"\d+", t))
    if (has_math and has_digits) or any(w in t for w in ("in calculator", "on calculator", "into calculator", "in the calculator", "calculator app")):
        return type_in_calc(t)

    # 6. Type / write into Notepad
    for prefix in (
        "write in notepad ", "type in notepad ", "write in notepad that ", "type in notepad that ",
        "take a note in notepad ", "take a note ", "note down in notepad ", "note down "
    ):
        if t.startswith(prefix):
            content = text[len(prefix):].strip()
            return type_in_notepad(content)

    # 7. Media & Song Playing (Direct YouTube Autoplay)
    for p in (
        "play a song called ", "play a song by ", "play a song ", "play me a song ",
        "play some music ", "play music ", "can you play ", "please play ", "put on ", "play "
    ):
        if t.startswith(p):
            song_query = t[len(p):].strip(" .!?,")
            return play_song(song_query)

    if t in ("play a song", "play some music", "play music", "play song", "put on some music", "listen to music", "play something"):
        return play_song("top hits music")

    if "play " in t and any(m in t for m in ("song", "music", "track", "audio")):
        idx = t.find("play ")
        return play_song(t[idx + 5:].strip(" .!?,"))

    # 8. Web Searches
    for prefix in ("search for ", "google "):
        if t.startswith(prefix):
            query = t[len(prefix):].strip()
            return execute_action(f"search_google {query}")

    # 8. Specific App and Website Opening (with speech recognition aliases)
    if any(w in t for w in ("youtube", "you tube", "utube", "u tube")):
        open_url(SITES["youtube"])
        return f"Opening YouTube, {USER_TITLE}."

    if any(w in t for w in ("calculator", "calc", "calci", "calcu")):
        open_app("calculator")
        return f"Opening Calculator, {USER_TITLE}."

    if any(w in t for w in ("notepad", "not pad", "not bad", "note pad", "notes")):
        open_app("notepad")
        return f"Opening Notepad, {USER_TITLE}."

    if any(w in t for w in ("file explorer", "explorer", "my computer", "this pc", "files")):
        open_app("file explorer")
        return f"Opening File Explorer, {USER_TITLE}."

    if any(w in t for w in ("chrome", "browser", "google chrome", "crawl", "crow", "clone", "chrom", "crome")):
        open_app("chrome")
        return f"Opening Chrome, {USER_TITLE}."

    if any(w in t for w in ("edge", "microsoft edge")):
        open_app("edge")
        return f"Opening Edge, {USER_TITLE}."

    if any(w in t for w in ("vs code", "vscode", "visual studio code", "code")):
        open_app("vs code")
        return f"Opening VS Code, {USER_TITLE}."

    if any(w in t for w in ("word", "winword", "ms word", "microsoft word")):
        open_app("word")
        return f"Opening Microsoft Word, {USER_TITLE}."

    if any(w in t for w in ("excel", "ms excel", "microsoft excel")):
        open_app("excel")
        return f"Opening Microsoft Excel, {USER_TITLE}."

    if any(w in t for w in ("powerpoint", "ppt", "power point")):
        open_app("powerpoint")
        return f"Opening PowerPoint, {USER_TITLE}."

    if any(w in t for w in ("camera", "webcam")):
        open_app("camera")
        return f"Opening Camera, {USER_TITLE}."

    if any(w in t for w in ("task manager", "taskmgr", "monitor", "the monitor", "activity monitor")):
        open_app("task manager")
        return f"Opening Task Manager, {USER_TITLE}."

    if any(w in t for w in ("whatsapp",)):
        open_app("whatsapp")
        return f"Opening WhatsApp, {USER_TITLE}."

    if any(w in t for w in ("spotify",)):
        open_app("spotify")
        return f"Opening Spotify, {USER_TITLE}."

    if any(w in t for w in ("settings", "windows settings", "pc settings", "setting")):
        open_app("settings")
        return f"Opening Settings, {USER_TITLE}."

    # General "open <target>" / "launch <target>" / "start <target>"
    for prefix in ("open ", "launch ", "start "):
        if t.startswith(prefix):
            target = t[len(prefix):].strip(" .!?,")
            if target in SITES:
                open_url(SITES[target])
                return f"Opening {target}, {USER_TITLE}."
            if open_app(target):
                return f"Opening {target}, {USER_TITLE}."

            # Rapidfuzz candidate matching against known apps and websites
            candidates = list(SITES.keys()) + [
                "calculator", "notepad", "chrome", "edge", "vs code", "word",
                "excel", "powerpoint", "camera", "task manager", "whatsapp",
                "spotify", "settings", "file explorer"
            ]
            match_res = process.extractOne(target, candidates, scorer=fuzz.WRatio)
            if match_res and match_res[1] >= 75:
                best = match_res[0]
                if best in SITES:
                    open_url(SITES[best])
                    return f"Opening {best}, {USER_TITLE}."
                if open_app(best):
                    return f"Opening {best}, {USER_TITLE}."

    # Direct fuzzy match for common app names spoken without "open" (e.g. "calculator", "notepd", "chome")
    if len(t.split()) <= 3:
        all_apps = [
            "calculator", "notepad", "chrome", "edge", "vs code", "word",
            "excel", "powerpoint", "camera", "task manager", "whatsapp",
            "spotify", "settings", "file explorer"
        ]
        match_app = process.extractOne(t, all_apps, scorer=fuzz.WRatio)
        if match_app and match_app[1] >= 85:
            if open_app(match_app[0]):
                return f"Opening {match_app[0]}, {USER_TITLE}."

    return None


def _is_ignore_reply(text: str) -> bool:
    """The model signals 'that was noise, say nothing' with [IGNORE]."""
    return bool(re.fullmatch(r"\W*\[?\s*ignore\s*\]?\W*", text.strip(), re.I))


def extract_sentences(buffer: str) -> tuple[list[str], str]:
    """Extract complete sentences ending in . ! ? or newline from buffer."""
    sentences = []
    pattern = re.compile(r'([^.!?\n]+[.!?]+(?:\s+|$)|[^\n]+\n+)')
    last_end = 0
    for match in pattern.finditer(buffer):
        s = match.group().strip()
        if s:
            sentences.append(s)
        last_end = match.end()
    remaining = buffer[last_end:]
    return sentences, remaining


class Brain:
    """The thinking core with intelligent action execution."""

    def __init__(self) -> None:
        self.client = None
        self.history: list[dict] = memory.get_history(max_turns=12)
        self.provider = LLM_PROVIDER
        cfg = PROVIDERS.get(self.provider)
        if cfg is None:
            logger.warning(f"Unknown LLM_PROVIDER '{self.provider}'. Choose from: {', '.join(PROVIDERS)}.")
            return
        self.model = os.environ.get("JARVIS_MODEL") or cfg["model"]

        key_env = cfg.get("key_env")
        key = os.environ.get(key_env) if key_env else "ollama"
        if not key:
            logger.warning(f"{key_env} is not set - only built-in commands will work.")
            return

        if self.provider == "anthropic":
            import anthropic

            self.client = anthropic.Anthropic()
        else:
            from openai import OpenAI

            self.client = OpenAI(base_url=cfg["base_url"], api_key=key)
        logger.info(f"Brain online: {self.provider} / {self.model}")

    def _complete(self) -> str:
        sys_prompt = memory.inject_system_prompt(SYSTEM_PROMPT)
        if self.provider == "anthropic":
            resp = self.client.messages.create(
                model=self.model, max_tokens=600, system=sys_prompt, messages=self.history
            )
            return "".join(b.text for b in resp.content if b.type == "text")
        messages = [{"role": "system", "content": sys_prompt}] + self.history
        resp = self.client.chat.completions.create(
            model=self.model, max_tokens=220, temperature=0.2, messages=messages
        )
        return resp.choices[0].message.content or ""

    def ask(self, text: str) -> str:
        if not self.client:
            return f"My language core is offline, {USER_TITLE}. Please set your API key."
        self.history.append({"role": "user", "content": text})
        memory.add_turn("user", text)
        try:
            raw_reply = self._complete().strip() or "Understood."
        except Exception as e:
            self.history.pop()
            msg = str(e).lower()
            logger.error(f"[llm error] {e}")
            if "429" in msg or "rate" in msg or "quota" in msg:
                return f"I've hit my usage limit for the moment, {USER_TITLE}. Give me a minute."
            if "404" in msg or "not found" in msg:
                return f"That model isn't available, {USER_TITLE}. Run jarvis dot py with the models flag to see which ones are."
            return f"I'm having trouble reaching my language core, {USER_TITLE}."

        if _is_ignore_reply(raw_reply):
            logger.info(f"Brain ignored non-command input: {text!r}")
            self.history.pop()
            memory.drop_last_turn("user")
            return ""

        # Parse and execute any embedded action tag: [ACTION: ...]
        spoken_reply = raw_reply
        action_match = re.search(r"\[ACTION:\s*([^\]]+)\]", raw_reply)
        if action_match:
            action_content = action_match.group(1).strip()
            if action_is_requested(text, action_content):
                logger.info(f"[Brain Executing Action: {action_content}]")
                action_res = execute_action(action_content)
            else:
                logger.warning(f"[Brain rejected unsupported action: {action_content}]")
                action_res = None
            spoken_reply = re.sub(r"\[ACTION:\s*[^\]]+\]", "", raw_reply).strip()
            if not spoken_reply:
                spoken_reply = action_res or f"Right away, {USER_TITLE}."

        self.history.append({"role": "assistant", "content": spoken_reply})
        memory.add_turn("assistant", spoken_reply)
        if len(self.history) > 12:
            del self.history[:-12]
        return spoken_reply

    def ask_stream(self, text: str, on_sentence=None) -> str:
        """Stream LLM response and yield completed sentences immediately to TTS worker."""
        if not self.client:
            msg = f"My language core is offline, {USER_TITLE}. Please set your API key."
            if on_sentence:
                on_sentence(msg)
            return msg

        self.history.append({"role": "user", "content": text})
        memory.add_turn("user", text)

        sys_prompt = memory.inject_system_prompt(SYSTEM_PROMPT)
        messages = [{"role": "system", "content": sys_prompt}] + self.history

        buf = ""
        full_reply = ""
        try:
            if self.provider == "anthropic":
                with self.client.messages.stream(
                    model=self.model, max_tokens=220, system=sys_prompt, messages=self.history
                ) as stream:
                    for chunk in stream.text_stream:
                        buf += chunk
                        full_reply += chunk
                        sents, buf = extract_sentences(buf)
                        for s in sents:
                            clean_s = re.sub(r"\[ACTION:\s*[^\]]+\]", "", s).strip()
                            if clean_s and on_sentence and not _is_ignore_reply(clean_s):
                                on_sentence(clean_s)
            else:
                stream = self.client.chat.completions.create(
                    model=self.model, max_tokens=220, temperature=0.2, messages=messages, stream=True
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else ""
                    if delta:
                        buf += delta
                        full_reply += delta
                        sents, buf = extract_sentences(buf)
                        for s in sents:
                            clean_s = re.sub(r"\[ACTION:\s*[^\]]+\]", "", s).strip()
                            if clean_s and on_sentence and not _is_ignore_reply(clean_s):
                                on_sentence(clean_s)

            if buf.strip():
                clean_s = re.sub(r"\[ACTION:\s*[^\]]+\]", "", buf.strip()).strip()
                if clean_s and on_sentence and not _is_ignore_reply(clean_s):
                    on_sentence(clean_s)

        except Exception as e:
            self.history.pop()
            msg = str(e).lower()
            logger.error(f"[llm error] {e}")
            if "429" in msg or "rate" in msg or "quota" in msg:
                err_reply = f"I've hit my usage limit for the moment, {USER_TITLE}. Give me a minute."
            elif "404" in msg or "not found" in msg:
                err_reply = f"That model isn't available, {USER_TITLE}."
            else:
                err_reply = f"I'm having trouble reaching my language core, {USER_TITLE}."
            if on_sentence:
                on_sentence(err_reply)
            return err_reply

        if _is_ignore_reply(full_reply):
            logger.info(f"Brain ignored non-command input: {text!r}")
            self.history.pop()
            memory.drop_last_turn("user")
            return ""

        action_results = []
        for action_content in re.findall(r"\[ACTION:\s*([^\]]+)\]", full_reply):
            action_content = action_content.strip()
            if action_is_requested(text, action_content):
                logger.info(f"[Brain Executing Streamed Action: {action_content}]")
                action_result = execute_action(action_content)
                if action_result:
                    action_results.append(action_result)
            else:
                logger.warning(f"[Brain rejected unsupported streamed action: {action_content}]")
        spoken_reply = re.sub(r"\[ACTION:\s*[^\]]+\]", "", full_reply).strip()
        if not spoken_reply:
            spoken_reply = action_results[-1] if action_results else f"Understood, {USER_TITLE}."
        self.history.append({"role": "assistant", "content": spoken_reply})
        memory.add_turn("assistant", spoken_reply)
        if len(self.history) > 12:
            del self.history[:-12]
        return spoken_reply


# ------------------------------ Main loop -----------------------------
def session(q: queue.Queue, stt, brain: Brain) -> None:
    """One conversation session: continuous listening for follow-ups."""
    beep(880, 0.05)
    follow_up = False
    consecutive_empty = 0
    awaiting_answer = False  # True when Jarvis just asked a question, so short replies like 'yes' are real

    while True:
        if follow_up:
            beep(600, 0.04)
        print("[Listening for command...]" if not follow_up else "[Listening for follow-up...]", flush=True)
        audio = record_utterance(q, max_wait=FOLLOWUP_WAIT if follow_up else FIRST_WAIT)
        if audio is None:
            if not follow_up:
                return
            print("[No speech detected, returning to standby]", flush=True)
            return

        print("[Transcribing audio...]", flush=True)
        text = normalize_command_text(transcribe(stt, audio))
        if text and is_noise_transcript(text, allow_short_replies=(pending_power_action is not None or awaiting_answer)):
            logger.info(f"Ignored noise transcript: {text!r}")
            text = ""
        if not text:
            if not follow_up:
                return
            consecutive_empty += 1
            if consecutive_empty < 2:
                print("[No words recognized, still listening for follow-up...]", flush=True)
                continue
            print("[No words recognized, returning to standby]", flush=True)
            return

        consecutive_empty = 0
        print(f"You: {text}", flush=True)
        hud.set_transcript(user=text)
        low = text.lower().strip(" .!?,")

        # Strip wake-word prefix, including the punctuation Whisper often adds.
        wake_prefix = re.compile(
            r"^(?:hey\s+jarvis|ok(?:ay)?\s+jarvis|jarvis|hey\s+javis|javis|"
            r"hey\s+josh|josh|hey\s+joe|joe)[\s,;:.-]*",
            re.IGNORECASE,
        )
        stripped_low = wake_prefix.sub("", low, count=1).strip(" .!?,")
        if stripped_low != low:
            text = wake_prefix.sub("", text, count=1).strip()
            low = stripped_low

        # If user only said the name and paused
        if not low or low in ("jarvis", "hey jarvis", "javis", "hey javis", "josh", "hey josh"):
            hud.set_state("SPEAKING")
            speak(f"Yes, {USER_TITLE}?", block=True)
            hud.set_state("LISTENING")
            time.sleep(0.4)
            drain(q)
            follow_up = True
            continue

        if any(p in low for p in QUIT_PHRASES):
            hud.set_state("SPEAKING")
            speak("Shutting down. Goodbye.", block=True)
            raise SystemExit
        if len(low.split()) <= 4 and any(p in low for p in SLEEP_PHRASES):
            hud.set_state("STANDBY")
            if QUIET_STANDBY:
                beep(500, 0.06)
            else:
                speak("Standing by.", block=True)
            return

        # Live Window Dictation Mode
        if dictation.is_dictation_start(low):
            dictation.run_dictation_session(
                q=q,
                stt=stt,
                speak_func=lambda s: speak(s, block=True),
                record_func=record_utterance,
                transcribe_func=lambda stt_, audio_: "" if is_noise_transcript(t_ := transcribe(stt_, audio_), dictation=True) else t_,
                beep_func=beep,
                user_title=USER_TITLE,
            )
            drain(q)
            follow_up = True
            hud.set_state("STANDBY")
            continue

        had_pending_power_action = pending_power_action is not None
        power_confirmation = handle_power_confirmation(low)
        if power_confirmation:
            hud.set_transcript(jarvis=power_confirmation)
            hud.set_state("SPEAKING")
            speak(power_confirmation, block=True)
            hud.set_state("STANDBY")
            time.sleep(0.4)
            drain(q)
            if had_pending_power_action and pending_power_action is None:
                return
            follow_up = True
            continue

        try:
            hud.set_state("THINKING")
            reply = run_builtin(low)
            if reply:
                memory.add_turn("user", text)
                memory.add_turn("assistant", reply)
                hud.set_transcript(jarvis=reply)
                hud.set_state("SPEAKING")
                speak(reply, block=True)
                awaiting_answer = reply.rstrip().endswith("?")
            else:
                hud.set_state("SPEAKING")
                def _stream_speaker(sentence: str):
                    hud.set_transcript(jarvis=sentence)
                    speak(sentence, block=False)

                prompt = (
                    f"The detected user language is {last_detected_language or 'unknown'}. "
                    "Reply in that language unless the user asks otherwise. "
                    f"User request: {text}"
                )
                if needs_live_context(text):
                    live_context = fetch_live_context(text)
                    if live_context:
                        prompt = (
                            f"Answer the user's time-sensitive question using these fresh web search snippets. "
                            f"Clearly distinguish confirmed facts from uncertain or incomplete information. "
                            f"Detected language: {last_detected_language or 'unknown'}\n"
                            f"Question: {text}\nFresh snippets:\n{live_context}"
                        )
                spoken = brain.ask_stream(prompt, on_sentence=_stream_speaker)
                tts_worker.wait()
                if not spoken:  # the model decided it was noise: stay silent
                    hud.set_state("STANDBY")
                    if not follow_up:
                        return
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        return
                    continue
                awaiting_answer = spoken.rstrip().endswith("?")
            hud.set_state("STANDBY")
        except Exception as e:
            logger.error(f"Error executing command '{text}': {e}", exc_info=True)
            hud.set_state("STANDBY")
            speak(f"Apologies, {USER_TITLE}. Encountered an issue, but standing by.", block=True)

        time.sleep(0.4)
        drain(q)
        follow_up = True


def show_levels() -> None:
    print(f"Clap near your mic. Claps should reach {CLAP_MIN_PEAK:.2f}+, normal speech should stay well below. Ctrl+C to stop.\n")
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
            while True:
                data, _ = stream.read(FRAME)
                peak = float(np.max(np.abs(data))) / 32768.0
                flag = "  <-- CLAP" if peak >= CLAP_MIN_PEAK else ""
                print(f"peak {peak:4.2f} |{'#' * int(peak * 50)}{flag}")
    except KeyboardInterrupt:
        pass


def wake_debug() -> None:
    """Live wake-word score meter, to pick a threshold that fits your mic and room."""
    wake = WakeWord()
    print(f"Current threshold: {WAKE_THRESHOLD}  (set JARVIS_WAKE_THRESHOLD to change it)")
    print("1) Say 'Hey Jarvis' 5 times and note the scores.  2) Stay quiet / play your usual background sound and note the highest score.")
    print("Pick a threshold between the two. Ctrl+C to stop.\n")
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
            while True:
                data, _ = stream.read(FRAME)
                fired = wake.heard(data[:, 0].copy())
                if wake.last_score >= 0.05 or fired:
                    print(f"score {wake.last_score:4.2f} |{'#' * int(wake.last_score * 40)}{'  <-- WAKE' if fired else ''}")
    except KeyboardInterrupt:
        pass


def test_llm() -> None:
    brain = Brain()
    if brain.client:
        print("Reply:", brain.ask("Say hello in five words."))


def list_models() -> None:
    brain = Brain()
    if not brain.client:
        return
    try:
        page = brain.client.models.list()
        for m in getattr(page, "data", page):
            print(m.id)
    except Exception as e:
        print(f"Could not list models: {e}")


def run_selftest() -> None:
    """Phase 4 Automated Tool and Safety Layer Verification Suite."""
    print("=== JARVIS AUTOMATED SELF-TEST (PHASE 4 SAFETY LAYER) ===")
    sandbox = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sandbox")
    os.makedirs(sandbox, exist_ok=True)
    test_file = os.path.join(sandbox, "test_target.txt")

    # Test 1: Tier 1 Tool - Write & Read file
    print("\n[Test 1] Testing Tier 1 Tools: write_file & read_file...")
    write_res = tools.write_file(test_file, "Jarvis Mark 85 armor protocol.")
    assert os.path.exists(test_file), "Failed to write test file"
    print(f"  [PASS] write_file: {write_res}")

    read_res = tools.read_file(test_file)
    assert "Mark 85" in read_res, "Failed to read test file"
    print(f"  [PASS] read_file: '{read_res}'")

    # Test 2: Tier 1 Tool - List Dir & System Stats
    print("\n[Test 2] Testing Tier 1 Tools: list_dir & get_system_stats...")
    list_res = tools.list_dir(sandbox)
    assert "test_target.txt" in list_res, "list_dir missing file"
    print(f"  [PASS] list_dir: {list_res}")

    stats_res = tools.get_system_stats()
    assert "capacity" in stats_res, "get_system_stats invalid"
    print(f"  [PASS] get_system_stats: {stats_res}")

    # Test 3: Tier 2 Tool - Safe Delete via send2trash (Recycle Bin)
    print("\n[Test 3] Testing Tier 2 Tool: delete_file (send2trash to Recycle Bin)...")
    del_res = tools.delete_file(test_file)
    assert not os.path.exists(test_file), "File was not removed from sandbox!"
    assert len(tools.undo_stack) > 0, "Undo stack missing delete action"
    print(f"  [PASS] delete_file: {del_res}")
    print(f"  [PASS] Undo stack verified: {tools.undo_stack[-1]}")

    # Test 4: Tier 3 Blocked Action - Format Drive & Protected Path Deletion
    print("\n[Test 4] Testing Tier 3 Protection: Blocked operations...")
    block_drive = tools.execute_tool("format_drive", {"drive": "C:"})
    assert "blocked" in block_drive.lower(), "Tier 3 format_drive was not blocked!"
    print(f"  [PASS] format_drive: {block_drive}")

    block_sys = tools.delete_file("C:\\Windows\\System32\\cmd.exe")
    assert "blocked" in block_sys.lower(), "Deletion of C:\\Windows was not blocked!"
    print(f"  [PASS] Protected path delete: {block_sys}")

    # Test 5: Confirmation Cancellation
    print("\n[Test 5] Testing Tier 2 Confirmation Cancellation...")
    cancel_res = tools.execute_tool(
        "delete_file",
        {"path": os.path.join(sandbox, "nonexistent.txt")},
        confirm_callback=lambda prompt: False,  # user says NO
    )
    assert "cancelled" in cancel_res.lower(), "Action was not cancelled on rejection"
    print(f"  [PASS] Confirmation cancellation: {cancel_res}")

    # Test 6: Audit Log Verification
    print("\n[Test 6] Verifying Audit Trail in logs/actions.log...")
    audit_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "actions.log")
    assert os.path.exists(audit_log), "Audit log file does not exist!"
    with open(audit_log, "r", encoding="utf-8") as f:
        log_content = f.read()
    assert "TIER 1" in log_content, "Missing Tier 1 entries in audit log"
    assert "TIER 2" in log_content, "Missing Tier 2 entries in audit log"
    assert "BLOCKED" in log_content, "Missing BLOCKED entries in audit log"
    assert "CANCELLED" in log_content, "Missing CANCELLED entries in audit log"
    print(f"  [PASS] Audit log verified with complete Tier 1, 2, 3 and status records.")

    # Cleanup sandbox
    try:
        shutil.rmtree(sandbox, ignore_errors=True)
    except Exception:
        pass
    print("\nALL PHASE 4 TESTS PASSED 100%!")


def main() -> None:
    ap = argparse.ArgumentParser(description=f"{NAME} voice assistant")
    ap.add_argument("--levels", action="store_true", help="show live mic levels to tune the clap threshold")
    ap.add_argument("--test-llm", action="store_true", help="send one test question to the configured brain")
    ap.add_argument("--models", action="store_true", help="list the model names your API key can use")
    ap.add_argument("--wake-debug", action="store_true", help="live wake-word score meter for tuning the threshold")
    ap.add_argument("--selftest", action="store_true", help="run automated safety and tool suite")
    ap.add_argument("--no-hud", action="store_true", help="run headless without desktop GUI HUD")
    args = ap.parse_args()
    if args.levels:
        return show_levels()
    if args.test_llm:
        return test_llm()
    if args.models:
        return list_models()
    if args.wake_debug:
        return wake_debug()
    if args.selftest:
        return run_selftest()

    if not acquire_single_instance():
        print("Jarvis is already running. Close the other copy first (Task Manager > python.exe / pythonw.exe).")
        logger.warning("Second instance refused: Jarvis is already running.")
        return

    # Start HUD in background thread unless headless requested
    if not args.no_hud:
        hud.start()

    now = dt.datetime.now()
    hour = now.hour
    if hour < 12:
        part = "Good morning"
    elif hour < 17:
        part = "Good afternoon"
    else:
        part = "Good evening"
    time_str = now.strftime("%I:%M %p")
    welcome_text = f"{part}, {USER_TITLE} {USER_NICKNAME}. All systems online."
    hud.set_transcript(jarvis=welcome_text)
    if ENABLE_AUTO_GREETING and should_greet():
        hud.set_state("SPEAKING")
        speak(welcome_text, block=False)
    else:
        hud.set_state("STANDBY")

    print("Loading models (the first run downloads them, give it a minute)...")
    wake = WakeWord() if ENABLE_WAKE_WORD else None
    clap = DoubleClapDetector() if ENABLE_CLAP else None
    from faster_whisper import WhisperModel

    stt = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    brain = Brain()
    tts_worker.wait()
    hud.set_state("STANDBY")

    q: queue.Queue = queue.Queue()
    echo_guard = EchoGuard()

    def on_audio(indata, frames, time_info, status):
        q.put(indata[:, 0].copy())

    def on_power_welcome(reason: str) -> None:
        if not ENABLE_AUTO_GREETING:
            logger.info(f"System wake/unlock detected ({reason}); automatic greeting disabled.")
            return
        if not should_greet():
            logger.info(f"System wake/unlock detected ({reason}); greeted recently, staying quiet.")
            return
        now = dt.datetime.now()
        hour = now.hour
        if hour < 12:
            part = "Good morning"
        elif hour < 17:
            part = "Good afternoon"
        else:
            part = "Good evening"
        time_str = now.strftime("%I:%M %p")
        wake_text = f"{part}, {USER_TITLE} {USER_NICKNAME}. All systems online."
        logger.info(f"System wake/unlock detected ({reason}): Greeting {USER_NICKNAME}.")
        hud.set_transcript(jarvis=wake_text)
        hud.set_state("SPEAKING")
        speak(wake_text, block=False)

        def _reset_hud():
            tts_worker.wait()
            hud.set_state("STANDBY")

        threading.Thread(target=_reset_hud, daemon=True).start()

    power_events.on_welcome = on_power_welcome
    power_events.start()

    triggers = " or ".join(x for x, on in (("'Jarvis'", wake), ("clap twice", clap)) if on)
    print(f"Listening... say {triggers}. Ctrl+C to quit.")
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME, callback=on_audio):
            while True:
                frame = q.get()
                skip, speech_ended = echo_guard.check(tts_worker.is_busy(), time.monotonic())
                if speech_ended:  # throw away anything the mic caught while he was talking
                    drain(q)
                    if wake:
                        wake.reset()
                    if clap:
                        clap.reset()
                if skip:
                    continue
                woke = wake.heard(frame) if wake else False
                clapped = clap.process(frame) if clap else False
                if woke or clapped:
                    print("[activated by " + ("wake word]" if woke else "double clap]"))
                    hud.set_state("LISTENING")
                    try:
                        session(q, stt, brain)
                    except SystemExit:
                        raise
                    except Exception as e:
                        logger.error(f"Session crash caught: {e}", exc_info=True)
                        speak(f"Apologies, {USER_TITLE}. Encountered an issue, but standing by.")
                    finally:
                        hud.set_state("STANDBY")
                        drain(q)
                        if wake:
                            wake.reset()
                        if clap:
                            clap.reset()
    except KeyboardInterrupt:
        print("\nGoodbye.")
    finally:
        power_events.stop()



if __name__ == "__main__":
    main()
