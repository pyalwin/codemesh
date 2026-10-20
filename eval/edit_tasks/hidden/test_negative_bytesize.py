from pydantic.types import ByteSize


def test_negative_magnitude_scales_with_both_unit_systems():
    assert ByteSize(-1536).human_readable() == "-1.5KiB"
    assert ByteSize(-1500).human_readable(decimal=True, separator=" ") == "-1.5 KB"


def test_positive_and_small_values_are_unchanged():
    assert ByteSize(1536).human_readable() == "1.5KiB"
    assert ByteSize(-12).human_readable() == "-12B"

