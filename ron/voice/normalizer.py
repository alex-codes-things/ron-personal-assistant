"""Conservative, accent-tolerant corrections before Ron's existing router."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from ron.voice.models import NormalizationResult
from ron.voice.settings import VoiceSettings


class VoiceNormalizer:
    """Correct likely ASR mistakes without allowing an LLM to rewrite commands."""

    _SPOTIFY = re.compile(
        r"\b(?:spot\s+(?:the|a)\s+fi|spot\s+if\s+i|spot\s+the\s+five|spotter\s*fi)\b",
        re.IGNORECASE,
    )
    _VOLUME_WORD = re.compile(r"\b(?:colume|volum|vollume|volumee)\b", re.IGNORECASE)
    _BRIGHTNESS_WORD = re.compile(
        r"\b(?:bright\s*ness|brideness|brightnesses)\b", re.IGNORECASE
    )
    _CONTROL_VERB = re.compile(
        r"^\s*(?:sit|sat|set)\s+(volume|brightness)\s+to\b", re.IGNORECASE
    )
    _GALWAY_TITLE = re.compile(
        r"\bplay\s+galway\s+(?:girl\s*bot|grohl)\s+on\s+spotify\b", re.IGNORECASE
    )
    _CONTROL_LINKER = re.compile(
        r"\b((?:volume|brightness)\s+)(?:two|too)(?=\s+(?:zero|one|two|three|four|five|"
        r"six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
        r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|"
        r"ninety|hundred|\d))",
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
    _APP_COMMAND = re.compile(
        r"^(open|launch|start)\s+(?:the\s+)?(?:app(?:lication)?\s+)?(.+?)[.!?]*$",
        re.IGNORECASE,
    )
    _KNOWN_APPS = {
        "spotify": "Spotify",
        "notepad": "Notepad",
        "note pad": "Notepad",
        "calculator": "Calculator",
        "file explorer": "File Explorer",
        "visual studio code": "Visual Studio Code",
        "vs code": "VS Code",
        "v s code": "VS Code",
        "brave": "Brave",
        "brave browser": "Brave",
        "youtube": "YouTube",
        "you tube": "YouTube",
    }

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
        self._wake_aliases = aliases
        joined_wake = re.sub(r"\s+", "", settings.wake_phrase)
        self._joined_wake_pattern = re.compile(
            rf"^(?:um\s+|uh\s+)?{re.escape(joined_wake)}\b[\s,.:;!?-]*(.*)$",
            re.IGNORECASE,
        )
        kws_aliases = sorted(settings.wake_kws_aliases, key=len, reverse=True)
        self._kws_wake_patterns = tuple(
            (
                alias,
                re.compile(
                    rf"^(?:um\s+|uh\s+)?{self._phrase_pattern(alias)}\b[\s,.:;!?-]*(.*)$",
                    re.IGNORECASE,
                ),
            )
            for alias in kws_aliases
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
        wake_notes: list[str] = []
        command = raw
        if require_wake:
            if not wake_detected:
                return NormalizationResult(raw, "", None, False)
            command, wake_phrase = self._strip_wake(raw)
            if wake_phrase is None and self.settings.require_wake_in_transcript:
                joined_command, joined_wake = self._strip_joined_wake(raw)
                if joined_wake is not None:
                    command, wake_phrase = joined_command, joined_wake
                    wake_notes.append(
                        "Accepted the joined wake phrase after KWS confirmation"
                    )
            if wake_phrase is None and self.settings.require_wake_in_transcript:
                kws_command, kws_wake = self._strip_kws_only_wake(raw)
                if kws_wake is not None:
                    command, wake_phrase = kws_command, kws_wake
                    wake_notes.append(
                        "Accepted a calibrated wake pronunciation after KWS confirmation"
                    )
            if wake_phrase is None and self.settings.require_wake_in_transcript:
                fuzzy_command, fuzzy_wake, score = self._strip_fuzzy_wake(raw)
                if fuzzy_wake is None:
                    return NormalizationResult(raw, "", None, False)
                command, wake_phrase = fuzzy_command, fuzzy_wake
                wake_notes.append(
                    f"Accepted an accent-tolerant wake match ({score:.0%}) after KWS confirmation"
                )
            if wake_phrase is None:
                wake_phrase = self.settings.wake_phrase
            if not command:
                return NormalizationResult(
                    raw,
                    "",
                    wake_phrase,
                    True,
                    waiting_for_command=True,
                    correction_notes=tuple(wake_notes),
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
        all_notes = tuple((*wake_notes, *notes))
        if self._AMBIGUOUS_VOLUME_TWO.search(corrected):
            return NormalizationResult(
                raw,
                corrected,
                wake_phrase,
                True,
                clarification="Did you mean 2%, or were you about to say another number?",
                correction_notes=all_notes,
            )
        return NormalizationResult(
            raw,
            corrected,
            wake_phrase,
            bool(corrected),
            correction_notes=all_notes,
        )

    def _strip_wake(self, text: str) -> tuple[str, str | None]:
        for alias, pattern in zip(self._wake_aliases, self._wake_patterns, strict=True):
            match = pattern.match(text)
            if match is not None:
                return self._clean(match.group(1)), alias
        return text, None


    def _strip_joined_wake(self, text: str) -> tuple[str, str | None]:
        """Handle ASR joining the configured wake words, e.g. Hey Ron -> Heyron."""
        match = self._joined_wake_pattern.match(text)
        if match is None:
            return text, None
        return self._clean(match.group(1)), self.settings.wake_phrase

    def _strip_kws_only_wake(self, text: str) -> tuple[str, str | None]:
        """Use personal wake pronunciations only after acoustic KWS confirmation."""
        for alias, pattern in self._kws_wake_patterns:
            match = pattern.match(text)
            if match is not None:
                return self._clean(match.group(1)), alias
        return text, None

    def _strip_fuzzy_wake(self, text: str) -> tuple[str, str | None, float]:
        """Allow a near wake phrase only after the acoustic KWS already fired."""
        words = text.split()
        best: tuple[float, int, str] | None = None
        for alias in self._wake_aliases:
            count = len(alias.split())
            if len(words) < count:
                continue
            heard = self._comparison_text(" ".join(words[:count]))
            expected = self._comparison_text(alias)
            score = SequenceMatcher(None, heard, expected).ratio()
            if best is None or score > best[0]:
                best = (score, count, alias)
        if best is None or best[0] < self.settings.wake_fuzzy_threshold:
            return text, None, 0.0
        score, count, alias = best
        return self._clean(" ".join(words[count:])), alias, score

    def _correct(self, text: str) -> tuple[str, tuple[str, ...]]:
        corrected = text
        notes: list[str] = []

        corrected, count = self._SPOTIFY.subn("Spotify", corrected)
        if count:
            notes.append("Matched a split pronunciation to Spotify")

        corrected, count = self._VOLUME_WORD.subn("volume", corrected)
        if count:
            notes.append("Corrected the volume control word")

        corrected, count = self._BRIGHTNESS_WORD.subn("brightness", corrected)
        if count:
            notes.append("Corrected the brightness control word")

        corrected, count = self._CONTROL_VERB.subn(r"set \1 to", corrected)
        if count:
            notes.append("Corrected a calibrated control verb")

        corrected, count = self._GALWAY_TITLE.subn(
            "play Galway Girl on Spotify", corrected
        )
        if count:
            notes.append("Corrected the calibrated Galway Girl title")

        corrected, count = self._CONTROL_LINKER.subn(r"\1to", corrected)
        if count:
            notes.append("Used bounded control grammar to resolve to/two/too")

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
            r"\bvisual\s+studio\s+coat\b": "Visual Studio Code",
            r"\bvs\s+coat\b": "VS Code",
            r"\byou\s+tube\b": "YouTube",
        }
        for pattern, replacement in replacements.items():
            corrected, count = re.subn(pattern, replacement, corrected, flags=re.IGNORECASE)
            if count:
                notes.append(f"Matched the known application {replacement}")

        corrected, app_note = self._fuzzy_application(corrected)
        if app_note:
            notes.append(app_note)

        return self._clean(corrected), tuple(notes)

    def _fuzzy_application(self, text: str) -> tuple[str, str | None]:
        """Resolve only a complete open/launch/start command to a small app allow-list."""
        match = self._APP_COMMAND.match(text)
        if match is None:
            return text, None
        verb, candidate = match.group(1), self._clean(match.group(2))
        comparison = self._comparison_text(candidate)
        if not comparison or len(comparison) < 3:
            return text, None

        best_score = 0.0
        best_name: str | None = None
        for alias, canonical in self._KNOWN_APPS.items():
            score = SequenceMatcher(None, comparison, alias).ratio()
            if score > best_score:
                best_score, best_name = score, canonical
        # Deliberately high: fuzzy matching is a final correction layer, not an
        # excuse to turn an unrelated sentence into an executable app command.
        if best_name is None or best_score < 0.78:
            return text, None
        canonical_text = f"{verb.lower()} {best_name}"
        if self._comparison_text(canonical_text) == self._comparison_text(text):
            return text, None
        return canonical_text, f"Matched a likely pronunciation to {best_name} ({best_score:.0%})"

    @staticmethod
    def _clean(value: str) -> str:
        value = value.replace("’", "'").replace("`", "'").replace("\0", "")
        value = " ".join(value.strip().split())
        return value[:6_000]

    @staticmethod
    def _comparison_text(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))

    @staticmethod
    def _phrase_pattern(value: str) -> str:
        words = [re.escape(word) for word in value.split()]
        return r"[\s,.-]+".join(words)
