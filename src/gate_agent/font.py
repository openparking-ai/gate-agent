"""The bitmap font the display draws with. In this repository, drawn by hand.

**Why not a font file.** A `.ttf` needs a rasteriser, a rasteriser is a runtime
dependency, and `dependencies = []` is a property of this package rather than a
convenience: this runs beside a lane, on a box in a gate housing. What the
display has to say is a ticket reference and one instruction line, and that is
a few dozen glyphs.

**Why UPPERCASE ONLY, and it is a decision rather than a shortcut.** Two
reasons, and the second is the one that decides it:

  * a driver reads this through a windscreen, at a few metres, often at night,
    and upper case at a given height is the more legible of the two;
  * **this repository has to be able to CHECK every glyph it ships.** Half a
    font is half the drawing, half the review and half the surface for a
    character to be wrong in a way nobody notices until a real driver is
    standing at a real barrier.

So the display's own lines are written in upper case in `lines.py`, and a
character this font lacks is a **STARTUP REFUSAL** naming the line and the
language -- never a blank, and never a substitution. A blank is a driver told
nothing; a substitution is a driver told something else.

**The glyphs are drawn as pictures, not as hex.** `0x7C` is a number nobody
proof-reads; seven rows of `#` and `.` is a thing a person can look at and see.
`tests/test_font.py` renders every one of them back to exactly this shape and
compares, so the picture in the source is the picture on the screen.

**Accents are COMPOSED**, not drawn again: `Á` is `A` with an acute above it in
the two rows this cell keeps for the purpose. Drawing seven more letters by hand
would be seven more chances to draw one wrongly, and the accent is the same mark
every time it appears.
"""

from __future__ import annotations

#: One glyph is five modules wide and seven tall, sitting under a two-row zone
#: that is empty for an unaccented character.
GLYPH_WIDTH = 5
GLYPH_HEIGHT = 7
ACCENT_HEIGHT = 2
CELL_HEIGHT = ACCENT_HEIGHT + GLYPH_HEIGHT

#: One column of space between glyphs, and it is part of the font rather than of
#: the renderer: a font whose spacing lives somewhere else is one that looks
#: different depending on who draws with it.
TRACKING = 1

GLYPHS: dict[str, tuple[str, ...]] = {
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#..##", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#...#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ",": (".....", ".....", ".....", ".....", ".##..", ".##..", ".#..."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    "'": (".#...", ".#...", ".....", ".....", ".....", ".....", "....."),
    "!": ("..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."),
    "?": (".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."),
    "/": ("....#", "...#.", "...#.", "..#..", ".#...", ".#...", "#...."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
}

#: The marks that sit in the two rows above a glyph. Drawn once each, because a
#: mark drawn per letter is a mark that comes out differently on one of them.
ACCENTS: dict[str, tuple[str, ...]] = {
    "acute": ("...#.", "..#.."),
    "tilde": (".##.#", "#.##."),
    "diaeresis": (".#.#.", "....."),
}

#: WHICH letters carry WHICH mark. Composed rather than drawn again: seven more
#: hand-drawn letters is seven more chances to draw one wrongly, and every one
#: of these is the base letter with a mark that is identical everywhere it
#: appears.
#:
#: This is the whole of what Castilian needs above the base set. A language this
#: font cannot write is refused at STARTUP, by name, rather than discovered as a
#: blank on a screen.
ACCENTED: dict[str, tuple[str, str]] = {
    "Á": ("A", "acute"),
    "É": ("E", "acute"),
    "Í": ("I", "acute"),
    "Ó": ("O", "acute"),
    "Ú": ("U", "acute"),
    "Ñ": ("N", "tilde"),
    "Ü": ("U", "diaeresis"),
}

#: Every character this font can draw. DERIVED from the two tables, so a glyph
#: added to either is in here without anybody remembering to add it twice.
DRAWABLE: frozenset[str] = frozenset(GLYPHS) | frozenset(ACCENTED)


class UndrawableCharacter(Exception):
    """A character this font has no glyph for.

    Raised where the string is first seen -- at STARTUP -- so it names the line
    and the language rather than appearing as a hole in a frame at three in the
    morning.
    """


def cell(character: str) -> tuple[str, ...]:
    """One glyph as `CELL_HEIGHT` rows of `GLYPH_WIDTH`, accent rows included."""
    if character in ACCENTED:
        base, mark = ACCENTED[character]
        return ACCENTS[mark] + GLYPHS[base]
    if character not in GLYPHS:
        raise UndrawableCharacter(character)
    return ("." * GLYPH_WIDTH,) * ACCENT_HEIGHT + GLYPHS[character]


def missing(text: str) -> tuple[str, ...]:
    """Every character in `text` this font cannot draw, in the order they appear.

    Returned rather than raised, so a startup refusal can name all of them at
    once: an installer told about one missing character at a time restarts the
    process once per character.
    """
    seen: list[str] = []
    for character in text:
        if character not in DRAWABLE and character not in seen:
            seen.append(character)
    return tuple(seen)


def width_of(text: str) -> int:
    """How wide `text` is in modules, tracking included. Zero for an empty one."""
    if not text:
        return 0
    return len(text) * GLYPH_WIDTH + (len(text) - 1) * TRACKING


def render(text: str) -> list[list[int]]:
    """`text` as rows of 0/1, `CELL_HEIGHT` tall. Refuses what it cannot draw."""
    absent = missing(text)
    if absent:
        raise UndrawableCharacter(
            f"this font has no glyph for {', '.join(repr(one) for one in absent)}"
        )
    rows = [[0] * width_of(text) for _ in range(CELL_HEIGHT)]
    for index, character in enumerate(text):
        left = index * (GLYPH_WIDTH + TRACKING)
        for row, line in enumerate(cell(character)):
            for column, pixel in enumerate(line):
                if pixel == "#":
                    rows[row][left + column] = 1
    return rows


__all__ = [
    "ACCENTED",
    "ACCENTS",
    "ACCENT_HEIGHT",
    "CELL_HEIGHT",
    "DRAWABLE",
    "GLYPHS",
    "GLYPH_HEIGHT",
    "GLYPH_WIDTH",
    "TRACKING",
    "UndrawableCharacter",
    "cell",
    "missing",
    "render",
    "width_of",
]
