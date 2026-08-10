"""Boundary tests for the frozen AF-5 deterministic parser gates (AC#3).
Pure-function tests, no parser SDK, no file I/O, no Docker.
"""

from __future__ import annotations

from src.domain.parse_gates import (
    Block,
    GateCode,
    evaluate_readable_content_gate,
    is_coherent_block,
    remove_repeated_header_footer,
    replacement_char_ratio,
)


def _text_of_length(n: int) -> str:
    """n normalized non-whitespace characters, with whitespace interspersed
    (whitespace must never count toward the threshold)."""
    return " ".join("x" * n)


def test_499_chars_fails_text_gate():
    text = "x" * 499
    codes = evaluate_readable_content_gate(text, blocks=[])
    assert GateCode.TEXT_BELOW_500 in codes


def test_500_chars_passes_text_gate():
    text = "x" * 500
    codes = evaluate_readable_content_gate(text, blocks=[])
    assert GateCode.TEXT_BELOW_500 not in codes


def test_whitespace_does_not_count_toward_text_gate():
    # 500 non-whitespace chars plus lots of whitespace padding still passes;
    # 499 non-whitespace chars plus whitespace padding still fails.
    assert GateCode.TEXT_BELOW_500 not in evaluate_readable_content_gate(_text_of_length(500), [])
    assert GateCode.TEXT_BELOW_500 in evaluate_readable_content_gate(_text_of_length(499), [])


def test_one_coherent_block_fails_block_gate():
    blocks = [Block(text="x" * 100, content_class="employment")]
    codes = evaluate_readable_content_gate("x" * 600, blocks)
    assert GateCode.COHERENT_BLOCKS_BELOW_2 in codes


def test_two_coherent_blocks_passes_block_gate():
    blocks = [
        Block(text="x" * 100, content_class="employment"),
        Block(text="x" * 100, content_class="education"),
    ]
    codes = evaluate_readable_content_gate("x" * 600, blocks)
    assert GateCode.COHERENT_BLOCKS_BELOW_2 not in codes


def test_block_below_100_chars_is_not_coherent():
    block = Block(text="x" * 99, content_class="employment")
    assert not is_coherent_block(block)


def test_block_at_100_chars_is_coherent():
    block = Block(text="x" * 100, content_class="employment")
    assert is_coherent_block(block)


def test_block_with_disallowed_class_is_not_coherent():
    block = Block(text="x" * 200, content_class="hobbies")
    assert not is_coherent_block(block)


def test_replacement_ratio_just_under_20_percent_is_coherent():
    # 19 replacement chars in 100 -> 19% < 20%
    text = "�" * 19 + "x" * 81
    block = Block(text=text, content_class="skills")
    assert replacement_char_ratio(text) < 0.20
    assert is_coherent_block(block)


def test_replacement_ratio_at_20_percent_is_not_coherent():
    # 20 replacement chars in 100 -> exactly 20%, frozen rule is "<20%"
    text = "�" * 20 + "x" * 80
    block = Block(text=text, content_class="skills")
    assert replacement_char_ratio(text) == 0.20
    assert not is_coherent_block(block)


def test_control_characters_count_toward_replacement_ratio():
    text = "\x01" * 25 + "x" * 75
    assert replacement_char_ratio(text) == 0.25


def test_c1_control_range_counts_toward_replacement_ratio():
    # 0x7F-0x9F (DEL plus the C1 control block) is a distinct range from the
    # C0 controls (<0x20) exercised above — assert it independently.
    text = "\x85" * 25 + "x" * 75  # U+0085 NEXT LINE, a C1 control character
    assert replacement_char_ratio(text) == 0.25


def test_tab_newline_carriage_return_do_not_count_as_control():
    text = "\t\n\r" + "x" * 97
    assert replacement_char_ratio(text) == 0.0


def test_remove_repeated_header_footer_strips_header_seen_on_3_plus_pages():
    pages = [
        ["Confidential Resume", "Body line A", "Footer 1"],
        ["Confidential Resume", "Body line B", "Footer 1"],
        ["Confidential Resume", "Body line C", "Footer 1"],
    ]
    cleaned = remove_repeated_header_footer(pages)
    assert cleaned == [["Body line A"], ["Body line B"], ["Body line C"]]


def test_remove_repeated_header_footer_leaves_unique_first_last_lines_alone():
    pages = [["Unique 1", "Body A"], ["Unique 2", "Body B"]]
    cleaned = remove_repeated_header_footer(pages)
    assert cleaned == pages


def test_remove_repeated_header_footer_noop_below_3_pages():
    pages = [["Header", "Body"], ["Header", "Body"]]
    assert remove_repeated_header_footer(pages) == pages
