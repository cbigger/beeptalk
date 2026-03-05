"""
beeper.py

Loads configuration from beeper.toml (looked up next to this script, or pass
a path as the first argument before text, see CLI section below).

Pipeline:
  text stream → Beeper.feed(chunk) → event queue → audio worker thread

Usage:
  python beeper.py "some text"
  python beeper.py --config /path/to/custom.toml "some text"
  echo "some text" | python beeper.py -
  python beeper.py          # interactive stdin
"""
import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

import sys
import queue
import string
import threading
import tomllib
from pathlib import Path

import numpy as np
import pygame
import pyphen
# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
DEFAULT_CONFIG_PATH = Path(__file__).parent / "beeper.toml"

def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)

def build_freq_table(cfg: dict) -> np.ndarray:
    """
    Returns a 26-element array where freqs[i] is the frequency for letter-index i.
    """
    low  = cfg["pitch"]["freq_low"]
    high = cfg["pitch"]["freq_high"]
    base = np.linspace(low, high, 26)
    # letter_indices maps letter → its position in the freq table
    mapping = cfg["pitch"]["letter_indices"]
    freqs = np.empty(26)
    for letter, idx in mapping.items():
        freqs[idx] = base[idx]
    return freqs

def letter_index(ch: str, mapping: dict) -> int:
    return mapping.get(ch.lower(), 0)

# ---------------------------------------------------------------------------
# DSP
# ---------------------------------------------------------------------------
def make_beep(freq: float, duration_ms: int, cfg: dict) -> np.ndarray:
    sr     = cfg["audio"]["sample_rate"]
    vol    = cfg["audio"]["volume"]
    atk_ms = cfg["audio"]["attack_ms"]
    dec_ms = cfg["audio"]["decay_ms"]

    n   = int(sr * duration_ms / 1000)
    t   = np.linspace(0, duration_ms / 1000, n, endpoint=False)
    wav = (np.sin(2 * np.pi * freq * t) * vol).astype(np.float32)

    atk = int(sr * atk_ms / 1000)
    dec = int(sr * dec_ms / 1000)
    if atk: wav[:atk]  *= np.linspace(0, 1, atk)
    if dec: wav[-dec:] *= np.linspace(1, 0, dec)

    stereo = np.stack([wav, wav], axis=-1)
    return (stereo * 32767).astype(np.int16)

def make_silence(duration_ms: int, cfg: dict) -> np.ndarray:
    sr = cfg["audio"]["sample_rate"]
    n  = int(sr * duration_ms / 1000)
    return np.zeros((n, 2), dtype=np.int16)

# ---------------------------------------------------------------------------
# Syllable → pitch + duration
# ---------------------------------------------------------------------------
def syllable_frequency(syllable: str, freqs: np.ndarray, mapping: dict) -> float:
    letters = [ch for ch in syllable.lower() if ch.isascii() and ch.isalpha()]
    if not letters:
        return float(freqs[0])
    idx = sum(letter_index(ch, mapping) for ch in letters) % 26
    return float(freqs[idx])

def syllable_duration_ms(syllable: str, cfg: dict) -> int:
    dc  = cfg["duration"]
    n   = max(1, sum(1 for ch in syllable if ch.isascii() and ch.isalpha()))
    ms  = n * dc["ms_per_letter"]
    return int(np.clip(ms, dc["min_ms"], dc["max_ms"]))

# ---------------------------------------------------------------------------
# Silence duration from punctuation character
# ---------------------------------------------------------------------------
def silence_ms(ch: str, cfg: dict) -> int:
    sc = cfg["silence"]
    if ch in (' ', '\t'): return sc["space_ms"]
    if ch == ',':         return sc["comma_ms"]
    return sc["other_ms"]

# ---------------------------------------------------------------------------
# Audio worker
# ---------------------------------------------------------------------------
_STOP = object()

class AudioWorker:
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._q   = queue.Queue()
        self._t   = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        sr = self._cfg["audio"]["sample_rate"]
        pygame.mixer.init(frequency=sr, size=-16, channels=2, buffer=512)
        while True:
            item = self._q.get()
            if item is _STOP:
                self._q.task_done()
                break
            sound = pygame.sndarray.make_sound(item)
            sound.play()
            ms = int(len(item) / sr * 1000)
            pygame.time.wait(ms)
            self._q.task_done()
        pygame.mixer.quit()

    def enqueue(self, pcm: np.ndarray):
        self._q.put(pcm)

    def drain(self):
        self._q.join()

    def stop(self):
        self._q.put(_STOP)
        self._t.join()

# ---------------------------------------------------------------------------
# Stream consumer
# ---------------------------------------------------------------------------
class Beeper:
    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH):
        self._cfg     = load_config(config_path)
        self._freqs   = build_freq_table(self._cfg)
        self._mapping = self._cfg["pitch"]["letter_indices"]
        self._dic     = pyphen.Pyphen(lang=self._cfg["language"]["pyphen_lang"])
        self._worker  = AudioWorker(self._cfg)
        self._buf     = []

    def feed(self, chunk: str):
        for ch in chunk:
            if ch.isalpha():
                self._buf.append(ch)
            else:
                self._flush_word()
                if ch in (' ', '\t') or ch in ('\n', '\r') or ch in string.punctuation:
                    self._worker.enqueue(make_silence(silence_ms(ch, self._cfg), self._cfg))

    def _flush_word(self):
        if not self._buf:
            return
        word = ''.join(self._buf)
        self._buf.clear()
        for syllable in self._split_syllables(word):
            freq = syllable_frequency(syllable, self._freqs, self._mapping)
            dur  = syllable_duration_ms(syllable, self._cfg)
            self._worker.enqueue(make_beep(freq, dur, self._cfg))

    def _split_syllables(self, word: str) -> list:
        return [p for p in self._dic.inserted(word).split('-') if p]

    def flush(self):
        self._flush_word()

    def stop(self):
        self.flush()
        self._worker.drain()
        self._worker.stop()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    args = sys.argv[1:]

    config_path = DEFAULT_CONFIG_PATH
    if args and args[0] == "--config":
        config_path = Path(args[1])
        args = args[2:]

    beeper = Beeper(config_path=config_path)

    if args and args[0] != "-":
        beeper.feed(" ".join(args))
        beeper.stop()
    else:
        try:
            for line in sys.stdin:
                beeper.feed(line)
        except KeyboardInterrupt:
            pass
        finally:
            beeper.stop()
