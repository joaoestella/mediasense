"""Turn "what to remove" (phrases, fillers, silences, time ranges) into an edit plan.

An :class:`EditPlan` is just a sorted list of non-overlapping cuts plus the
complementary list of ranges to keep. Rendering, exporting EDL/FCPXML and
re-timing the transcript all work from the same plan, so a dry run shows
exactly what the render will do.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .models import Transcript, Word, fmt_time, join_words, regroup

Range = tuple[float, float]

# --------------------------------------------------------------- text utils
_CJK_LANGS = {"ja", "zh", "ko", "th", "yue", "lo", "my", "km"}


def normalize(text: str) -> str:
    """casefold, strip accents and punctuation: "Então," -> "entao"."""
    text = unicodedata.normalize("NFKD", text.casefold())
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Mn":  # combining accent
            continue
        if cat[0] in ("P", "S"):
            out.append(" ")
            continue
        out.append(ch)
    return " ".join("".join(out).split())


@dataclass
class Match:
    start: float
    end: float
    first_word: int
    last_word: int
    text: str

    def to_dict(self) -> dict:
        return {"start": round(self.start, 3), "end": round(self.end, 3),
                "start_tc": fmt_time(self.start), "end_tc": fmt_time(self.end),
                "first_word": self.first_word, "last_word": self.last_word, "text": self.text}


def find_phrase(transcript: Transcript, query: str, words: list[Word] | None = None) -> list[Match]:
    """Find every occurrence of ``query`` (accent/case/punctuation-insensitive)."""
    words = words if words is not None else transcript.words()
    q = normalize(query)
    if not q or not words:
        return []
    norm = [normalize(w.text) for w in words]

    hits = _search(norm, q, sep=" ", boundaries=True)
    if not hits:
        # Languages without spaces (ja/zh/th…) match inside tokens; elsewhere we
        # still try a space-less match on word boundaries, which catches words
        # the model split ("Whats App" vs "WhatsApp").
        cjk = (transcript.language or "")[:2] in _CJK_LANGS
        hits = _search(norm, q.replace(" ", ""), sep="", boundaries=not cjk)
    return [
        Match(words[i].start, words[j].end, i, j, join_words([w.text for w in words[i:j + 1]]))
        for i, j in hits
    ]


def _search(norm: list[str], q: str, sep: str, boundaries: bool) -> list[tuple[int, int]]:
    hay = ""
    owner: list[int] = []  # char index -> word index
    starts: set[int] = set()
    ends: set[int] = set()
    for i, n in enumerate(norm):
        if not n:
            continue
        if hay and sep:
            hay += sep
            owner.append(-1)
        starts.add(len(hay))
        hay += n
        owner.extend([i] * len(n))
        ends.add(len(hay))
    hits = []
    pos = hay.find(q)
    while pos != -1:
        end = pos + len(q)
        if not boundaries or (pos in starts and end in ends):
            first = owner[pos]
            last = owner[end - 1]
            if first >= 0 and last >= 0:
                hits.append((first, last))
        pos = hay.find(q, pos + 1)
    return hits


# --------------------------------------------------------------- fillers
# Only unambiguous hesitation sounds by default. Words that are fillers in
# some contexts but meaningful in others ("like", "tipo", "né", "so", "este")
# are opt-in through ``extra``.
_FILLER_PATTERN = re.compile(
    r"^(h*m{2,}|hm+|u+m+|u+h+m*|e+h+m*|e+r+m+|a+h+m+|a+h+n+|h+u+m+|e{2,}|a{2,}|o{2,}|ah|euh|heu)$"
)
_FILLER_WORDS = {
    "pt": {"hum", "hã", "ahn", "aham", "hmm", "éé", "ééé", "ã", "ãã"},
    "en": {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm", "mm", "mhm"},
    "es": {"eh", "ehh", "em", "emm", "mmm", "ehm"},
    "fr": {"euh", "heu", "hum", "bah"},
    "de": {"äh", "ähm", "öh", "öhm", "hm"},
    "it": {"ehm", "eh", "mmm", "uhm"},
    "ja": {"えー", "えーと", "えっと", "うーん", "あー", "ええと", "えーっと"},
    "zh": {"嗯", "呃", "额"},
    "ko": {"음", "어"},
    "ru": {"э", "ээ", "эм", "ммм"},
}
_FILLER_NORM = {lang: {normalize(p) for p in pool} for lang, pool in _FILLER_WORDS.items()}

# Sounds that are hesitations in one language but real words in another:
# "um" is "a/one" in Portuguese and "around" in German, "em" is "in" in Portuguese.
_REAL_WORDS = {
    "pt": {"um", "em", "ah", "oi", "ai", "ui", "eh"},
    "de": {"um", "oh", "ah"},
    "es": {"un", "eh", "ah"},
    "it": {"un", "ah"},
    "fr": {"un", "ah", "oh"},
    "ca": {"un", "em"},
    "nl": {"hum", "ah"},
}

# Whisper marks hesitations with an ellipsis ("É... voltei"). A lone vowel sound
# followed by one is a filler; the same vowel without it is a word ("é" = is).
_ELLIPSIS_FILLERS = {"é", "e", "ã", "a", "ah", "eh", "éé", "ée", "hm", "uh", "ehm", "euh", "äh"}


def is_filler(word: str, language: str | None = None, extra: Iterable[str] = ()) -> bool:
    stripped = word.strip().casefold()
    raw = stripped.strip(".,!?…;:\"'¿¡()[]、。")
    n = normalize(word)
    if not n and not raw:
        return False
    if n and n in {normalize(e) for e in extra}:
        return True
    lang = (language or "")[:2]
    if raw in _REAL_WORDS.get(lang, ()) or n in _REAL_WORDS.get(lang, ()):
        return False
    if stripped.endswith(("...", "…")) and raw in _ELLIPSIS_FILLERS and lang not in ("es", "it"):
        # (in es/it "e…" / "a…" are usually a trailing "and…"/"to…", not a hesitation)
        return True
    langs = [lang] if lang in _FILLER_WORDS else list(_FILLER_WORDS)
    for code in langs:
        pool = _FILLER_WORDS[code]
        if raw in pool:
            return True
        # accent-insensitive match, but a 1-letter result ("e", "a") is a real
        # word in many languages, so "é"/"ã" only count when written that way
        if n in _FILLER_NORM[code] and len(n) > 1:
            return True
    if lang in _CJK_LANGS:
        return False
    return bool(_FILLER_PATTERN.match(n))


# --------------------------------------------------------------- plan
_WORD_SLACK = 0.2  # how far a removed word's own timestamps may be off


def _join_spans(a: tuple[float, float] | None, b: tuple[float, float] | None) -> tuple[float, float] | None:
    if a is None or b is None:
        return a or b
    return min(a[0], b[0]), max(a[1], b[1])


@dataclass
class Cut:
    start: float
    end: float
    reason: str
    text: str = ""
    adjusted: bool = False  # moved to the real pause in the audio (see boundaries.py)
    # (start of the first removed word, end of the last one) for cuts made from the transcript:
    # snapping may not move the cut so far that it no longer covers those words
    words: tuple[float, float] | None = None

    def to_dict(self) -> dict:
        d = {"start": round(self.start, 3), "end": round(self.end, 3),
             "start_tc": fmt_time(self.start), "end_tc": fmt_time(self.end),
             "length": round(self.end - self.start, 3), "reason": self.reason, "text": self.text}
        if self.adjusted:
            d["adjusted"] = True
        return d


@dataclass
class EditPlan:
    duration: float
    cuts: list[Cut] = field(default_factory=list)

    @property
    def keep(self) -> list[Range]:
        out, cur = [], 0.0
        for c in self.cuts:
            if c.start > cur:
                out.append((cur, c.start))
            cur = max(cur, c.end)
        if cur < self.duration:
            out.append((cur, self.duration))
        return out

    @property
    def removed(self) -> float:
        return sum(c.end - c.start for c in self.cuts)

    @property
    def new_duration(self) -> float:
        return self.duration - self.removed

    def to_dict(self) -> dict:
        return {
            "duration": round(self.duration, 3),
            "new_duration": round(self.new_duration, 3),
            "removed": round(self.removed, 3),
            "cuts": [c.to_dict() for c in self.cuts],
            "keep": [[round(a, 3), round(b, 3)] for a, b in self.keep],
        }

    def snapped(self, env, reach: float | None = None) -> tuple[EditPlan, int]:
        """Same plan with every cut point moved into a real pause, so no word is clipped.

        ``env`` is a :class:`mediasense.boundaries.Envelope` of the same media.
        Returns (new plan, number of cuts that moved).
        """
        from .boundaries import REACH, snap_cut

        moved, out = 0, []
        for c in self.cuts:
            limits = None
            if c.words:  # leave at least most of the removed words inside the cut
                limits = (c.words[0] + _WORD_SLACK, c.words[1] - _WORD_SLACK)
            got = snap_cut(c.start, c.end, env, REACH if reach is None else reach, limits)
            if got is None:  # no pause anywhere in the span
                if set(c.reason.split("+")) == {"silence"}:
                    moved += 1  # a "silence" with sound all through it (music, noise): leave it
                    continue
                out.append(Cut(c.start, c.end, c.reason, c.text, c.adjusted, c.words))
                continue
            s, e = got
            if c.start <= 0.0:
                s = 0.0
            if c.end >= self.duration:
                e = self.duration
            changed = abs(s - c.start) > 0.005 or abs(e - c.end) > 0.005
            moved += changed
            out.append(Cut(s, e, c.reason, c.text, c.adjusted or changed, c.words))
        out.sort(key=lambda c: c.start)
        merged: list[Cut] = []
        for c in out:  # snapping can make neighbouring cuts touch
            if merged and c.start <= merged[-1].end:
                last = merged[-1]
                last.end = max(last.end, c.end)
                last.adjusted = last.adjusted or c.adjusted
                last.words = _join_spans(last.words, c.words)
                if c.reason not in last.reason.split("+"):
                    last.reason += "+" + c.reason
                if c.text and c.text not in last.text:
                    last.text = (last.text + " " + c.text).strip()
            else:
                merged.append(c)
        return EditPlan(duration=self.duration, cuts=merged), moved

    def summary(self, limit: int = 50) -> str:
        lines = [f"{len(self.cuts)} cut(s), removing {self.removed:.2f}s "
                 f"({fmt_time(self.duration)} -> {fmt_time(self.new_duration)})"]
        for c in self.cuts[:limit]:
            txt = f'  "{c.text}"' if c.text else ""
            mark = "  (moved to the pause)" if c.adjusted else ""
            lines.append(f"  - {fmt_time(c.start)} → {fmt_time(c.end)}  [{c.reason}]{txt}{mark}")
        if len(self.cuts) > limit:
            lines.append(f"  … and {len(self.cuts) - limit} more")
        return "\n".join(lines)


class PlanBuilder:
    """Collects removals, then merges them into a clean :class:`EditPlan`."""

    def __init__(self, duration: float, transcript: Transcript | None = None,
                 max_pad: float = 0.12, min_keep: float = 0.08):
        self.duration = duration
        self.transcript = transcript
        self.words = transcript.words() if transcript else []
        self.max_pad = max_pad
        self.min_keep = min_keep
        self._cuts: list[Cut] = []

    # -- helpers --------------------------------------------------------
    def _need_transcript(self) -> list[Word]:
        if not self.transcript:
            raise ValueError("this operation needs a transcript (run `mediasense transcribe` first)")
        return self.words

    def _padded(self, i: int, j: int) -> Range:
        """Range covering words i..j, extended into the neighbouring pauses."""
        w = self.words
        start, end = w[i].start, w[j].end
        prev_end = w[i - 1].end if i > 0 else 0.0
        next_start = w[j + 1].start if j + 1 < len(w) else self.duration
        start -= min(self.max_pad, max(0.0, (start - prev_end) / 2))
        end += min(self.max_pad, max(0.0, (next_start - end) / 2))
        return max(0.0, start), min(self.duration, end)

    def _add(self, start: float, end: float, reason: str, text: str = "",
             words: tuple[int, int] | None = None) -> None:
        start, end = max(0.0, start), min(self.duration, end)
        span = (self.words[words[0]].start, self.words[words[1]].end) if words else None
        if end - start > 0.005:
            self._cuts.append(Cut(start, end, reason, text, words=span))

    # -- operations -----------------------------------------------------
    def remove_range(self, start: float, end: float, reason: str = "range") -> PlanBuilder:
        if end < start:
            start, end = end, start
        text = ""
        if self.transcript:
            text = join_words([w.text for w in self.transcript.words_between(start, end)])
        self._add(start, end, reason, text)
        return self

    def keep_only(self, start: float, end: float) -> PlanBuilder:
        self._add(0.0, start, "outside kept range")
        self._add(end, self.duration, "outside kept range")
        return self

    def remove_phrase(self, phrase: str, occurrence: int | None = None) -> list[Match]:
        """Remove every occurrence (or only the n-th, 1-based) of ``phrase``."""
        self._need_transcript()
        matches = find_phrase(self.transcript, phrase, self.words)
        chosen = matches if occurrence is None else matches[occurrence - 1:occurrence]
        for m in chosen:
            s, e = self._padded(m.first_word, m.last_word)
            self._add(s, e, "phrase", m.text, (m.first_word, m.last_word))
        return chosen

    def remove_between(self, start_phrase: str, end_phrase: str, inclusive: bool = True,
                       occurrence: int = 1) -> Match | None:
        """Remove from ``start_phrase`` up to the next ``end_phrase`` after it."""
        self._need_transcript()
        starts = find_phrase(self.transcript, start_phrase, self.words)
        if len(starts) < occurrence:
            return None
        a = starts[occurrence - 1]
        ends = [m for m in find_phrase(self.transcript, end_phrase, self.words)
                if m.first_word > a.last_word]
        if not ends:
            return None
        b = ends[0]
        if inclusive:
            i, j = a.first_word, b.last_word
        else:
            i, j = a.last_word + 1, b.first_word - 1
            if j < i:
                return None
        s, e = self._padded(i, j)
        text = join_words([w.text for w in self.words[i:j + 1]])
        self._add(s, e, "between", text, (i, j))
        return Match(s, e, i, j, text)

    def remove_fillers(self, extra: Iterable[str] = (), language: str | None = None) -> int:
        words = self._need_transcript()
        lang = language or self.transcript.language
        extra = list(extra)
        multi = [e for e in extra if len(normalize(e).split()) > 1]
        single = [e for e in extra if e not in multi]
        n = 0
        for i, w in enumerate(words):
            if is_filler(w.text, lang, single):
                s, e = self._padded(i, i)
                self._add(s, e, "filler", w.text.strip(), (i, i))
                n += 1
        for phrase in multi:
            for m in find_phrase(self.transcript, phrase, words):
                s, e = self._padded(m.first_word, m.last_word)
                self._add(s, e, "filler", m.text, (m.first_word, m.last_word))
                n += 1
        return n

    def remove_silences(self, silences: Sequence[Range], keep: float = 0.2) -> int:
        """Shorten each silence so at most ``keep`` seconds of it remain
        (half before, half after)."""
        n = 0
        half = keep / 2
        for s, e in silences:
            if e - s > keep + 0.02:
                self._add(s + half, e - half, "silence")
                n += 1
        return n

    def transcript_gaps(self, min_gap: float = 0.6) -> list[Range]:
        """Pauses between words – useful when background music fools silencedetect."""
        words = self._need_transcript()
        gaps = []
        if words and words[0].start >= min_gap:
            gaps.append((0.0, words[0].start))
        for a, b in zip(words, words[1:]):
            if b.start - a.end >= min_gap:
                gaps.append((a.end, b.start))
        if words and self.duration - words[-1].end >= min_gap:
            gaps.append((words[-1].end, self.duration))
        return gaps

    # -- result ---------------------------------------------------------
    def build(self) -> EditPlan:
        cuts = sorted(self._cuts, key=lambda c: (c.start, c.end))
        merged: list[Cut] = []
        for c in cuts:
            if merged and c.start <= merged[-1].end + self.min_keep:
                last = merged[-1]
                last.end = max(last.end, c.end)
                last.words = _join_spans(last.words, c.words)
                if c.reason not in last.reason.split("+"):
                    last.reason += "+" + c.reason
                if c.text and c.text not in last.text:
                    last.text = (last.text + " " + c.text).strip()
            else:
                merged.append(Cut(c.start, c.end, c.reason, c.text, words=c.words))
        # avoid slivers at the very start/end
        if merged and merged[0].start < self.min_keep:
            merged[0].start = 0.0
        if merged and self.duration - merged[-1].end < self.min_keep:
            merged[-1].end = self.duration
        return EditPlan(duration=self.duration, cuts=merged)


# --------------------------------------------------------------- re-timing
def map_time(t: float, keep: Sequence[Range]) -> float | None:
    """Source time -> time in the edited output (None if it was cut)."""
    offset = 0.0
    for a, b in keep:
        if a <= t <= b:
            return offset + (t - a)
        offset += b - a
    return None


def remap_transcript(transcript: Transcript, plan: EditPlan) -> Transcript:
    """Transcript of the *edited* media: cut words removed, times shifted."""
    keep = plan.keep
    out: list[Word] = []
    for w in transcript.iter_words():
        best = None
        for a, b in keep:
            ov = min(b, w.end) - max(a, w.start)
            if ov > 0 and (best is None or ov > best[2]):
                best = (a, b, ov)
        if best is None:
            continue
        a, b, ov = best
        if ov < 0.5 * max(w.end - w.start, 1e-3):
            continue
        s = map_time(max(a, w.start), keep)
        e = map_time(min(b, w.end), keep)
        if s is None or e is None:
            continue
        out.append(Word(w.text, s, e, w.confidence, w.speaker))
    return Transcript(
        segments=regroup(out),
        language=transcript.language,
        duration=plan.new_duration,
        source=transcript.source,
        backend=transcript.backend,
        model=transcript.model,
    )
