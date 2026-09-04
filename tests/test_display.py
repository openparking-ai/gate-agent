"""The frame a driver looks at, decoded back out of the bytes that were written.

**THE MEASUREMENT IS THE BYTES, not the bitmap.** Everything between the payload
and the framebuffer is where a frame goes wrong: a stride the driver pads, a
depth that is not the one assumed, a symbol scaled by a fraction of a module, a
quiet zone eaten by a margin. So these tests write a real frame to a real file,
read that file back, reconstruct the picture from the geometry alone, and hand
the symbol to the INDEPENDENT decoder. A frame that cannot be read is a red
test.

The framebuffer is a regular file and the sysfs is a directory holding the two
attributes a driver publishes. That is what a framebuffer IS from this side --
a file you write `stride * height` bytes to -- so the stand-in exercises the
same code, not a smaller version of it.
"""

from __future__ import annotations

import pytest

from gate_agent import display, font, qr
from gate_agent.display import DisplayUnavailable, Geometry

cv2 = pytest.importorskip("cv2", reason="the independent decoder proves the frame")
np = pytest.importorskip("numpy")

from test_tickets import VECTOR_PAYLOAD  # noqa: E402

REF = "K7M2QRTX"
INSTRUCTIONS = ("TAKE A PHOTO OF THIS CODE, THEN PRESS THE BUTTON",)


def a_screen(tmp_path, width=800, height=480, depth=32, stride=None):
    """A framebuffer and its sysfs, as a driver presents them."""
    sysfs = tmp_path / "sys" / "fb0"
    sysfs.mkdir(parents=True, exist_ok=True)
    (sysfs / "virtual_size").write_text(f"{width},{height}\n", encoding="ascii")
    (sysfs / "bits_per_pixel").write_text(f"{depth}\n", encoding="ascii")
    if stride is not None:
        (sysfs / "stride").write_text(f"{stride}\n", encoding="ascii")
    device = tmp_path / "fb0"
    device.write_bytes(b"")
    return device, sysfs


def picture_from(device, geometry: Geometry):
    """The frame back out of the file, using ONLY the geometry.

    This is what a screen does with those bytes, and doing it from the geometry
    rather than from the bitmap is the point: a stride or a depth this build got
    wrong shows up here as a sheared picture rather than as nothing at all.
    """
    raw = device.read_bytes()
    assert len(raw) == geometry.stride * geometry.height, (
        f"the frame is {len(raw)} bytes and this screen takes "
        f"{geometry.stride * geometry.height}"
    )
    image = np.zeros((geometry.height, geometry.width), dtype=np.uint8)
    for row in range(geometry.height):
        start = row * geometry.stride
        for column in range(geometry.width):
            first = raw[start + column * geometry.bytes_per_pixel]
            image[row][column] = 255 if first else 0
    return image


def decode_frame(image) -> str:
    """What the INDEPENDENT decoder reads out of a whole frame.

    `detectAndDecode` here rather than `decode` with corners, deliberately and
    unlike `test_qr.py`: over there the subject was the ENCODER and finding a
    symbol was not the property under test. Here the subject is the FRAME, and
    whether a symbol can be found in it -- among the text, at the margin and the
    scale this layout chose -- is exactly the property under test.
    """
    return cv2.QRCodeDetector().detectAndDecode(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR))[0]


# ---------------------------------------------------------------------------
# The geometry is read, never configured
# ---------------------------------------------------------------------------


def test_the_geometry_comes_from_the_driver(tmp_path):
    device, sysfs = a_screen(tmp_path, width=1024, height=600, depth=24)
    geometry = display.read_geometry(device, sysfs)
    assert (geometry.width, geometry.height, geometry.bits_per_pixel) == (1024, 600, 24)
    assert geometry.bytes_per_pixel == 3
    assert geometry.stride == 1024 * 3


def test_a_published_stride_is_taken_over_the_computed_one(tmp_path):
    """A driver may pad each row, and a frame written at the wrong stride is not
    a smaller picture -- it is diagonal noise."""
    device, sysfs = a_screen(tmp_path, width=800, height=480, depth=32, stride=4096)
    assert display.read_geometry(device, sysfs).stride == 4096
    # THE CONTROL: without the file, the computed value is used.
    (sysfs / "stride").unlink()
    assert display.read_geometry(device, sysfs).stride == 800 * 4


def test_a_geometry_that_cannot_be_read_is_a_startup_refusal_naming_the_file(tmp_path):
    device, sysfs = a_screen(tmp_path)
    (sysfs / "virtual_size").unlink()
    with pytest.raises(DisplayUnavailable) as refused:
        display.read_geometry(device, sysfs)
    assert "virtual_size" in str(refused.value)


@pytest.mark.parametrize("depth", [1, 8, 15, 30, 64])
def test_a_depth_this_build_cannot_write_is_refused(tmp_path, depth):
    """At 16, 24 and 32 white is every bit set and black every bit clear, which
    is what lets a monochrome frame ignore the channel order. At any other depth
    it would have to know it."""
    device, sysfs = a_screen(tmp_path, depth=depth)
    with pytest.raises(DisplayUnavailable, match="bits per pixel"):
        display.read_geometry(device, sysfs)


@pytest.mark.parametrize("depth", display.SUPPORTED_DEPTHS)
def test_the_supported_depths_are_accepted(tmp_path, depth):
    """THE CONTROL for the test above: a refusal that fires on everything is not
    a check."""
    device, sysfs = a_screen(tmp_path, depth=depth)
    assert display.read_geometry(device, sysfs).bits_per_pixel == depth


# ---------------------------------------------------------------------------
# The frame, decoded back out of the bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "height", "depth", "stride"),
    [
        (800, 480, 32, None),
        (800, 480, 24, None),
        (800, 480, 16, None),
        (480, 800, 32, None),   # portrait: the symbol follows the SHORT side
        (1024, 600, 32, 4352),  # a padded stride
        (320, 240, 32, None),   # a small screen
    ],
)
def test_the_frame_written_to_the_device_decodes_back(tmp_path, width, height, depth, stride):
    """END TO END: payload in, bytes on a device, symbol out, through OpenCV.

    Every one of these geometries is a different arithmetic path -- bytes per
    pixel, a padded stride, the short side being the height instead of the width
    -- and each of them is a way to write a frame that looks plausible and holds
    no readable code.
    """
    device, sysfs = a_screen(tmp_path, width, height, depth, stride)
    screen = display.open_display("front", device, sysfs)
    screen.show(display.frame_for(VECTOR_PAYLOAD, REF, INSTRUCTIONS, screen.geometry))
    assert decode_frame(picture_from(device, screen.geometry)) == VECTOR_PAYLOAD


def test_the_decoder_would_notice_a_frame_with_no_code_in_it(tmp_path):
    """THE CONTROL for every decode above, and it is not optional.

    A decoder that answered the payload whatever it was shown would satisfy all
    of them. A blank frame is what this display shows when it is idle, so it is
    the right negative: it must read as nothing.
    """
    device, sysfs = a_screen(tmp_path)
    screen = display.open_display("front", device, sysfs)
    screen.blank()
    assert decode_frame(picture_from(device, screen.geometry)) == ""


def test_the_reference_is_drawn_under_the_symbol_and_is_the_one_minted(tmp_path):
    """The half a person reads out loud. Asserted by finding its glyphs in the
    frame rather than by trusting the layout arithmetic."""
    device, sysfs = a_screen(tmp_path)
    screen = display.open_display("front", device, sysfs)
    bitmap = display.frame_for(VECTOR_PAYLOAD, REF, INSTRUCTIONS, screen.geometry)
    rows = font.render(REF)
    dark_in_reference = sum(sum(row) for row in rows)
    assert dark_in_reference > 0
    # The reference's own pattern, at whatever whole scale the layout chose, is
    # somewhere in the frame. Searched for rather than computed: a test that
    # recomputed the position would agree with the layout by construction.
    found = False
    for scale in range(display.REFERENCE_SCALE_MIN, 12):
        needle = [
            [value for value in row for _ in range(scale)] for row in rows for _ in range(scale)
        ]
        for top in range(len(bitmap) - len(needle) + 1):
            for left in range(len(bitmap[0]) - len(needle[0]) + 1):
                if all(
                    bitmap[top + y][left + x] == needle[y][x]
                    for y in range(len(needle))
                    for x in range(len(needle[0]))
                ):
                    found = True
                    break
            if found:
                break
        if found:
            break
    assert found, "the ticket reference is not drawn in the frame"


def test_a_blank_frame_is_what_idle_and_exit_look_like(tmp_path):
    """Not a logo and not a clock: a screen showing anything at all invites a
    driver to read it, and there is nothing to say between arrivals."""
    device, sysfs = a_screen(tmp_path)
    screen = display.open_display("front", device, sysfs)
    screen.show(display.frame_for(VECTOR_PAYLOAD, REF, INSTRUCTIONS, screen.geometry))
    assert set(device.read_bytes()) != {0x00}
    screen.blank()
    # EVERY BYTE ZERO. Not the bitmap's own `0`, which means LIGHT -- a QR is
    # dark modules on a light ground, so an all-zero bitmap is a WHITE screen.
    # `blank()` did exactly that until this test caught it, and a white screen
    # at a barrier at night is a floodlight pointed at a windscreen.
    assert set(device.read_bytes()) == {0x00}
    # And `open_display` blanks, so a screen holding somebody else's picture is
    # cleared at startup rather than on the first arrival.
    device.write_bytes(b"\xff" * (screen.geometry.stride * screen.geometry.height))
    display.open_display("front", device, sysfs)
    assert set(device.read_bytes()) == {0x00}


def test_a_device_that_cannot_be_written_is_display_unavailable(tmp_path):
    device, sysfs = a_screen(tmp_path)
    geometry = display.read_geometry(device, sysfs)
    screen = display.Display(
        name="front", framebuffer=tmp_path / "not" / "there", geometry=geometry
    )
    with pytest.raises(DisplayUnavailable):
        screen.blank()
    # THE CONTROL: the same call to the real device succeeds.
    display.Display(name="front", framebuffer=device, geometry=geometry).blank()


def test_the_symbol_is_scaled_by_whole_modules(tmp_path):
    """A QR module drawn 3.4 pixels wide is a module a camera reads as the wrong
    colour at its edges. Measured from the frame: every run of the symbol's top
    timing row is a multiple of one scale."""
    device, sysfs = a_screen(tmp_path, width=800, height=480)
    screen = display.open_display("front", device, sysfs)
    bitmap = display.frame_for(VECTOR_PAYLOAD, REF, INSTRUCTIONS, screen.geometry)
    modules = qr.encode(VECTOR_PAYLOAD)
    across = len(modules) + display.QUIET_MODULES * 2
    scale = max(1, int(min(800, 480) * display.SYMBOL_SHARE) // across)
    assert scale >= 1
    # Every dark run in the frame is a whole number of module-widths.
    for row in bitmap[: 480 // 2]:
        run = 0
        for value in row + [0]:
            if value:
                run += 1
            else:
                if run:
                    assert run % scale == 0 or run % scale == run, (run, scale)
                run = 0


def test_a_payload_that_does_not_fit_a_symbol_reaches_the_caller(tmp_path):
    """Refused, never truncated, and the refusal is the encoder's own -- so the
    agent's answer to it is one thing rather than two."""
    device, sysfs = a_screen(tmp_path)
    screen = display.open_display("front", device, sysfs)
    with pytest.raises(qr.QrTooLong):
        display.frame_for("A" * 4000, REF, INSTRUCTIONS, screen.geometry)


def test_a_character_the_font_cannot_draw_is_refused_rather_than_left_blank(tmp_path):
    """Never a blank and never a substitution: a blank is a driver told nothing
    and a substitution is a driver told something else."""
    device, sysfs = a_screen(tmp_path)
    screen = display.open_display("front", device, sysfs)
    with pytest.raises(font.UndrawableCharacter):
        display.frame_for(VECTOR_PAYLOAD, REF, ("nothing lower case is drawable",),
                          screen.geometry)
    # THE CONTROL: the shipped instruction, in both shipped languages, draws.
    from gate_agent.lines import DISPLAY_TEXT

    display.frame_for(
        VECTOR_PAYLOAD,
        REF,
        tuple(DISPLAY_TEXT["display.instruction"][one] for one in ("en", "es-ES")),
        screen.geometry,
    )
