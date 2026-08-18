"""Conservative, intent-aware corrections before Ron's existing router."""

from __future__ import annotations

import re

from ron.voice.models import NormalizationResult
from ron.voice.settings import VoiceSettings


class VoiceNormalizer:
    """Correct known ASR mistakes without asking an LLM to rewrite commands."""

    _SPOTIFY = re.compile(
        r"\b(?:spot\s+(?:the|a)\s+fi|spot\s+if\s+i|spot\s+the\s+five|spotter\s*fi)\b",
        re.IGNORECASE,
    )
    _VOLUME_WORD = re.compile(r"\b(?:colume|volum|vollume)\b", re.IGNORECASE)
    _VOLUME_LINKER = re.compile(
        r"\b(volume\s+)(?:two|too)(?=\s+(?:zero|one|two|three|four|five|six|seven|"
        r"eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
        r"hundred|\d))",
        re.IGNORECASE,
    )
    _VOLUME_TWENTY = re.compile(
        r"\bvolume\s+to\s+(?:two|2)\s+(?:twenty|20)\b", re.IGNORECASE
    )
    _APP_FILLER = re.compile(
        r"\b(open|launch|start)\s+(?:the\s+)?(?:app|application|out)\s+(?=\w)",
        re.IGNORECASE,
    )
    _BLANK_DOCUMENT = re.compile(
        r"\bopen(?:\s+up)?\s+(?:a\s+)?(?:latin|blanket)\s+text\s+"
        r"(?:human|document|editor)\b",
        re.IGNORECASE,
    )
    _AMBIGUOUS_VOLUME_TWO = re.compile(
        r"\b(?:set|change|put|turn|adjust)\b.*\bvolume\s+(?:two|too)\s*[.!?]*$",
        re.IGNORECASE,
    )

    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        aliases = sorted(settings.wake_aliases, key=len, reverse=True)
        self._wake_patterns = tuple(
            re.compile(
                rf"^(?:um\s+|uh\s+)?{self._phrase_pattern(alias)}\b[\s,.:;!?-]*(.*)$",
                re.IGNORECASE,
            )
            for alias in aliases
        )

    def normalize(
        self,
        raw_text: str,
        *,
        require_wake: bool,
        wake_detected: bool,
    ) -> NormalizationResult:
        raw = self._clean(raw_text)
        if not raw:
            return NormalizationResult(raw_text, "", None, False)

        wake_phrase: str | None = None
        command = raw
        if require_wake:
            if not wake_detected:
                return NormalizationResult(raw, "", None, False)
            command, wake_phrase = self._strip_wake(raw)
            if wake_phrase is None and self.settings.require_wake_in_transcript:
                return NormalizationResult(raw, "", None, False)
            if wake_phrase is None:
                wake_phrase = self.settings.wake_phrase
            if not command:
                return NormalizationResult(
                    raw,
                    "",
                    wake_phrase,
                    True,
                    waiting_for_command=True,
                )
        else:
            optional_command, optional_wake = self._strip_wake(raw)
            if optional_wake is not None:
                command = optional_command
                wake_phrase = optional_wake
                if not command:
                    return NormalizationResult(
                        raw,
                        "",
                        wake_phrase,
                        True,
                        waiting_for_command=True,
                    )

        corrected, notes = self._correct(command)
        if self._AMBIGUOUS_VOLUME_TWO.search(corrected):
            return NormalizationResult(
                raw,
                corrected,
                wake_phrase,
                True,
                clarification="Did you mean 2%, or were you about to say another number?",
                correction_notes=notes,
            )
        return NormalizationResult(
            raw,
            corrected,
            wake_phrase,
            bool(corrected),
            correction_notes=notes,
        )

    def _strip_wake(self, text: str) -> tuple[str, str | None]:
        for alias, pattern in zip(self.settings.wake_aliases, self._wake_patterns, strict=True):
            match = pattern.match(text)
            if match is not None:
                return self._clean(match.group(1)), alias
        return text, None

    def _correct(self, text: str) -> tuple[str, tuple[str, ...]]:
        corrected = text
        notes: list[str] = []

        corrected, count = self._SPOTIFY.subn("Spotify", corrected)
        if count:
            notes.append("Matched a split pronunciation to Spotify")

        corrected, count = self._VOLUME_WORD.subn("volume", corrected)
        if count:
            notes.append("Corrected the volume control word")

        corrected, count = self._VOLUME_LINKER.subn(r"\1to", corrected)
        if count:
            notes.append("Used command grammar to resolve to/two/too")

        corrected, count = self._VOLUME_TWENTY.subn("volume to twenty", corrected)
        if count:
            notes.append("Resolved a duplicated two/twenty volume phrase")

        corrected, count = self._APP_FILLER.subn(r"\1 ", corrected)
        if count:
            notes.append("Removed a misheard application filler")

        corrected, count = self._BLANK_DOCUMENT.subn(
            "open a blank text document", corrected
        )
        if count:
            notes.append("Matched a known blank-text-document phrase")

        replacements = {
            r"\bnote\s+pad\b": "Notepad",
            r"\bfile\s+explore\b": "File Explorer",
            r"\bcalculate\s+her\b": "Calculator",
        }
        for pattern, replacement in replacements.items():
            corrected, count = re.subn(pattern, replacement, corrected, flags=re.IGNORECASE)
            if count:
                notes.append(f"Matched the known application {replacement}")

        return self._clean(corrected), tuple(notes)

    @staticmethod
    def _clean(value: str) -> str:
        value = value.replace("’", "'").replace("`", "'").replace("\0", "")
        value = " ".join(value.strip().split())
        return value[:6_000]

    @staticmethod
    def _phrase_pattern(value: str) -> str:
        words = [re.escape(word) for word in value.split()]
        return r"[\s,.-]+".join(words)
