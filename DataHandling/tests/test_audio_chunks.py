"""A late-arriving first chunk (the one with the webm header) is put back at the front."""

import unittest

from app.audio.chunks import WEBM_HEADER, add_chunk


HEADER_CHUNK = WEBM_HEADER + b"header-and-first-audio"
SECOND = b"second-chunk"
THIRD = b"third-chunk"


class AddChunkTests(unittest.TestCase):
    def test_chunks_in_order_are_appended_as_they_come(self):
        buffer = bytearray()

        moved = [add_chunk(buffer, chunk) for chunk in (HEADER_CHUNK, SECOND, THIRD)]

        self.assertEqual(bytes(buffer), HEADER_CHUNK + SECOND + THIRD)
        self.assertEqual(moved, [False, False, False])

    def test_a_header_chunk_that_arrives_second_goes_first(self):
        buffer = bytearray()

        add_chunk(buffer, SECOND)
        moved = add_chunk(buffer, HEADER_CHUNK)
        add_chunk(buffer, THIRD)

        self.assertTrue(moved)
        self.assertEqual(bytes(buffer), HEADER_CHUNK + SECOND + THIRD)

    def test_a_header_chunk_that_arrives_after_several_goes_first(self):
        buffer = bytearray()

        add_chunk(buffer, SECOND)
        add_chunk(buffer, THIRD)
        add_chunk(buffer, HEADER_CHUNK)

        self.assertEqual(bytes(buffer), HEADER_CHUNK + SECOND + THIRD)

    def test_a_clip_that_already_starts_with_the_header_is_left_alone(self):
        buffer = bytearray(HEADER_CHUNK)

        moved = add_chunk(buffer, WEBM_HEADER + b"another header")

        self.assertFalse(moved)
        self.assertTrue(bytes(buffer).startswith(HEADER_CHUNK))

    def test_other_formats_are_never_reordered(self):
        buffer = bytearray()

        add_chunk(buffer, b"RIFFfirst")
        add_chunk(buffer, b"second")

        self.assertEqual(bytes(buffer), b"RIFFfirstsecond")


if __name__ == "__main__":
    unittest.main()
