# Prompt to paste into Antigravity

Open this folder as your workspace, open the agent panel, and paste everything inside the block below.

```
You are my setup engineer. This workspace contains "JARVIS", a Python voice assistant
(jarvis.py, requirements.txt, README.md). It wakes on the wake word "Hey Jarvis" or a double clap,
records my command, transcribes it locally with faster-whisper, answers using an LLM API
(a free LLM such as Groq or Gemini, via an OpenAI-compatible API), and speaks the reply with pyttsx3.

YOUR GOAL: get it running end to end on my laptop, verify every feature, and set it to start
automatically when I turn the laptop on. Work through the phases in order. Show me a short
plan first, then execute. After each phase, report PASS or FAIL with evidence.

GROUND RULES
- Read README.md and jarvis.py first. Keep the existing file structure and make the smallest
  fixes needed. Explain every code change you make.
- Never hardcode or print my API key. Never write it into any file in the project. Read it only
  from environment variables (GROQ_API_KEY, GEMINI_API_KEY, etc).
- Ask before installing anything system-wide or outside a project virtual environment.
- Ask me to do anything that needs my hands (speaking, clapping, granting mic permission,
  entering the API key) and wait for my confirmation.
- If something fails, diagnose the root cause, fix it, and retry. Do not skip a failing step.

PHASE 1 - Environment
1. Detect my OS and Python version (Python 3.10-3.12 is best; if I have 3.13+ warn me and
   suggest installing 3.12).
2. Create a virtual environment in ./venv and activate it.
3. pip install -r requirements.txt. If any package fails to install (for example sounddevice,
   openwakeword, pyttsx3 or faster-whisper), find the cause for my OS and fix it.
4. Run: python -c "import sounddevice; print(sounddevice.query_devices())"
   Confirm a working default microphone and speaker exist. Tell me if the wrong device is default.

PHASE 2 - Free LLM key
1. Ask me which provider I want: Groq (key from console.groq.com/keys), Gemini (key from
   aistudio.google.com/apikey), or Ollama (local, no key). Explain the choice in one line each.
2. Check whether the matching environment variable is set (GROQ_API_KEY or GEMINI_API_KEY) and
   whether JARVIS_PROVIDER is set to my choice. If not, give me the exact commands for my OS
   (Windows: setx ... then reopen the terminal) and wait for me to run them. For Ollama, help me
   install it and run: ollama pull llama3.2
3. Run: python jarvis.py --test-llm   and show the reply.
4. If it fails with "model not found", run: python jarvis.py --models, pick a suitable fast
   model from the list, and set JARVIS_MODEL. If it fails with a rate-limit error, tell me and
   suggest waiting or switching provider.

PHASE 3 - Components, one at a time
1. Text-to-speech: make pyttsx3 speak a sentence twice in a row (this catches the known
   "speaks once, then hangs" bug). Ask me to confirm I heard both.
2. Wake word: load the openWakeWord model (it downloads on first run). Ask me to say
   "Hey Jarvis" and confirm detection works. If it triggers too easily or ignores me, tune
   WAKE_THRESHOLD.
3. Speech-to-text: record a short phrase from me, transcribe it with the model in WHISPER_MODEL
   and print the result. If my accent is mis-recognized, suggest "small.en".
4. Claps: run python jarvis.py --levels, ask me to clap and speak normally, read the peak
   values, and set CLAP_MIN_PEAK so claps trigger it but normal speech never does.

PHASE 4 - Full run
1. Run python jarvis.py. Ask me to test, in this order:
   a. say "Hey Jarvis", then "what time is it"
   b. clap twice, then "open YouTube"
   c. say "Hey Jarvis", then ask a general question like "explain black holes in two
      sentences", then a follow-up without repeating the wake word
   d. say "that's all" (it should go back to standby), then "quit Jarvis" (it should exit)
2. Fix anything that misbehaves. Show me the final log.

PHASE 5 - Auto-start at boot
1. Create a start script appropriate for my OS (on Windows: start_jarvis.bat that activates the
   venv and runs pythonw jarvis.py).
2. Put it in the right autostart location (Windows: shell:startup, Mac: launchd or Login Items,
   Linux: systemd user service or autostart entry). Ask me before touching anything outside
   this folder.
3. Tell me how to test it by restarting, and how to disable it later.

PHASE 6 - Wrap-up
1. Update README.md with anything that was different on my machine.
2. Give me a final checklist: what works, what I tuned (with values), and what is still left.
3. Suggest the 3 best upgrades next (for example a nicer voice with edge-tts, more voice skills,
   a custom "Hey Friday" wake-word model).

START NOW with Phase 1. Show me your plan first.
```

## Optional follow-up prompts

**Add the name "Friday":**
```
Help me add a custom wake word "Hey Friday" to this project using openWakeWord. Walk me through
training the model (use the official openWakeWord training notebook), save the .onnx file in a
models/ folder, update WAKE_MODELS, set NAME = "Friday", pick a fitting voice, and test it.
```

**Better voice:**
```
Replace the pyttsx3 voice in speak() with edge-tts for a smoother, more natural voice. Keep
pyttsx3 as an offline fallback if there is no internet. Let me choose from a few voices.
```

**More skills:**
```
Add these voice commands to run_builtin(): volume up/down/mute, take a screenshot, lock the
screen, set a timer, and tell me today's weather for my city. Keep each skill small and safe.
```
