"""The display: a framebuffer, a QR code, a reference, and one instruction.

**It is a FRAMEBUFFER and not a toolkit.** `/dev/fb0` is a file: writing
`width x height x bytes-per-pixel` to it puts a picture on a screen, and that
is the whole of what this needs. A windowing system on a box in a gate housing
is a second thing to start, a second thing to crash and a second thing to
update, and none of it draws anything this cannot.

**The geometry is READ, never configured.** `/sys/class/graphics/<fb>/virtual_size`
and `bits_per_pixel` are what the driver says the screen is. A site that typed
its own resolution would be a site whose display is silently wrong on the day
somebody changes a cable, and a frame written at the wrong stride is not a
smaller picture -- it is diagonal noise. An unreadable geometry is a STARTUP
REFUSAL, naming the file.

**Monochrome, and that is what makes the pixel format not matter.** White is
every bit set and black is every bit clear at 16, 24 and 32 bits per pixel, so
this writes a picture without knowing whether the driver wants BGRA, RGBA or
RGB565. It is stated rather than left as a coincidence: the moment anything here
draws a colour, the channel order becomes a question and this paragraph stops
being true.

**What the driver sees.** The QR fills the short side less a margin, the
`ticket_ref` is printed under it -- large, because it is what somebody reads out
over an intercom -- and one instruction line per declared driver language sits
under that. Nothing else: a display at a barrier is read in a few seconds
through a windscreen.

**Idle is a black frame, and so is exit.** Not a logo, not a clock: a screen
showing anything at all invites a driver to read it, and there is nothing to
say between arrivals. On exit the frame is blacked in a `finally`.

**A CRASH LEAVES THE LAST FRAME UP, and that is stated rather than pretended
away.** A framebuffer holds what was written to it; nothing repaints it. So a
process that dies mid-window leaves a ticket on the screen -- and **that ticket
can never be vended**, because a restarted agent starts with no pending tickets
and the press that would confirm it goes to a person. The harm is a driver
photographing a code that will not work, and the answer they get is a human.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import font, qr

log = logging.getLogger(__name__)

#: The pixel depths this build writes. Every one of them is white-is-all-ones,
#: which is what lets a monochrome frame ignore channel order.
SUPPORTED_DEPTHS = (16, 24, 32)

#: How many MODULES of quiet zone go round the symbol. Four is the standard's
#: minimum and there is no reason to spend more of a small screen on it.
QUIET_MODULES = 4

#: What fraction of the short side the symbol may use. The rest is the margin
#: and the text under it.
SYMBOL_SHARE = 0.62

#: How many display pixels one glyph module is, for the reference, and for the
#: instruction lines. The reference is bigger because it is the part a person
#: reads out loud over an intercom.
REFERENCE_SCALE_MIN = 2
LINE_SCALE_MIN = 1


class DisplayUnavailable(Exception):
    """The screen cannot be written, or its geometry cannot be read.

    One exception for both, because to a driver at a barrier they are one fact:
    there is no code to photograph. The agent's answer is the same either way --
    no ticket is offered and the press goes to a person.
    """


@dataclass(frozen=True, slots=True)
class Geometry:
    """What the DRIVER says the screen is. Read at startup, never configured."""

    width: int
    height: int
    bits_per_pixel: int
    #: Bytes per row. Usually `width * bits_per_pixel // 8`, and NOT always: a
    #: driver may pad each row to an alignment, and a frame written at the wrong
    #: stride is diagonal noise rather than a smaller picture. Read from sysfs
    #: where the driver publishes it and computed only where it does not.
    stride: int

    @property
    def bytes_per_pixel(self) -> int:
        return self.bits_per_pixel // 8


def sysfs_for(framebuffer: Path) -> Path:
    """Where this framebuffer's attributes live: `/sys/class/graphics/<fb0>`.

    Derived from the device's own name rather than configured, so the two cannot
    name different screens -- which is a frame written with one screen's
    geometry onto another.
    """
    return Path("/sys/class/graphics") / framebuffer.name


def read_geometry(framebuffer: Path, sysfs: Path | None = None) -> Geometry:
    """The geometry, or a `DisplayUnavailable` naming the file that failed.

    `sysfs` is passed in only by the tests, which stand a directory of the same
    two files in for a driver's. The default is derived, so a real installation
    has nothing to configure and nothing to get wrong.
    """
    root = sysfs if sysfs is not None else sysfs_for(framebuffer)

    def read(name: str) -> str:
        path = root / name
        try:
            return path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise DisplayUnavailable(
                f"{path}: {exc}. This is where the driver publishes what the screen IS, and "
                "a display whose geometry cannot be read is one this agent would write "
                "diagonal noise to. Refusing to start."
            ) from exc

    raw = read("virtual_size")
    try:
        width, height = (int(part) for part in raw.split(","))
    except ValueError as exc:
        raise DisplayUnavailable(
            f"{root / 'virtual_size'} holds {raw!r}, which is not `width,height`."
        ) from exc
    try:
        depth = int(read("bits_per_pixel"))
    except ValueError as exc:
        raise DisplayUnavailable(
            f"{root / 'bits_per_pixel'} is not a number."
        ) from exc
    if depth not in SUPPORTED_DEPTHS:
        raise DisplayUnavailable(
            f"{framebuffer} is {depth} bits per pixel and this build writes "
            f"{', '.join(str(one) for one in SUPPORTED_DEPTHS)}. At those depths white is "
            "every bit set and black is every bit clear, so a monochrome frame does not "
            "have to know the channel order; at any other depth it would."
        )
    if width < 1 or height < 1:
        raise DisplayUnavailable(f"{framebuffer} says it is {width}x{height}")
    stride = width * depth // 8
    try:
        published = (root / "stride").read_text(encoding="ascii").strip()
    except OSError:
        # NOT a refusal. `stride` is not published by every driver, and the
        # computed value is right wherever rows are unpadded -- which is most
        # of them. What would be wrong is to guess silently on a driver that
        # DOES publish one, which is why it is read first.
        log.info("%s publishes no stride; using %d bytes per row", root, stride)
    else:
        try:
            stride = max(int(published), stride)
        except ValueError:
            log.warning("%s holds %r, which is not a number of bytes", root / "stride",
                        published)
    return Geometry(width=width, height=height, bits_per_pixel=depth, stride=stride)


def _blank_bitmap(width: int, height: int) -> list[list[int]]:
    return [[0] * width for _ in range(height)]


def _draw(bitmap, rows, top: int, left: int, scale: int) -> None:
    """One 0/1 picture onto another, scaled by whole modules.

    Whole modules, never fractional: a QR module drawn 3.4 pixels wide is a QR
    module a camera reads as the wrong colour at its edges.
    """
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if not value:
                continue
            for dy in range(scale):
                for dx in range(scale):
                    y, x = top + row_index * scale + dy, left + column_index * scale + dx
                    if 0 <= y < len(bitmap) and 0 <= x < len(bitmap[0]):
                        bitmap[y][x] = 1


def frame_for(
    payload: str, ticket_ref: str, instructions: tuple[str, ...], geometry: Geometry
) -> list[list[int]]:
    """The whole frame as rows of 0/1, `1` meaning DARK.

    Laid out from the short side: the symbol takes `SYMBOL_SHARE` of it, the
    reference goes under the symbol at the largest whole scale that fits, and
    the instruction lines follow. A line that does not fit is not shrunk below
    one pixel per module and is not clipped -- it is left OUT, and the reference
    and the code are what a driver needs.
    """
    bitmap = _blank_bitmap(geometry.width, geometry.height)
    short = min(geometry.width, geometry.height)

    modules = qr.encode(payload)
    across = len(modules) + QUIET_MODULES * 2
    scale = max(1, int(short * SYMBOL_SHARE) // across)
    symbol = across * scale
    top = max(0, (short - symbol) // 8)
    left = max(0, (geometry.width - symbol) // 2)
    _draw(bitmap, modules, top + QUIET_MODULES * scale, left + QUIET_MODULES * scale, scale)

    cursor = top + symbol + scale * 2
    reference = font.render(ticket_ref)
    reference_scale = max(
        REFERENCE_SCALE_MIN,
        min(
            (geometry.width - 2 * scale) // max(1, font.width_of(ticket_ref)),
            (geometry.height - cursor) // (font.CELL_HEIGHT * 3),
        ),
    )
    if font.width_of(ticket_ref) * reference_scale <= geometry.width:
        _draw(
            bitmap,
            reference,
            cursor,
            (geometry.width - font.width_of(ticket_ref) * reference_scale) // 2,
            reference_scale,
        )
        cursor += font.CELL_HEIGHT * reference_scale + reference_scale * 2

    for line in instructions:
        rows = font.render(line)
        line_scale = max(
            LINE_SCALE_MIN, (geometry.width - 4) // max(1, font.width_of(line))
        )
        needed = font.CELL_HEIGHT * line_scale
        if cursor + needed > geometry.height:
            # LEFT OUT rather than clipped or shrunk to nothing. Half a sentence
            # is worse than none: a driver reads it as the whole one.
            log.info("no room for an instruction line on this display")
            break
        _draw(
            bitmap,
            rows,
            cursor,
            max(0, (geometry.width - font.width_of(line) * line_scale) // 2),
            line_scale,
        )
        cursor += needed + line_scale
    return bitmap


def to_bytes(bitmap, geometry: Geometry) -> bytes:
    """The frame as the framebuffer wants it: `stride` bytes per row.

    White is `0xff` in every byte and black is `0x00`, which is white and black
    at all three supported depths whatever the channel order. The padding
    between `width * bytes_per_pixel` and `stride` is written BLACK rather than
    left alone: what is already there is the previous frame.
    """
    per_pixel = geometry.bytes_per_pixel
    out = bytearray()
    for row in bitmap:
        line = bytearray()
        for value in row:
            line += b"\x00" * per_pixel if value else b"\xff" * per_pixel
        line += b"\x00" * (geometry.stride - len(line))
        out += line[: geometry.stride]
    return bytes(out)


@dataclass(frozen=True, slots=True)
class Display:
    """One declared screen. Its geometry was read at startup."""

    name: str
    framebuffer: Path
    geometry: Geometry

    def show(self, bitmap) -> None:
        try:
            with open(self.framebuffer, "wb") as handle:
                handle.write(to_bytes(bitmap, self.geometry))
        except OSError as exc:
            raise DisplayUnavailable(f"{self.framebuffer}: {exc}") from exc

    def blank(self) -> None:
        """A BLACK frame. What idle looks like, and what exit looks like.

        Every pixel DARK, which is every byte zero -- not the bitmap's own
        `0`, which means *light* because a QR symbol is dark modules on a light
        ground. Written as `1` here and caught by a test: `blank()` used to pass
        an all-zero bitmap and put up a WHITE screen, which at a barrier at
        night is a floodlight pointed at a windscreen.
        """
        width, height = self.geometry.width, self.geometry.height
        self.show([[1] * width for _ in range(height)])


def open_display(name: str, framebuffer: Path, sysfs: Path | None = None) -> Display:
    """Read the geometry and prove the device can be written, at STARTUP.

    Both, and in that order: a geometry that reads and a device that refuses a
    write is a display that fails at the first arrival instead of at the moment
    somebody is installing it.
    """
    geometry = read_geometry(framebuffer, sysfs)
    display = Display(name=name, framebuffer=Path(framebuffer), geometry=geometry)
    display.blank()
    return display


__all__ = [
    "LINE_SCALE_MIN",
    "QUIET_MODULES",
    "REFERENCE_SCALE_MIN",
    "SUPPORTED_DEPTHS",
    "SYMBOL_SHARE",
    "Display",
    "DisplayUnavailable",
    "Geometry",
    "frame_for",
    "open_display",
    "read_geometry",
    "sysfs_for",
    "to_bytes",
]
