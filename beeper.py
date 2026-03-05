"""
beeper.py

Loads all valid .toml configs from a directory at startup, keyed by filename
stem. The active config (emotion) can be swapped at any time via set_emotion().

Pipeline:
  text stream → Beeper.feed(chunk) → event queue → audio worker thread

Usage:
  python beeper.py "some text"
  python beeper.py --emotion harmonic-minor "some text"
  python beeper.py --config-dir ./configs --emotion melodic-minor "some text"
  echo "some text" | python beeper.py --emotion natural-minor -
  python beeper.py          # interactive stdin, default emotion
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
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_CONFIG_DIR = Path(__file__).parent
DEFAULT_EMOTION    = "beeper" #None   # falls back to first loaded config alphabetically

REQUIRED_KEYS = [
    ("audio", "sample_rate"),
    ("audio", "volume"),
    ("audio", "attack_ms"),
    ("audio", "decay_ms"),
    ("pitch", "scale_frequencies"),
    ("pitch", "letter_indices"),
    ("duration", "ms_per_letter"),
    ("duration", "min_ms"),
    ("duration", "max_ms"),
    ("silence", "space_ms"),
    ("silence", "comma_ms"),
    ("silence", "other_ms"),
    ("language", "pyphen_lang"),
]

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def _is_valid_config(cfg: dict) -> bool:
    for keys in REQUIRED_KEYS:
        node = cfg
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return False
            node = node[k]
    return True

def load_configs(config_dir: Path) -> dict:
    """
    Loads all .toml files from config_dir that pass validation.
    Returns dict of {stem: cfg_dict}.
    """
    configs = {}
    for path in sorted(config_dir.glob("*.toml")):
        try:
            with open(path, "rb") as f:
                cfg = tomllib.load(f)
            if _is_valid_config(cfg):
                configs[path.stem] = cfg
        except Exception:
            pass  # silently skip malformed files
    return configs

def build_freq_table(cfg: dict) -> np.ndarray:
    scale = cfg["pitch"]["scale_frequencies"]
    return np.array([scale[i % len(scale)] for i in range(26)], dtype=float)

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
    n = int(cfg["audio"]["sample_rate"] * duration_ms / 1000)
    return np.zeros((n, 2), dtype=np.int16)

# ---------------------------------------------------------------------------
# Punctuation classification
# ---------------------------------------------------------------------------
def silence_ms(ch: str, cfg: dict) -> int:
    sc = cfg["silence"]
    if ch in (' ', '\t'): return sc["space_ms"]
    if ch == ',':         return sc["comma_ms"]
    return sc["other_ms"]

# ---------------------------------------------------------------------------
# Syllable → pitch + duration
# ---------------------------------------------------------------------------
def syllable_frequency(syllable: str, freqs: np.ndarray, mapping: dict) -> float:
    letters = [ch for ch in syllable.lower() if ch.isascii() and ch.isalpha()]
    if not letters:
        return float(freqs[0])
    idx = sum(letter_index(ch, mapping) for ch in letters) % len(freqs)
    return float(freqs[idx])

def syllable_duration_ms(syllable: str, cfg: dict) -> int:
    dc = cfg["duration"]
    n  = max(1, sum(1 for ch in syllable if ch.isascii() and ch.isalpha()))
    return int(np.clip(n * dc["ms_per_letter"], dc["min_ms"], dc["max_ms"]))

# ---------------------------------------------------------------------------
# Audio worker
# ---------------------------------------------------------------------------
_STOP = object()

class AudioWorker:
    def __init__(self, sample_rate: int):
        self._sr = sample_rate
        self._q  = queue.Queue()
        self._t  = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        pygame.mixer.init(frequency=self._sr, size=-16, channels=2, buffer=512)
        while True:
            item = self._q.get()
            if item is _STOP:
                self._q.task_done()
                break
            sound = pygame.sndarray.make_sound(item)
            sound.play()
            ms = int(len(item) / self._sr * 1000)
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
    def __init__(self,
                 config_dir: Path = DEFAULT_CONFIG_DIR,
                 emotion: str = DEFAULT_EMOTION):

        self._configs = load_configs(config_dir)
        if not self._configs:
            raise RuntimeError(f"No valid .toml configs found in {config_dir}")

        self._emotion = None
        self._cfg     = None
        self._freqs   = None
        self._mapping = None
        self._dic     = None
        self._worker  = None
        self._buf     = []

        # Set starting emotion — fall back to first alphabetically
        start = emotion if emotion in self._configs else next(iter(self._configs))
        self._apply_emotion(start)

    def _apply_emotion(self, name: str):
        if name not in self._configs:
            raise ValueError(
                f"Unknown emotion '{name}'. Available: {', '.join(self._configs)}"
            )
        self._emotion = name
        self._cfg     = self._configs[name]
        self._freqs   = build_freq_table(self._cfg)
        self._mapping = self._cfg["pitch"]["letter_indices"]

        lang = self._cfg["language"]["pyphen_lang"]
        if self._dic is None or self._dic.language != lang:
            self._dic = pyphen.Pyphen(lang=lang)

        sr = self._cfg["audio"]["sample_rate"]
        if self._worker is None:
            self._worker = AudioWorker(sample_rate=sr)

    def set_emotion(self, name: str):
        """Switch the active config mid-stream. Takes effect on the next word."""
        self._apply_emotion(name)

    @property
    def emotion(self) -> str:
        return self._emotion

    @property
    def emotions(self) -> list:
        return list(self._configs.keys())

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

    config_dir = DEFAULT_CONFIG_DIR
    emotion    = DEFAULT_EMOTION

    while args and args[0].startswith("--"):
        flag = args[0]
        if flag == "--config-dir" and len(args) > 1:
            config_dir = Path(args[1])
            args = args[2:]
        elif flag == "--emotion" and len(args) > 1:
            emotion = args[1]
            args = args[2:]
        else:
            print(f"Unknown flag: {flag}", file=sys.stderr)
            sys.exit(1)

    beeper = Beeper(config_dir=config_dir, emotion=emotion)
    print(f"Loaded emotions: {', '.join(beeper.emotions)}", file=sys.stderr)
    print(f"Active emotion:  {beeper.emotion}", file=sys.stderr)

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
