"""PII redaction.

Inbound messages carry phone numbers and names. None of it reaches a model, the
store, or the audit log: it is replaced with stable pseudonyms first, and the
mapping lives in memory only for the life of the process. Nothing here is ever
written to disk, and there is deliberately no persistence method to call.

This module is the pure, testable core. The Strands hook that calls it lives in
agent/hooks/pii_redaction.py; the optional model-assisted name pass is layered
on top of these regexes rather than replacing them, because a regex that misses
is a bug and a model that misses is a Tuesday.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

# Pakistani mobile and landline shapes, plus generic international. Ordered
# longest-first so a +92 number is not partly eaten by the local-format rule.
_PHONE_PATTERNS = [
    re.compile(r"\+92[\s-]?3\d{2}[\s-]?\d{7}"),
    re.compile(r"\+\d{1,3}[\s-]?\d{2,4}[\s-]?\d{6,8}"),
    re.compile(r"\b03\d{2}[\s-]?\d{7}\b"),
    re.compile(r"\b0\d{2,4}[\s-]?\d{6,8}\b"),
]

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")

# Pakistani national ID: 5 digits - 7 digits - 1 digit.
_CNIC = re.compile(r"\b\d{5}[\s-]?\d{7}[\s-]?\d\b")

# Signature-style attributions the extractor would otherwise read as content:
# a trailing "- Asif", "-Fatima Bibi", "— Asif Khan". En and em dashes are
# intentional: people sign off with all three.
#
# The dash does NOT have to start a line. People sign off inline, mid-sentence:
# "Koi boat bhejo jaldi. - Asif" slipped past an earlier version of this pattern
# that anchored on line start, and that name reached the model, the store and
# the audit log before an end-to-end run caught it.
_SIGNATURE = re.compile(
    r"[-–—]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s*$",  # noqa: RUF001
    flags=re.MULTILINE,
)

# Words that follow a dash often enough to need protecting from the signature
# rule. Redacting "Please" as a person is its own kind of wrong: it corrupts the
# message the coordinator has to read.
_NOT_A_SIGNATURE = {
    "please", "help", "urgent", "thanks", "thank", "send", "need", "we", "our",
    "water", "yes", "no", "hurry", "quick", "quickly", "sos", "emergency",
    "location", "update", "still", "come", "anyone", "someone", "god", "allah",
}

# Honorific-led names, the most reliable non-model name signal we have.
_HONORIFIC = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Malik|Haji|Syed|Sheikh)\.?\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)

# Common name-final words in the seed region. Used only to confirm a
# capitalised bigram is a person, never to match on its own.
_NAME_TAILS = {
    "bibi", "khan", "begum", "khatoon", "shah", "ullah", "ahmed", "ahmad",
    "hussain", "ali", "gul", "din", "khel",
}
_CAPITALISED_PAIR = re.compile(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b")


@dataclass
class Redactor:
    """Assigns stable pseudonyms to the entities it removes.

    Stability matters: the same phone number appearing in two messages must map
    to the same CALLER_n, or the dedupe node loses a genuine signal that two
    reports come from one household.
    """

    _map: dict[str, str] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def _token(self, kind: str, value: str) -> str:
        key = f"{kind}:{value.casefold().strip()}"
        with self._lock:
            existing = self._map.get(key)
            if existing:
                return existing
            n = self._counts.get(kind, 0) + 1
            self._counts[kind] = n
            token = f"{kind}_{n}"
            self._map[key] = token
            return token

    def redact(self, text: str) -> str:
        """Return the message with contact details and names replaced."""
        if not text:
            return text
        out = text

        for pattern in _PHONE_PATTERNS:
            out = pattern.sub(lambda m: self._token("CALLER", m.group(0)), out)
        out = _CNIC.sub(lambda m: self._token("ID", m.group(0)), out)
        out = _EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), out)

        def _signature(match: re.Match[str]) -> str:
            name = match.group(1)
            if name.split()[0].casefold() in _NOT_A_SIGNATURE:
                return match.group(0)
            return f"- {self._token('PERSON', name)}"

        out = _SIGNATURE.sub(_signature, out)
        out = _HONORIFIC.sub(lambda m: self._token("PERSON", m.group(1)), out)

        def _pair(match: re.Match[str]) -> str:
            second = match.group(2)
            if second.casefold() in _NAME_TAILS:
                return self._token("PERSON", match.group(0))
            return match.group(0)

        out = _CAPITALISED_PAIR.sub(_pair, out)
        return out

    def redact_names(self, text: str, names: list[str]) -> str:
        """Second pass for names a model identified that the regexes missed.

        Longest first, so "Fatima Bibi" is replaced before "Fatima" can split it.
        """
        out = text
        for name in sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True):
            pattern = re.compile(rf"\b{re.escape(name)}\b", flags=re.IGNORECASE)
            out = pattern.sub(lambda m, n=name: self._token("PERSON", n), out)  # type: ignore[misc]
        return out

    @property
    def pseudonym_count(self) -> int:
        with self._lock:
            return len(self._map)

    def clear(self) -> None:
        with self._lock:
            self._map.clear()
            self._counts.clear()


# One redactor per process. Never serialised, never written down.
_redactor = Redactor()


def get_redactor() -> Redactor:
    return _redactor
