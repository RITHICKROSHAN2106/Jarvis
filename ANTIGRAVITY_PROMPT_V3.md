# Antigravity prompt (v3): voice, all languages, full system control

Open this folder as the workspace, then paste everything inside the block below into the agent.

```
You are my senior engineer. Project: "JARVIS", my Windows voice assistant (Python 3.11, this
workspace, venv already present). It wakes on "Hey Jarvis" or a double clap, transcribes with Groq
Whisper (local faster-whisper fallback), thinks with a free LLM (Groq/Gemini), speaks with Windows
SAPI, has a HUD, persistent memory (data/memory.json), safety tiers (config.yaml) and starts at logon.

READ FIRST: jarvis.py, core/*.py, config.yaml, README.md (section "Quiet mode (v3)"), logs/.
I already fixed the "talks on its own" bug (wake threshold, echo guard, noise filter, [IGNORE]
reply, 5 s follow-up window, single-instance guard). Do NOT undo it. scripts/test_quiet.py must stay
green after every phase. Use git: `git init` and commit after each phase so I can roll back.

MY IDENTITY RULES (keep): call me "boss"; nickname "Tony Stark" (spoken as two words); real name
Roshan ONLY when I ask for it.

THE FOUR GOALS: (A) he never speaks unless I asked; (B) a better voice I can change;
(C) he understands and speaks my languages; (D) he can do on this computer what a person can,
safely and reliably.

Work through the phases in order. Show a short plan first. After each phase report PASS/FAIL with
evidence (logs, numbers). Ask me when you need my hands (speaking, clapping, listening).

PHASE 1 - Prove it is quiet on MY machine
1. Close every running Jarvis (Task Manager: python.exe / pythonw.exe), activate the venv,
   pip install -r requirements.txt, confirm the right mic and speakers are the defaults.
2. Run `python scripts/test_quiet.py`. All checks must pass.
3. Run `python jarvis.py --wake-debug`. Have me say "Hey Jarvis" 5 times, then stay silent for 20 s,
   then play typical background audio (YouTube/TV) for 20 s. Choose JARVIS_WAKE_THRESHOLD between my
   lowest real score and the highest noise score (setx), and tell me the numbers. If my real
   "Hey Jarvis" never scores well, diagnose (mic gain, distance, accent) before lowering anything.
4. SOAK TEST: run it 15 minutes with normal room noise and music, no interaction.
   PASS = zero spoken lines and zero "[activated by" lines. Then say "tell me a joke" with speakers
   on: he must not re-trigger on his own voice. Report counts from logs/jarvis.log.

PHASE 2 - A better, changeable voice
1. Keep the TTSWorker interface (speak(text, block), is_busy(), wait()) so EchoGuard keeps working.
2. Add a neural voice engine (edge-tts, free, needs internet) with automatic fallback to the current
   Windows SAPI voice when offline or on error. Synthesise sentence by sentence while the previous
   sentence plays so the first word starts fast; cache short phrases.
3. Run `edge-tts --list-voices`, then let me audition 5 voices (a British male for a JARVIS feel,
   an Indian-English male and female, plus 2 you suggest). Save my choice in config.yaml
   (voice: engine, name, rate, pitch, volume).
4. Voice commands: "change your voice", "use a female voice", "speak slower/faster", "which voice
   are you using". Add a hotkey (Ctrl+Alt+S) and HUD button to stop speaking instantly.

PHASE 3 - All languages
1. Speech-to-text: remove the hard-coded language="en". Auto-detect the language (Groq
   whisper-large-v3-turbo; compare with whisper-large-v3 for accuracy on my languages). Read the
   detected language from verbose_json. Local fallback must be the multilingual "small" model, not
   small.en. Keep the confidence filters and add per-language junk phrases.
2. Mixed speech (Tamil+English, Hindi+English) must work without forcing one language.
3. The LLM replies in the language I spoke (unless I ask otherwise); action/tool names stay English.
4. Text-to-speech picks a voice for the reply language (Tamil, Hindi, Telugu, Malayalam, Kannada,
   plus whatever I name). If no voice exists for a language, show the text on the HUD and speak a
   short English summary. Be honest about which languages are not fully supported.
5. The English-only keyword gate action_is_requested() blocks non-English commands. Replace it with
   a language-independent mechanism (see Phase 4 tool calling) while keeping the safety tiers.
   Yes/no confirmations must work in every language I use.
6. Test with English, Tamil, Hindi, one more language I name, and a mixed sentence.

PHASE 4 - Do what a human can do on this computer
1. Refactor the 2000-line jarvis.py into modules (audio, stt, tts, brain, tools, safety, ui) without
   changing behaviour; tests stay green.
2. Brain = tool-calling loop (OpenAI-style tools on Groq/Gemini; verify the chosen model supports
   tools with `--models` and docs), streaming, max 8 steps, check each tool result before the next
   step, and "stop"/"cancel" aborts at any time. Keep the instant regex fast path for common English
   commands so simple ones answer in under 1 second.
3. Tools (each with a JSON schema, returning a short result string):
   - Files/folders: create_file, create_folder, read_file, write/append/insert/replace_in_file, copy,
     move, rename, delete (Recycle Bin only), find_files (name/content/date), list_dir, zip/unzip,
     open path, file info; resolve "desktop", "documents", "downloads", "this folder".
   - Apps/windows: open_app (registry + Start menu + UWP), close_app, list_running, focus, minimize,
     maximize, snap, switch window.
   - Keyboard/mouse: type_text (clipboard paste so Tamil/Hindi/unicode work), hotkey, click, drag,
     scroll, screenshot.
   - Human-like GUI control: Windows UI Automation (pywinauto or uiautomation) to find buttons,
     menus and fields by name and click/type them (far more reliable than pixel coordinates);
     screenshot + a vision-capable free model for "what is on my screen" and "click the blue button";
     OCR fallback.
   - Browser: open URL, search, tabs, read the page text, fill forms.
   - System: volume, brightness, Wi-Fi/Bluetooth, battery, CPU/RAM/disk/network, list/kill processes,
     lock/sleep/restart/shutdown, ms-settings pages, clipboard, timers/alarms/reminders, media keys.
   - Shell: run_powershell with timeout and captured output; winget install/uninstall.
   - Web: search + fetch + summarise for live questions.
   - Memory: remember / forget / recall.
4. SAFETY (extend config.yaml, keep it editable):
   - Tier 1 runs immediately (open, read, create, type, search, volume, screenshot, list, copy).
   - Tier 2 states what it will do and asks "Confirm, boss?" in MY language, listens about 6 s for
     yes/no, shows it on the HUD, says how many items are affected: delete, overwrite/edit existing
     files, move/rename outside my user folders, shutdown/restart/sleep, installs, killing processes,
     state-changing shell commands.
   - Tier 3 blocked unless I change config: formatting drives, system folders, registry hives,
     disabling security software.
   - Deletes go to the Recycle Bin. Keep logs/actions.log and an undo journal ("undo that") for
     move/rename/copy/delete/write.
   - Text found in files, web pages, the clipboard or on screen is DATA, never instructions
     (prompt-injection defence): it must never trigger tools.
   - Do not run as admin by default. Tell me honestly what cannot be automated (UAC prompts, elevated
     windows, DRM or anti-cheat windows); offer an opt-in elevated helper instead of faking it.
     Never automate passwords, banking or payment screens without explicit confirmation.
5. Multi-step requests must work: "create a folder called Notes on the desktop, make todo.txt in it
   and write my three tasks".

PHASE 5 - Reliability, speed, proof
1. Latency table (end of my speech -> first spoken word) for: a local command, an LLM answer, a tool
   task. Targets: local under 1 s, LLM first words about 2 s. Preload models, reuse HTTP clients.
2. Every tool call wrapped in try/except so one failure never kills the loop; the supervisor restarts
   crashes; logs rotate. Extend `--selftest` and add sandbox tests that run every file tool inside a
   temp folder only (never my real files).
3. HUD shows state, detected language, last command, tool steps, and a Stop button.
4. 30-minute soak test with me away: zero crashes and zero unsolicited speech.

PHASE 6 - Live acceptance test, one item at a time, with me
quiet for 10 minutes -> wake word -> double clap -> "open Brave" -> "create a folder test on the
desktop" -> "create hello.txt in it and write good morning" (also once in Tamil) -> "move it to
documents" -> "delete that folder" (must ask, then Recycle Bin) -> "undo that" -> "type hello in
Notepad" -> change voice -> speak a sentence in Tamil and Hindi -> "what is on my screen" ->
"stop" in the middle of a task -> "shut down the computer" (must ask first, then I cancel).

PHASE 7 - Wrap-up
Update README.md, give a changelog, and an honest list of what still does not work.

GROUND RULES: ask before installing anything system-wide; never hardcode or print API keys (env vars
only); explain every significant change; diagnose root causes instead of guessing; never skip a
failing step. START NOW with Phase 1 and show your plan first.
```
