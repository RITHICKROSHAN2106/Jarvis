| **App Control**  | *"Open Chrome"*, *"Open Calculator"*, *"Open VS Code"*, *"Open Notepad"*, *"Close Calculator"*, *"Close Chrome"* |
# JARVIS — Tony Stark Voice Assistant for Windows 11 (Mark 85)

> *"Good evening, boss. Welcome back, Tony Stark. All systems are online and fully operational."*

A state-of-the-art, low-latency AI voice assistant engineered for **Windows 10/11**. Powered by high-speed hybrid speech recognition, sub-millisecond Windows SAPI text-to-speech, persistent memory, safety-tiered tool execution, live window dictation, and a futuristic desktop Arc Reactor HUD.

---

## ⚡ Speed & Latency Benchmarks

In Mark 85, pipeline latency was overhauled across all subsystems:

| Pipeline Component | Legacy Baseline | Mark 85 Optimized | Improvement |
| :--- | :--- | :--- | :--- |
| **Trailing Silence Cut-Off** | `1,800 ms` | **`550 ms`** | 1,250 ms saved with immediate audio cue |
| **Speech-to-Text (STT)** | `3,038 ms` (local CPU) | **`305 ms`** (Groq Turbo) | **~10.0x faster** (cloud turbo + local fallback) |
| **TTS Engine Initialization** | `858 ms` (pyttsx3) | **`0.6 ms`** (Persistent SAPI) | **~1,430x faster** (dedicated worker thread) |
| **Time-to-First-Speech (LLM)** | `1,402 ms` (full wait) | **`905 ms`** (Streaming) | Immediate speech while subsequent sentences stream |
| **Fast-Path Command Matching** | `N/A` (strict regex) | **`0.039 ms`** (`rapidfuzz`) | Sub-millisecond instant app/site resolution |
| **Total Turnaround (Local Command)**| **`5,706 ms` (~5.7s)** | **`855 ms` (<0.9s)** | **6.7x faster overall** |
| **Total Turnaround (LLM Query)** | **`7,098 ms` (~7.1s)** | **`1,760 ms` (~1.8s)** | **4.0x faster overall** |

---

## 🛡️ Identity & Hard Protocol Rules

* **Address User**: Always addressed as **`"boss"`**. Never *"sir"*.
* **Public Nickname**: **`"Tony Stark"`** (pronounced as two separate words for natural text-to-speech).
* **Real Name**: **`"Roshan"`**. Strictly confidential; spoken ONLY when explicitly queried for real name.
* **Deterministic Identity Core (Zero LLM Latency)**:
  * *"Who am I"* / *"What is my name"* $\rightarrow$ `"You're Tony Stark, boss."`
  * *"What is my real name"* $\rightarrow$ `"Your real name is Roshan, boss."`

---

## 🧠 Persistent Memory & Fact Learning (`data/memory.json`)

JARVIS maintains persistent conversational and factual memory across restarts:
* **Teach Facts**: *"Remember that the workshop passcode is Mark 85"* $\rightarrow$ Saved to disk atomically.
* **Recall Facts**: *"What do you remember about the workshop"* $\rightarrow$ Returns stored facts.
* **Forget Facts**: *"Forget the workshop"* $\rightarrow$ Removes matching facts from memory.
* **Conversation History**: Automatically persists the last 20 conversational turns across reboots.
* **Prompt Injection**: Injects known facts and identity constraints into every LLM completion.

---

## 🔒 Safety Layer & Tool Classification (`config.yaml`)

JARVIS features a 3-tier security architecture with mandatory audit logging in [`logs/actions.log`](file:///c:/Users/HRITIK/Desktop/jarvis/logs/actions.log):

* **Tier 1 (Safe — Immediate Execution)**:
  * App opening, URL navigation, Google/YouTube search, typing into Notepad/Calculator, volume/window control, reading files, listing directories, system telemetry.
* **Tier 2 (Destructive — Confirmation & Recycle Bin)**:
  * File deletion (`send2trash` moves files safely to the Windows Recycle Bin — **NEVER** unrecoverable permanent deletion).
  * Undo buffer: *"Undo last action"* verifies and traces trashed items.
  * System shutdown, reboot, lock, and process termination.
* **Tier 3 (Strictly Blocked — Policy Violation)**:
  * Drive formatting, mass deletion (`rmdir /s`, `del /f`), tampering with `C:\Windows` or `C:\Program Files`, registry deletion.

---

## 🎙️ Live Window Dictation Engine (`core/dictation.py`)

JARVIS streams real-time speech directly into whatever window currently has focus (Notepad, Word, VS Code, Browser inputs, etc.):
* **Activation**: *"Start dictation"*, *"Dictate mode"*, *"Take dictation"*.
* **Spoken Punctuation Conversion**:
  * *"period"* $\rightarrow$ `.`
  * *"comma"* $\rightarrow$ `,`
  * *"question mark"* $\rightarrow$ `?`
  * *"exclamation mark"* $\rightarrow$ `!`
  * *"colon"* / *"semicolon"* $\rightarrow$ `:` / `;`
  * *"new line"* / *"new paragraph"* $\rightarrow$ `\n` / `\n\n`
* **Real-time Sentence Capitalization**: Capitalizes beginnings of sentences after `.`, `!`, `?`, or newlines.
* **In-Flight Voice Editing**:
  * *"Scratch that"* / *"Delete last word"* $\rightarrow$ `Ctrl+Backspace`
  * *"Delete line"* / *"Clear line"* $\rightarrow$ `Shift+Home` + `Backspace`
  * *"Select all"* $\rightarrow$ `Ctrl+A`
  * *"Copy that"* / *"Paste that"* $\rightarrow$ `Ctrl+C` / `Ctrl+V`
* **Deactivation**: *"Stop dictation"*, *"Exit dictation"*, *"Finish dictation"*.

---

## 🖥️ Futuristic Desktop Arc Reactor HUD (`core/hud.py`)

A lightweight, frameless, semi-transparent (`alpha = 0.92`) Tkinter desktop widget:
* **Animated Arc Reactor**: Concentric rotating rings and a pulsing reactor core running at ~30 FPS with negligible CPU impact (<0.5%).
* **Visual State Transitions**:
  * `[ STANDBY ]`: Cyan ambient glow (`#00F2FE`).
  * `[ LISTENING ]`: Active gold/amber pulse (`#FFD166`).
  * `[ PROCESSING ]`: Fast-spinning purple reactor rings (`#A855F7`).
  * `[ SPEAKING ]`: Undulating energy waveform (`#00F2FE`).
  * `[ MUTED ]`: Red indicator badge (`#EF476F`).
* **Live Dual-Line Transcript**: Displays user speech and JARVIS spoken responses in real-time.
* **HUD Voice Controls**: *"Show HUD"*, *"Hide HUD"*, *"Mute mic"*, *"Unmute mic"*.

---

## 🚀 Windows Logon Startup & Supervisor Watchdog

1. **Watchdog Supervisor ([`supervisor.py`](file:///c:/Users/HRITIK/Desktop/jarvis/supervisor.py))**:
   * Runs JARVIS under `python -X faulthandler`.
   * Automatically restarts the assistant if an unhandled termination occurs, logging to [`logs/supervisor.log`](file:///c:/Users/HRITIK/Desktop/jarvis/logs/supervisor.log).
2. **Logon Startup Integration ([`scripts/install_startup.ps1`](file:///c:/Users/HRITIK/Desktop/jarvis/scripts/install_startup.ps1))**:
   * Installs `JARVIS.lnk` into the user's Windows Startup folder (`shell:startup`).
   * On logon, launches [`start_jarvis.vbs`](file:///c:/Users/HRITIK/Desktop/jarvis/start_jarvis.vbs), which waits 3 seconds for audio drivers to initialize, then starts `supervisor.py` silently in the background.
   * Greeted on desktop with a time-aware personalized welcome:
     > *"Good [morning/afternoon/evening], boss. Welcome back, Tony Stark. All systems are online and fully operational."*

---

## 🛠️ CLI Options & Verification

```bash
# Start JARVIS from any directory after installing the command
powershell -ExecutionPolicy Bypass -File scripts/install_command.ps1
jarvis

# Run JARVIS interactive voice assistant
python jarvis.py

# Run headless (without desktop HUD)
python jarvis.py --no-hud

# Run Phase 4 Automated Tool & Safety Self-Test Suite
python jarvis.py --selftest

# Run Phase 7 Full End-to-End Acceptance Test
python scripts/acceptance_test.py

# Live microphone level monitor (calibrate clap threshold)
python jarvis.py --levels

# Test language core connection
python jarvis.py --test-llm

# List accessible LLM models
python jarvis.py --models

# Install / Uninstall Windows Logon Startup
powershell -ExecutionPolicy Bypass -File scripts/install_startup.ps1
powershell -ExecutionPolicy Bypass -File scripts/uninstall_startup.ps1
```

The command installer adds a user-level `jarvis` command and does not require administrator access. Open a new terminal after installation so Windows reloads the updated user `PATH`. If the assistant exits, inspect `logs/supervisor.log` and `logs/jarvis_stderr.log`.

---

## 🗣️ Voice Commands Cheat Sheet

| Category | Example Commands |
| :--- | :--- |
| **Wake** | Say *"Jarvis"* or **clap twice**. *"Hey Jarvis"* also remains supported. |
| **Identity** | *"Who am I?"*, *"What is my name?"*, *"What is my real name?"* |
| **Memory** | *"Remember that [fact]"*, *"What do you remember about [topic]"*, *"Forget [topic]"* |
| **Dictation** | *"Start dictation"*, *"Scratch that"*, *"Delete line"*, *"Select all"*, *"Stop dictation"* |
| **App Control** | *"Open Chrome"*, *"Open Calculator"*, *"Open VS Code"*, *"Open Notepad"*, *"Close Calculator"* |
| **Direct Typing**| *"Write in Notepad that meeting is at 3 PM"*, *"Calculate 1500 times 12 in Calculator"* |
| **Media & Music**| *"Play [song name]"*, *"Play some music"*, *"Pause"*, *"Resume"*, *"Volume up"*, *"Mute"* |
| **Mouse & Keyboard** | *"Click at 500 300"*, *"Double click at 500 300"*, *"Right click at 500 300"*, *"Scroll down"*, *"Type meeting starts at 3 PM"*, *"Press enter"* |
| **Inside Apps & Tabs** | *"Close this tab"*, *"Open a new tab"*, *"Go back"*, *"Refresh page"*, *"Find invoice"*, *"Save document"*, *"Copy"*, *"Paste"*, *"Select all"* |
| **Window Mgmt**| *"Maximize window"*, *"Minimize window"*, *"Switch window"*, *"Show desktop"*, *"Take screenshot"* |
| **File Safety** | *"Trash file [path]"*, *"Undo"*, *"Read file [path]"*, *"System stats"* |
| **HUD Controls**| *"Show HUD"*, *"Hide HUD"*, *"Mute mic"*, *"Unmute mic"* |
| **Power Control**| *"Shut down the system"*, *"Cancel shutdown"*, *"Restart computer"*, *"Lock screen"* |
| **Current Information** | *"What is the latest news?"*, *"What is the current score?"*, *"What events are happening today?"*, *"What is the weather right now?"* |
| **Standby / Exit**| *"That's all"*, *"Stand by"*, *"Quit Jarvis"* |

---

## ⚙️ Configuration & Environment

Configuration is stored in `config.yaml` and environment variables:

```cmd
# Local Ollama language core (install Ollama, then pull the model)
ollama pull qwen2.5:3b
setx JARVIS_PROVIDER "ollama"
setx JARVIS_MODEL "qwen2.5:3b"

# Optional: Groq provides faster cloud speech-to-text when configured
setx GROQ_API_KEY "your-groq-key-here"

# Automatic startup and wake greetings are disabled by default
setx JARVIS_AUTO_GREETING "1"
```

---

## Quiet mode (v3): why he no longer talks on his own

If he used to speak without being asked, these were the causes, and each is now fixed:

| Cause | Fix |
|---|---|
| Wake threshold was `0.018` (fired on almost any sound) | Default `0.25`, and it must be hit in 2 of the last 4 frames. Tune with `python jarvis.py --wake-debug`. |
| He heard his own voice (the greeting even said "Jarvis") | He is deaf while speaking and for 1 s after; greeting no longer says his own name. |
| Whisper invents words from noise ("Bop.", "Thank you.") | Low-confidence segments are dropped, junk phrases are blocked, and the Whisper prompt is now a plain vocabulary list. |
| Junk was sent to the LLM, which answered "Got it, boss. All systems nominal." | The LLM replies `[IGNORE]` to noise and that is never spoken. |
| He kept listening 10 s after every reply and picked up TV or talking | Follow-up window is 5 s. |
| A click or cough started a recording | Needs about a quarter second of speech-like sound. |
| "Standing by." after thanks/that's all | Silent chime instead (set `QUIET_STANDBY = False` to hear it). |
| Greeting on every unlock | Once per 4 hours (`JARVIS_GREETING_COOLDOWN_HOURS`). |
| Two copies running (Startup + the `jarvis` command + supervisor) hear each other | Single-instance guard: a second copy refuses to start. |

### Tuning the wake word for your mic
```
python jarvis.py --wake-debug
```
Say "Hey Jarvis" 5 times and note the scores, then stay quiet or play your usual background sound and note the highest score. Set `JARVIS_WAKE_THRESHOLD` to a value between the two
(`setx JARVIS_WAKE_THRESHOLD "0.35"`, then reopen the terminal).

### Regression test (no mic, no internet, no API key needed)
```
python scripts/test_quiet.py
```
Checks the noise filter, wake-word confirmation, echo guard, click-vs-speech detection, `[IGNORE]` handling, greeting cooldown, and a simulated session of the exact "Bop." case.

### Applying this update
1. Close every running copy first (Task Manager > `python.exe` / `pythonw.exe`), or the old one keeps talking.
2. Copy these files over your project folder (your `venv`, `data` and `logs` are not touched).
3. `python jarvis.py --wake-debug`, then run it normally.

## Changeable neural voice

JARVIS keeps Windows SAPI as the offline-safe default. To use the optional free
Edge neural voice, install the requirements and change `voice.engine` in
`config.yaml` from `sapi` to `edge`. The configured voice is `voice.name`; rate,
pitch, volume, and the local short-phrase cache are configurable there as well.
Neural synthesis requires internet access. If synthesis or playback fails,
JARVIS logs the error and falls back to SAPI/pyttsx3 without changing the
`TTSWorker` interface or echo guard.

Useful voice settings:

```yaml
voice:
  engine: "edge"
  name: "en-GB-RyanNeural"
  rate: "+0%"
  pitch: "+0Hz"
  volume: "+0%"
```

You can override these for one run with `JARVIS_VOICE_ENGINE`, `JARVIS_VOICE_NAME`,
`JARVIS_VOICE_RATE`, `JARVIS_VOICE_PITCH`, and `JARVIS_VOICE_VOLUME`. Never put
API keys in this file.

## Multilingual speech recognition

Cloud Whisper now auto-detects the spoken language from `verbose_json`; the
English `language` override is intentionally not sent. The local fallback uses
the multilingual `small` model (override with `JARVIS_WHISPER_MODEL`) and also
auto-detects language. The selected Groq model can be changed with
`JARVIS_GROQ_STT_MODEL` for accuracy experiments, for example
`whisper-large-v3`.

## Controlled text-based system access

JARVIS can run approved PowerShell commands through the Tier 2
`run_powershell` tool. It runs as the normal logged-in user, never silently as
Administrator, captures output, limits execution to 60 seconds, and writes an
audit entry to `logs/actions.log`. Destructive or protected commands are
blocked before execution. A voice request such as “run PowerShell and show my
disk space” requires confirmation; saying “cancel” denies it.

File deletion remains deliberately safer than raw shell deletion: JARVIS
requires confirmation and moves files to the Windows Recycle Bin so an undo
path remains possible. It will not delete Windows, Program Files, Recovery,
Boot, or other protected system paths. Opening a normal `cmd.exe` window is
supported through the existing app launcher, but JARVIS does not grant that
window administrator rights.
