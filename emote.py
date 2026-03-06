"""
emote.py

Streaming emotion classifier. Accepts text via feed(), buffers it into
segments, and classifies each segment using j-hartmann/emotion-english-distilroberta-base.

Segments are flushed at:
  - Sentence boundaries (. ! ?)
  - Every WORD_COUNT_FALLBACK words if no sentence boundary appears

On each classification, calls the registered callback with the detected
emotion label and its confidence score.

Emotions: anger, disgust, fear, joy, neutral, sadness, surprise

Usage as a library:
  from emote import Emote

  def on_emotion(label, score):
      print(f"{label} ({score:.2f})")

  emote = Emote(callback=on_emotion)
  emote.feed("some streaming text")
  emote.flush()

Standalone (reads stdin, prints emotions to stdout):
  echo "I am so happy today!" | python emote.py
  cat file.txt | python emote.py
"""

import sys
import re
import threading
import queue
from transformers import pipeline

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL_NAME           = "j-hartmann/emotion-english-distilroberta-base"
WORD_COUNT_FALLBACK  = 10   # classify after this many words if no sentence boundary
SENTENCE_END_RE      = re.compile(r'[.!?]')

# ---------------------------------------------------------------------------
# Classifier worker (runs model in a background thread)
# ---------------------------------------------------------------------------
_STOP = object()

class ClassifierWorker:
    def __init__(self, callback):
        self._callback = callback
        self._q        = queue.Queue()
        self._t        = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        print("Loading emotion model...", file=sys.stderr)
        classifier = pipeline(
            "text-classification",
            model=MODEL_NAME,
            top_k=1,
            truncation=True,
            max_length=512,
        )
        print("Emotion model ready.", file=sys.stderr)

        while True:
            item = self._q.get()
            if item is _STOP:
                self._q.task_done()
                break
            text = item.strip()
            if text:
                result = classifier(text)
                # pipeline returns [[{label, score}]] with top_k=1
                label = result[0][0]["label"].lower()
                score = result[0][0]["score"]
                self._callback(label, score)
            self._q.task_done()

    def submit(self, text: str):
        self._q.put(text)

    def drain(self):
        self._q.join()

    def stop(self):
        self._q.put(_STOP)
        self._t.join()

# ---------------------------------------------------------------------------
# Stream consumer
# ---------------------------------------------------------------------------
class Emote:
    def __init__(self, callback, word_count_fallback: int = WORD_COUNT_FALLBACK):
        """
        callback: callable(label: str, score: float)
            Called after each segment is classified.
        word_count_fallback: int
            Classify after this many words if no sentence boundary is seen.
        """
        self._callback  = callback
        self._fallback  = word_count_fallback
        self._worker    = ClassifierWorker(callback)
        self._buf       = []   # char buffer for current segment
        self._wordcount = 0
        self._in_word   = False

    def feed(self, chunk: str):
        for ch in chunk:
            self._buf.append(ch)

            # Track word boundaries for fallback
            if ch.isspace():
                if self._in_word:
                    self._wordcount += 1
                    self._in_word = False
                    if self._wordcount >= self._fallback:
                        self._flush()
            else:
                if not self._in_word:
                    self._in_word = True

            # Flush on sentence boundary
            if SENTENCE_END_RE.match(ch):
                if self._in_word:
                    self._wordcount += 1
                    self._in_word = False
                self._flush()

    def _flush(self):
        if not self._buf:
            return
        text = ''.join(self._buf).strip()
        self._buf.clear()
        self._wordcount = 0
        self._in_word   = False
        if text:
            self._worker.submit(text)

    def flush(self):
        """Flush any remaining buffered text."""
        if self._in_word:
            self._wordcount += 1
            self._in_word = False
        self._flush()

    def stop(self):
        """Flush, drain, and shut down the classifier worker."""
        self.flush()
        self._worker.drain()
        self._worker.stop()

    @property
    def current_emotion(self) -> str | None:
        """
        Last emotion label seen. None until first classification completes.
        Updated synchronously from the worker thread — read is eventually
        consistent, not instantaneous.
        """
        return self._last_label

    # Internal — worker updates this via callback wrapper
    _last_label: str | None = None
    _last_score: float | None = None

# ---------------------------------------------------------------------------
# Convenience subclass that also tracks last label as a property
# ---------------------------------------------------------------------------
class TrackingEmote(Emote):
    """
    Wraps Emote and keeps .current_emotion / .current_score updated
    in addition to calling any user-supplied callback.
    """
    def __init__(self, callback=None, word_count_fallback: int = WORD_COUNT_FALLBACK):
        self._last_label = None
        self._last_score = None
        self._user_cb    = callback

        def _inner(label, score):
            self._last_label = label
            self._last_score = score
            if self._user_cb:
                self._user_cb(label, score)

        super().__init__(callback=_inner, word_count_fallback=word_count_fallback)

    @property
    def current_emotion(self) -> str | None:
        return self._last_label

    @property
    def current_score(self) -> float | None:
        return self._last_score

# ---------------------------------------------------------------------------
# CLI — reads stdin, prints emotion labels to stdout
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    def on_emotion(label, score):
        print(f"{label} ({score:.2f})", flush=True)

    emote = TrackingEmote(callback=on_emotion)

    try:
        for line in sys.stdin:
            emote.feed(line)
    except KeyboardInterrupt:
        pass
    finally:
        emote.stop()
