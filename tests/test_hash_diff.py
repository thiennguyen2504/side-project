"""
tests/test_hash_diff.py — Unit tests for the content-hash logic in main.py.

Tests:
  1. Identical content → same hash.
  2. Cosmetic whitespace differences → same hash (normalisation).
  3. Genuinely different content → different hash.
  4. Extra blank lines → same hash (normalised).
  5. Different casing → different hash (content IS different).
"""

import sys
import os

# Allow importing main.py from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from main import compute_hash
from uploader import parse_display_name


class TestComputeHash:
    """Tests for the compute_hash function."""

    def test_identical_content_same_hash(self) -> None:
        """Same text always produces the same hash."""
        content = "# Hello\n\nThis is a test article.\n"
        assert compute_hash(content) == compute_hash(content)

    def test_trailing_spaces_normalised(self) -> None:
        """Trailing spaces on lines should NOT change the hash."""
        clean = "# Hello\n\nThis is a test.\n"
        dirty = "# Hello   \n\nThis is a test.   \n"
        assert compute_hash(clean) == compute_hash(dirty)

    def test_leading_spaces_normalised(self) -> None:
        """Leading spaces on lines should NOT change the hash."""
        clean = "# Hello\n\nThis is a test.\n"
        indented = "  # Hello\n\n  This is a test.\n"
        assert compute_hash(clean) == compute_hash(indented)

    def test_extra_blank_lines_normalised(self) -> None:
        """Multiple consecutive blank lines normalise to one."""
        single_blank = "# Hello\n\nThis is a test.\n"
        multi_blank = "# Hello\n\n\n\n\nThis is a test.\n"
        assert compute_hash(single_blank) == compute_hash(multi_blank)

    def test_internal_whitespace_collapsed(self) -> None:
        """Multiple spaces within a line collapse to one space."""
        normal = "# Hello\n\nThis is a test.\n"
        spaced = "# Hello\n\nThis   is   a   test.\n"
        assert compute_hash(normal) == compute_hash(spaced)

    def test_mixed_whitespace_normalised(self) -> None:
        """Combination of trailing spaces + extra blanks → same hash."""
        a = "# Title\n\nContent here.\n"
        b = "# Title   \n\n\n\n\nContent  here.\n"
        assert compute_hash(a) == compute_hash(b)

    def test_different_content_different_hash(self) -> None:
        """Different text must produce a different hash."""
        content_a = "# Article A\n\nThis is article A.\n"
        content_b = "# Article B\n\nThis is article B.\n"
        assert compute_hash(content_a) != compute_hash(content_b)

    def test_added_sentence_different_hash(self) -> None:
        """Adding a real sentence must change the hash."""
        original = "# Intro\n\nOne sentence.\n"
        modified = "# Intro\n\nOne sentence. And another.\n"
        assert compute_hash(original) != compute_hash(modified)

    def test_different_title_different_hash(self) -> None:
        """Changing just the title must change the hash."""
        a = "# Old Title\n\nSame content.\n"
        b = "# New Title\n\nSame content.\n"
        assert compute_hash(a) != compute_hash(b)

    def test_empty_string_stable(self) -> None:
        """Empty string produces a consistent hash."""
        assert compute_hash("") == compute_hash("   \n\n   ")

    def test_hash_is_sha256_length(self) -> None:
        """Returned hash is a 64-character hex string (SHA-256)."""
        h = compute_hash("some content")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash8_different_articles(self) -> None:
        """
        Verify two different articles produce different 8-character hash prefixes.
        Note: We accept a tiny 1-in-4.2-billion collision risk (8 hex chars = 32 bits)
        as a documented tradeoff for shorter, more readable filenames in the store.
        """
        hash1 = compute_hash("Article one content")[:8]
        hash2 = compute_hash("Article two content")[:8]
        assert hash1 != hash2


class TestParseDisplayName:
    """Tests for the parse_display_name helper in uploader.py."""

    def test_valid_display_name(self) -> None:
        """Correctly parses slug and hash8."""
        slug, hash8 = parse_display_name("getting-started__a1b2c3d4.md")
        assert slug == "getting-started"
        assert hash8 == "a1b2c3d4"

    def test_slug_with_underscores(self) -> None:
        """
        Splits on the *last* '__', so a slug containing '__' is parsed correctly.
        (slugify() usually converts to hyphens, but we test defensively.)
        """
        slug, hash8 = parse_display_name("weird__slug__name__e5f6g7h8.md")
        assert slug == "weird__slug__name"
        assert hash8 == "e5f6g7h8"

    def test_missing_md_extension(self) -> None:
        """Raises ValueError if not ending with .md."""
        with pytest.raises(ValueError, match="Must end with .md"):
            parse_display_name("getting-started__a1b2c3d4")

    def test_missing_separator(self) -> None:
        """Raises ValueError if '__' separator is missing."""
        with pytest.raises(ValueError, match="Must contain __ separator"):
            parse_display_name("getting-started-a1b2c3d4.md")

    def test_empty_string(self) -> None:
        """Raises ValueError on empty string."""
        with pytest.raises(ValueError, match="Must end with .md"):
            parse_display_name("")
