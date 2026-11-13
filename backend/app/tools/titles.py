"""Quote marks in the titles the agent writes (#522).

Conductor passes another app's title in curly quotes ("add a task to sort
“Honey” this weekend"), and the model copies whatever marks it read, or swaps
them for single ones. The stored title then differs by its quote marks from
what a later reply or another app quotes, and nothing matches. The quotes
around an outside title are kept, as the user decided, but in one form: every
wrapping quote becomes a straight double quote.

- Curly double quotes (“ ” „ ‟) are never apostrophes, so each one becomes ``"``.
- Single quotes (' ‘ ’) become ``"`` only as a pair that wraps two or more
  characters: the opening one starts a word, the closing one ends one.
  An apostrophe inside or at the end of a word (``Don't``, ``kids'``,
  ``Caribou’s``) or before a number (``'90s``) is not a quote and stays.

Applied where the agent's tools write a task title (``registry.create_task``,
``registry.update_task``), never in the service layer: a title the user types
in the UI is stored as typed.
"""

from __future__ import annotations

import re

_CURLY_DOUBLE = re.compile("[“”„‟]")
# An opening single quote that starts a word, two or more characters, and a
# closing single quote that ends one.
_SINGLE_PAIR = re.compile(
    r"(?<![\w'‘’])['‘](?=[^\s'‘’])"
    r"(?P<inner>[^\n]{2,}?)"
    r"(?<=\S)['’](?![\w'‘’])"
)


def straight_quotes(title: str) -> str:
    """``title`` with its wrapping quotes made straight double quotes."""
    title = _CURLY_DOUBLE.sub('"', title)
    return _SINGLE_PAIR.sub(lambda match: f'"{match["inner"]}"', title)
