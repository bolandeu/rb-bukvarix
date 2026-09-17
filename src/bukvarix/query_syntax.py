from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"([+-]?)(\S+)")


@dataclass
class ParsedQuery:
    """A parsed subset of Wordstat operators: +word, -word — for a single
    phrase (see query_syntax.py history for why (a|b) group support was
    dropped, 2026-08-31: keeping the tool scoped to one phrase at a time).

    Deliberately narrow scope: no quotes, no `!` exact-form operator —
    those need per-word grammar our signature columns can't express.
    """

    required: list[str] = field(default_factory=list)  # plain words + `+word` — must be present
    forced: list[str] = field(default_factory=list)  # `+word` only — subset of required
    excluded: list[str] = field(default_factory=list)  # `-word` — must not be present


def parse_query(query: str) -> ParsedQuery:
    result = ParsedQuery()
    for m in _TOKEN_RE.finditer(query.strip()):
        sign, word = m.groups()
        if not word:
            continue
        w = word.lower()
        if sign == "-":
            result.excluded.append(w)
        else:
            result.required.append(w)
            if sign == "+":
                result.forced.append(w)
    return result
