#!/usr/bin/env python3
r"""
fo_text.py
===================================================================
PRODUCTION CODE
The File Organizer -- B6 (Shared Text Measurement)
Module version: 1.0.0
===================================================================

Counting words, lines and characters without building a list of every
word first.

THE DEFECT THIS REPLACES (B5-E.F009)
------------------------------------
Three analyzers counted words with `len(text.split())`. An 8 MB
document produced roughly 110.5 MB of heap -- an 11x amplification --
because `str.split()` allocates a Python object for every token before
`len()` throws all of them away. On an 8 MB document that is about
1.55 million string objects, each carrying its own header, created
solely to be counted and discarded.

The count itself was never the problem. Materialising the tokens was.

WHAT THIS DOES INSTEAD
----------------------
`count_words()` walks the string once and counts transitions from
whitespace to non-whitespace. Allocation is constant regardless of
document size, and the answer is IDENTICAL to `len(text.split())` for
every input -- including the edge cases that make a naive rewrite
wrong:

  * leading and trailing whitespace produce no phantom tokens;
  * runs of whitespace count as one separator, not several;
  * `str.split()` with no argument splits on Unicode whitespace, not
    just ASCII, so this uses `str.isspace()` rather than a literal set;
  * the empty string, and a string of nothing but whitespace, are both
    zero.

`WORD_COUNT_EQUIVALENCE` in the test suite asserts that equality over
a corpus of awkward inputs, because "obviously the same" is exactly
the kind of claim that turns out not to be.

WHY A SHARED MODULE
-------------------
B5-C and the D findings both flag duplicated analyzer machinery. Three
scripts had three copies of the same counting idiom, which is three
places for it to be fixed incompletely. There is now one.
"""


#: How much of a file to read at a time when counting from a stream.
CHUNK_CHARS = 1 << 20


def count_words(text):
    r"""Exactly `len(text.split())`, in constant additional memory.

    Counts whitespace -> non-whitespace transitions. See the module
    header for why each edge case below is handled the way it is.
    """
    if not text:
        return 0
    count = 0
    in_word = False
    for char in text:
        if char.isspace():
            in_word = False
        elif not in_word:
            in_word = True
            count += 1
    return count


#: The exact boundary set `str.splitlines()` uses. Written out rather
#: than approximated: \n alone is wrong on Windows text that uses \r,
#: on old Mac text, and on anything carrying \x85 or \u2028. An earlier
#: draft of this module counted boundaries in fixed-size slices and got
#: \r\n wrong whenever the pair straddled a slice edge -- which is why
#: the counter below is a character state machine and not a chunker.
LINE_BOUNDARIES = frozenset(
    "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


def count_lines(text):
    r"""Exactly `len(text.splitlines())`, without building the list.

    `splitlines()` is not `count('\n') + 1`. It splits on the full
    Unicode boundary set above, it treats '\r\n' as ONE boundary, and a
    trailing boundary does not produce a final empty element.

    Lines = boundaries counted, plus one if the text does not end on a
    boundary.
    """
    if not text:
        return 0
    count = 0
    previous_cr = False
    ended_on_boundary = False
    for char in text:
        if char in LINE_BOUNDARIES:
            # '\r\n' is a single boundary, so the '\n' after a '\r'
            # does not count again.
            if not (previous_cr and char == "\n"):
                count += 1
            previous_cr = (char == "\r")
            ended_on_boundary = True
        else:
            previous_cr = False
            ended_on_boundary = False
    if not ended_on_boundary:
        count += 1
    return count


class TextStats(object):
    """Characters, words and lines accumulated across many chunks.

    Lets an extractor measure text it is streaming to disk without
    holding the whole document to measure it afterwards.
    """

    __slots__ = ("chars", "words", "lines", "_in_word", "_previous_cr",
                 "_ended_on_boundary", "_finished")

    def __init__(self):
        self.chars = 0
        self.words = 0
        self.lines = 0
        self._in_word = False
        self._previous_cr = False
        # Starts True so that an empty document reports zero lines,
        # matching ''.splitlines().
        self._ended_on_boundary = True
        self._finished = False

    def feed(self, chunk):
        r"""Add one chunk.

        The word and line state machines both CARRY ACROSS the call, so
        a word or a '\r\n' pair split between two chunks is counted
        once and correctly. That property is the entire reason this
        class exists rather than the caller joining the chunks first --
        joining them is what B5-E.F009 measured the cost of.
        """
        if not chunk:
            return self
        self.chars += len(chunk)
        for char in chunk:
            if char in LINE_BOUNDARIES:
                if not (self._previous_cr and char == "\n"):
                    self.lines += 1
                self._previous_cr = (char == "\r")
                self._ended_on_boundary = True
                self._in_word = False
            else:
                self._previous_cr = False
                self._ended_on_boundary = False
                if char.isspace():
                    self._in_word = False
                elif not self._in_word:
                    self._in_word = True
                    self.words += 1
        return self

    def finish(self):
        """Close the last line, if the text did not end on a boundary."""
        if not self._finished and not self._ended_on_boundary:
            self.lines += 1
        self._finished = True
        return self

    def as_dict(self):
        return {"CharCount": str(self.chars),
                "WordCount": str(self.words),
                "LineCount": str(self.lines)}


# ---------------------------------------------------------------------------
# Text decoding -- deterministic first, probabilistic only as fallback
# ---------------------------------------------------------------------------

def decode_bytes(raw):
    r"""Decode text bytes without letting chardet override valid UTF-8.

    B6.1 correctness rule: Unicode encodings with an explicit BOM win;
    otherwise valid UTF-8 wins. Only byte streams that are not valid UTF-8
    are handed to probabilistic detection. This prevents short UTF-8 text
    such as the bytes for ``Ω`` from being misidentified as Windows-1252
    and silently persisted as ``Î©``.

    Returns ``(text, encoding_label)``. The fallback always returns text;
    replacement characters are used only after strict decoding options fail.
    """
    if raw is None:
        return "", "utf-8"
    if not isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw)
    raw = bytes(raw)

    # BOM-bearing Unicode is authoritative. Check UTF-32 before UTF-16
    # because UTF-32LE begins with the UTF-16LE prefix.
    #
    # B6.2 P4d -- these literals were previously double-escaped
    # (b"\\xff\\xfe" is the eight ASCII bytes \ x f f \ x f e, not the
    # 2-byte BOM), so the table never matched and a real UTF-8-with-BOM
    # file fell through to a plain utf-8 decode that KEPT the U+FEFF
    # character at the start of the text. Real byte literals now, and the
    # utf-8-sig / utf-16 / utf-32 codecs strip their own BOM.
    bom_table = (
        (b"\xff\xfe\x00\x00", "utf-32", "utf-32 BOM"),
        (b"\x00\x00\xfe\xff", "utf-32", "utf-32 BOM"),
        (b"\xef\xbb\xbf", "utf-8-sig", "utf-8 BOM"),
        (b"\xff\xfe", "utf-16", "utf-16 BOM"),
        (b"\xfe\xff", "utf-16", "utf-16 BOM"),
    )
    for prefix, codec, label in bom_table:
        if raw.startswith(prefix):
            text = raw.decode(codec, errors="strict")
            # Belt and braces: utf-16/32 codecs strip the BOM and
            # utf-8-sig strips EF BB BF -- but never hand back a leading
            # U+FEFF if one somehow survives.
            if text[:1] == "﻿":
                text = text[1:]
            return text, label

    # UTF-8 is deterministic. Do this BEFORE chardet.
    try:
        return raw.decode("utf-8", errors="strict"), "utf-8"
    except UnicodeDecodeError:
        pass

    # B6.2 P4d -- chardet's guess is TRUSTED ONLY WHEN IT IS CONFIDENT.
    # On a short sample of a legacy encoding chardet 7.x routinely picks
    # an unrelated codec at low confidence -- Shift-JIS read as IBM855,
    # GB2312 as KOI8-R -- and the old code decoded with the guess anyway
    # and stored the bogus encoding label as fact. The result was wrong
    # word and character counts (a mis-decoded multi-byte stream reports
    # one character per byte) and mojibake extracted text, all silently
    # marked "analyzed". If chardet is not reasonably sure, we decline to
    # guess and take the lossless latin-1 fallback, whose label already
    # says the encoding is uncertain -- and whose one-byte-one-character
    # decode keeps the counts honest.
    CHARDET_MIN_CONFIDENCE = 0.65
    encoding = None
    guess_note = ""
    try:
        import chardet
        detected = chardet.detect(raw) or {}
        guess = detected.get("encoding")
        confidence = detected.get("confidence") or 0.0
        if guess and confidence >= CHARDET_MIN_CONFIDENCE:
            encoding = guess
        elif guess:
            guess_note = " -- chardet guessed %s at %.2f confidence, not trusted" % (
                guess, confidence)
    except Exception:
        encoding = None

    if encoding:
        try:
            return raw.decode(encoding, errors="strict"), str(encoding)
        except (LookupError, UnicodeDecodeError, TypeError):
            try:
                return raw.decode(encoding, errors="replace"), str(encoding) + " (replacement)"
            except (LookupError, TypeError):
                pass

    # latin-1 is a lossless byte-to-codepoint final fallback: it cannot
    # throw or silently drop bytes. The label makes the uncertainty visible.
    return raw.decode("latin-1"), "latin-1 (fallback)" + guess_note
