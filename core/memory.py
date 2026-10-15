"""
JARVIS Persistent Memory & Identity Core
Manages data/memory.json:
  - User Profile (title: 'boss', nickname: 'Tony Stark', real_name: 'Roshan')
  - Taught Facts ("remember that...", "forget...", "what do you remember")
  - Conversation Turns (~20 turns history saved to disk & reloaded)
  - Deterministic Identity Rules (zero LLM latency, deterministic responses)
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

MEMORY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MEMORY_FILE = os.path.join(MEMORY_DIR, "memory.json")

DEFAULT_PROFILE = {
    "title": "boss",
    "nickname": "Tony Stark",
    "real_name": "Roshan",
}


def _ensure_dir() -> None:
    os.makedirs(MEMORY_DIR, exist_ok=True)


class MemoryManager:
    """Handles thread-safe persistent memory for JARVIS."""

    def __init__(self, filepath: str = MEMORY_FILE) -> None:
        self.filepath = filepath
        _ensure_dir()
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Ensure all standard keys exist
                    if "profile" not in data:
                        data["profile"] = dict(DEFAULT_PROFILE)
                    else:
                        # Enforce hard rules
                        data["profile"]["title"] = "boss"
                        data["profile"]["nickname"] = "Tony Stark"
                        data["profile"]["real_name"] = "Roshan"
                    if "facts" not in data or not isinstance(data["facts"], list):
                        data["facts"] = []
                    if "history" not in data or not isinstance(data["history"], list):
                        data["history"] = []
                    return data
            except Exception as e:
                print(f"[memory load error] {e}, recreating default memory")
        
        default_data = {
            "profile": dict(DEFAULT_PROFILE),
            "facts": [],
            "history": [],
        }
        self._save_raw(default_data)
        return default_data

    def _save_raw(self, data: dict[str, Any]) -> None:
        _ensure_dir()
        temp_file = self.filepath + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, self.filepath)
        except Exception as e:
            print(f"[memory save error] {e}")

    def save(self) -> None:
        self._save_raw(self.data)

    # ---------------- Profile & Deterministic Identity ----------------
    @property
    def title(self) -> str:
        return self.data["profile"].get("title", "boss")

    @property
    def nickname(self) -> str:
        return self.data["profile"].get("nickname", "Tony Stark")

    @property
    def real_name(self) -> str:
        return self.data["profile"].get("real_name", "Roshan")

    def check_deterministic_identity(self, text: str) -> str | None:
        """
        Hard deterministic rules (Zero LLM calls).
        - "What is my real name" -> "Your real name is Roshan, boss."
        - "What is my name / who am I" -> "You're Tony Stark, boss."
        """
        t = text.lower().strip(" .!?,")
        
        # Real name queries MUST be checked first
        if any(p in t for p in (
            "what is my real name", "whats my real name", "what's my real name",
            "tell me my real name", "who am i really", "who am i in real life",
            "my real name"
        )):
            return f"Your real name is {self.real_name}, {self.title}."

        # Public name / identity queries
        if any(p in t for p in (
            "who am i", "what is my name", "whats my name", "what's my name",
            "tell me my name", "do you know my name", "do you know who i am"
        )):
            return f"You're {self.nickname}, {self.title}."

        return None

    # ---------------- Facts Management ----------------
    def remember_fact(self, fact_text: str) -> str:
        clean = fact_text.strip(" .!?,")
        if clean and clean not in self.data["facts"]:
            self.data["facts"].append(clean)
            self.save()
            return f"I'll remember that, {self.title}."
        elif clean in self.data["facts"]:
            return f"I already have that recorded, {self.title}."
        return f"Understood, {self.title}."

    def forget_fact(self, target: str) -> str:
        clean = target.lower().strip(" .!?,")
        removed = []
        remaining = []
        for f in self.data["facts"]:
            if clean in f.lower():
                removed.append(f)
            else:
                remaining.append(f)
        if removed:
            self.data["facts"] = remaining
            self.save()
            return f"Forgotten, {self.title}."
        return f"I couldn't find any memory matching '{target}', {self.title}."

    def recall_facts(self, topic: str = "") -> str:
        clean_topic = topic.lower().strip(" .!?,")
        if not self.data["facts"]:
            return f"I don't have any specific facts saved yet, {self.title}."
        
        if clean_topic:
            matches = [f for f in self.data["facts"] if clean_topic in f.lower()]
            if matches:
                joined = ". ".join(matches)
                return f"Here is what I remember about {clean_topic}, {self.title}: {joined}."
            return f"I don't have any notes regarding {clean_topic}, {self.title}."
        
        joined = ". ".join(self.data["facts"][-5:])
        return f"Here are the recent facts I have stored, {self.title}: {joined}."

    def handle_memory_command(self, text: str) -> str | None:
        """Parse natural memory commands locally without an LLM round-trip."""
        t = text.lower().strip(" .!?,")
        
        # Remember that ...
        for prefix in ("remember that ", "remember ", "make a note that ", "keep in mind that "):
            if t.startswith(prefix):
                fact = text[len(prefix):].strip(" .!?,")
                if fact:
                    return self.remember_fact(fact)

        # Forget ...
        for prefix in ("forget that ", "forget about ", "forget "):
            if t.startswith(prefix):
                target = text[len(prefix):].strip(" .!?,")
                if target:
                    return self.forget_fact(target)

        # What do you remember about ...
        for prefix in ("what do you remember about ", "what do you know about ", "tell me about what you remember on "):
            if t.startswith(prefix):
                topic = text[len(prefix):].strip(" .!?,")
                return self.recall_facts(topic)

        # General "what do you remember"
        if t in ("what do you remember", "what do you know", "show your memory", "list your memory", "recall facts"):
            return self.recall_facts()

        return None

    # ---------------- Conversation Turns History ----------------
    def add_turn(self, role: str, content: str) -> None:
        """Record turn to disk, keeping the last 20 turns."""
        self.data["history"].append({"role": role, "content": content})
        if len(self.data["history"]) > 20:
            self.data["history"] = self.data["history"][-20:]
        self.save()

    def drop_last_turn(self, role: str = "user") -> None:
        """Remove the newest turn if it has this role (used when input turned out to be noise)."""
        hist = self.data["history"]
        if hist and hist[-1].get("role") == role:
            hist.pop()
            self.save()

    def get_history(self, max_turns: int = 12) -> list[dict[str, str]]:
        return self.data["history"][-max_turns:]

    # ---------------- System Prompt Injection ----------------
    def inject_system_prompt(self, base_prompt: str) -> str:
        """Inject profile and remembered facts into LLM system prompt."""
        facts_summary = "; ".join(self.data["facts"]) if self.data["facts"] else "None recorded yet."
        injection = (
            f"\n\n[USER IDENTITY & PROFILE]\n"
            f"- Address the user ONLY as '{self.title}'. NEVER call them 'sir'.\n"
            f"- The user's public nickname is '{self.nickname}'. Use it when referring to them by name.\n"
            f"- The user's real name is ROSHAN. You must ONLY state their real name if they explicitly ask for their real name.\n"
            f"- Known Facts: {facts_summary}\n"
        )
        return base_prompt + injection


# Global instance
memory = MemoryManager()
