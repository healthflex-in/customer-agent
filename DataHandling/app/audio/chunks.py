"""Putting recorder chunks together in the order the recorder made them.

The browser sends each 100 ms chunk after an asynchronous read, so a small chunk
can reach the server before the larger first one. The first chunk of a webm
recording holds the file header, and a recording that does not start with it
cannot be decoded.
"""

WEBM_HEADER = b"\x1a\x45\xdf\xa3"


def add_chunk(buffer: bytearray, chunk: bytes) -> bool:
    """Append ``chunk`` to ``buffer``; returns True when it was moved to the front.

    A chunk that starts with the webm header belongs first. If it arrives after
    other audio, and nothing in the buffer is a header yet, it goes to the front.
    Every other chunk is appended in arrival order.
    """

    if bytes(chunk[:4]) == WEBM_HEADER and buffer and bytes(buffer[:4]) != WEBM_HEADER:
        buffer[:0] = chunk
        return True
    buffer.extend(chunk)
    return False
