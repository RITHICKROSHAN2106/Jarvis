"""
Regression tests for "Jarvis talks when nobody asked".

Run from the project folder:   python scripts/test_quiet.py
Needs no microphone, speakers, internet or API key. Uses a temporary memory file, so your real
data/memory.json is never touched.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import types, time, tempfile
from unittest.mock import MagicMock
import numpy as np

# ---- stub heavy / Windows-only modules so jarvis.py imports on this sandbox ----
rf = types.ModuleType("rapidfuzz"); rf.process = MagicMock(); rf.fuzz = MagicMock(); sys.modules["rapidfuzz"] = rf
sd = types.ModuleType("sounddevice"); sys.modules["sounddevice"] = sd
for name in ("pyautogui", "pyperclip"):
    sys.modules[name] = MagicMock()
hud_mod = types.ModuleType("core.hud"); hud_mod.hud = MagicMock(); hud_mod.JarvisHUD = MagicMock(); sys.modules["core.hud"] = hud_mod
pe_mod = types.ModuleType("core.power_events"); pe_mod.power_events = MagicMock(); pe_mod.PowerEventManager = MagicMock(); sys.modules["core.power_events"] = pe_mod
if "yaml" not in sys.modules:
    try: import yaml
    except ImportError: sys.modules["yaml"] = MagicMock()

import jarvis as j
from core.memory import MemoryManager
j.memory = MemoryManager(os.path.join(tempfile.mkdtemp(), "memory.json"))  # throwaway memory for the tests
FAILS = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

# ---------- 1. transcript gate ----------
junk = ["Bop.", "Thank you.", "you", "Thanks for watching!", "Bye.", ".", "ah", "Subtitles by the Amara.org community",
        "Jarvis, Chrome, Edge, Calculator", "Okay.", "ご視聴ありがとうございました", "www.example.com", "Yeah"]
for t in junk: check(f"junk blocked: {t!r}", j.is_noise_transcript(t))
real = ["open chrome", "what time is it", "mute", "volume up", "play arijit singh on youtube", "hello", "take a screenshot",
        "type hello boss", "open the calculator", "what is my real name"]
for t in real: check(f"command kept:  {t!r}", not j.is_noise_transcript(t))
check("'yes' blocked normally", j.is_noise_transcript("Yes."))
check("'yes' allowed when he asked a question", not j.is_noise_transcript("Yes.", allow_short_replies=True))
check("dictation keeps 'okay'", not j.is_noise_transcript("Okay.", dictation=True))
check("dictation blocks 'Bop.'", j.is_noise_transcript("Bop.", dictation=True))

# ---------- 2. whisper confidence rules ----------
check("seg: silent+unsure dropped", j.segment_is_junk(0.9, -1.5, 1.0))
check("seg: clear speech kept", not j.segment_is_junk(0.05, -0.3, 1.2))
check("seg: accent-heavy but speech kept", not j.segment_is_junk(0.1, -1.3, 1.5))
check("seg: repetition gibberish dropped", j.segment_is_junk(0.1, -0.3, 3.0))
check("seg: None values safe", not j.segment_is_junk(None, None, None))

# ---------- 3. wake word needs repeated hits ----------
class FakeModel:
    def __init__(self): self.score = 0.0; self.resets = 0
    def predict(self, f): return {"hey_jarvis": self.score}
    def reset(self): self.resets += 1
w = j.WakeWord.__new__(j.WakeWord); w.model = FakeModel(); w.recent = j.deque(maxlen=j.WAKE_CONFIRM_WINDOW); w.last_score = 0.0
fr = np.zeros(j.FRAME, dtype=np.int16)
w.model.score = 0.018 + 0.001
check("noise-level score 0.019 no longer wakes (old threshold would)", not any(w.heard(fr) for _ in range(20)))
w.reset(); w.model.score = 0.9
first = w.heard(fr); second = w.heard(fr)
check("single spike does not wake", first is False)
check("sustained score wakes", second is True)
w.reset(); w.model.score = 0.9; w.heard(fr); w.model.score = 0.0
check("one spike then silence never wakes", not any(w.heard(fr) for _ in range(10)))
check("reset clears model", w.model.resets >= 1)

# ---------- 4. echo guard ----------
g = j.EchoGuard(hold=1.0)
check("idle: not skipped", g.check(False, 0.0) == (False, False))
check("speaking: skipped", g.check(True, 1.0) == (True, False))
check("speech ends: skipped + flush flag", g.check(False, 2.0) == (True, True))
check("hold period: still skipped", g.check(False, 2.5) == (True, False))
check("after hold: listening again", g.check(False, 3.1) == (False, False))

# ---------- 5. recording: click vs real speech ----------
import queue
def frames(kind, n):
    rng = np.random.default_rng(1); out = []
    for i in range(n):
        if kind == "quiet": f = rng.normal(0, 0.004, j.FRAME)
        elif kind == "speech": f = rng.normal(0, 0.06, j.FRAME)
        elif kind == "click": f = rng.normal(0, 0.004, j.FRAME); f[300] = 0.6
        out.append((np.clip(f, -1, 1) * 32767).astype(np.int16))
    return out
q = queue.Queue()
for f in frames("quiet", 8) + frames("click", 1) + frames("quiet", 200): q.put(f)
check("a single click is NOT speech (returns None)", j.record_utterance(q, max_wait=3.0) is None)
q = queue.Queue()
for f in frames("quiet", 8) + frames("speech", 12) + frames("quiet", 20): q.put(f)
a = j.record_utterance(q, max_wait=3.0)
check("real speech is recorded", a is not None and len(a) > 8 * j.FRAME)

# ---------- 6. Brain: [IGNORE] stays silent, normal answers are spoken ----------
class Delta:  # one streamed chunk
    def __init__(self, t): self.choices = [types.SimpleNamespace(delta=types.SimpleNamespace(content=t))]
class FakeCompletions:
    reply = ""
    def create(self, **kw):
        if kw.get("stream"): return iter([Delta(self.reply[i:i+4]) for i in range(0, len(self.reply), 4)])
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.reply))])
class FakeOpenAI:
    def __init__(self, **kw): self.chat = types.SimpleNamespace(completions=FakeCompletions())
fake = types.ModuleType("openai"); fake.OpenAI = FakeOpenAI; sys.modules["openai"] = fake
os.environ["GROQ_API_KEY"] = "gsk_test"; j.LLM_PROVIDER = "groq"
b = j.Brain(); b.history = []
spoken = []
for r in ("[IGNORE]", "[IGNORE].", "  [ignore] "):
    FakeCompletions.reply = r; spoken.clear(); hist0 = len(b.history)
    out = b.ask_stream("Bop", on_sentence=spoken.append)
    check(f"stream {r!r}: nothing spoken, returns empty, history clean", spoken == [] and out == "" and len(b.history) == hist0)
FakeCompletions.reply = "It is ten past nine, boss."; spoken.clear()
out = b.ask_stream("what time is it", on_sentence=spoken.append)
check("normal reply still spoken", spoken == ["It is ten past nine, boss."] and out.startswith("It is"))
FakeCompletions.reply = "[IGNORE]"
check("non-stream ask() ignores too", b.ask("hmm") == "")

# ---------- 7. greeting cooldown ----------
j._GREETING_STAMP = os.path.join(tempfile.mkdtemp(), "last.txt")
check("first greeting allowed", j.should_greet() is True)
check("second greeting within cooldown blocked", j.should_greet() is False)
open(j._GREETING_STAMP, "w").write(str(time.time() - 5 * 3600))
check("greets again after cooldown", j.should_greet() is True)

# ---------- 8. full simulated session: the exact bug from the log ----------
said, beeps = [], []
j.speak = lambda text, block=True: said.append(text)
j.beep = lambda *a, **k: beeps.append(a)
j.tts_worker = types.SimpleNamespace(wait=lambda: None, is_busy=lambda: False, speak=lambda *a, **k: said.append(a[0]))
j.time.sleep = lambda s: None
def run_session(transcripts, brain=None):
    said.clear(); beeps.clear()
    it = iter(transcripts)
    j.record_utterance = lambda q, **k: (np.ones(16000, dtype=np.float32) * 0.1) if True else None
    seq = list(transcripts)
    def rec(q, **k):
        return np.ones(16000, dtype=np.float32) * 0.1 if seq else None
    def tr(stt, a): return seq.pop(0)
    j.record_utterance = rec; j.transcribe = tr
    j.session(queue.Queue(), None, brain or b)
    return list(said)
FakeCompletions.reply = "[IGNORE]"
check("log case: wake -> 'Bop.' -> says NOTHING", run_session(["Bop."]) == [])
check("wake -> 'Thank you.' -> says NOTHING", run_session(["Thank you."]) == [])
FakeCompletions.reply = "Got it, boss. All systems nominal."
check("noise never even reaches the LLM", run_session(["you"]) == [])
check("'that's all' ends silently (chime, no speech)", run_session(["that's all"]) == [] and len(beeps) >= 2)
r = run_session(["what is my real name"])
check("real command still answered", len(r) == 1 and "roshan" in r[0].lower())
FakeCompletions.reply = "[IGNORE]"
check("noise-like text the gate misses: LLM [IGNORE] keeps him silent", run_session(["blah blah something unclear"]) == [])

print("\n==== %d failure(s) ====" % len(FAILS))
for f in FAILS: print("  -", f)
sys.exit(1 if FAILS else 0)
