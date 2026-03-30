"""Tests for src/voice/directive_parser.py — parsing <actions> blocks."""

import pytest

from src.voice.directive_parser import (
    ParseResult,
    VoiceDirective,
    ImageDirective,
    FileDirective,
    VideoDirective,
    parse_directives,
    strip_actions_block,
)


class TestParseDirectives:

    def test_no_actions_block_returns_text_unchanged(self):
        result = parse_directives("Hello, no actions here.")
        assert result.clean_text == "Hello, no actions here."
        assert result.directives == []

    def test_empty_string(self):
        result = parse_directives("")
        assert result.clean_text == ""
        assert result.directives == []

    def test_voice_directive(self):
        text = 'Some text <actions><voice>Hello world</voice></actions>'
        result = parse_directives(text)
        assert result.clean_text == "Some text"
        assert len(result.directives) == 1
        d = result.directives[0]
        assert isinstance(d, VoiceDirective)
        assert d.text == "Hello world"

    def test_image_directive(self):
        text = '<actions><image url="https://example.com/pic.png" alt="A picture">Caption here</image></actions>'
        result = parse_directives(text)
        assert result.clean_text == ""
        assert len(result.directives) == 1
        d = result.directives[0]
        assert isinstance(d, ImageDirective)
        assert d.url == "https://example.com/pic.png"
        assert d.alt == "A picture"
        assert d.caption == "Caption here"

    def test_image_directive_no_alt(self):
        text = '<actions><image url="https://example.com/pic.png">Caption</image></actions>'
        result = parse_directives(text)
        assert len(result.directives) == 1
        d = result.directives[0]
        assert isinstance(d, ImageDirective)
        assert d.alt == ""
        assert d.caption == "Caption"

    def test_file_directive(self):
        text = '<actions><file url="https://example.com/doc.pdf" alt="report">Download here</file></actions>'
        result = parse_directives(text)
        assert len(result.directives) == 1
        d = result.directives[0]
        assert isinstance(d, FileDirective)
        assert d.url == "https://example.com/doc.pdf"
        assert d.filename == "report"
        assert d.caption == "Download here"

    def test_video_directive(self):
        text = '<actions><video url="https://example.com/vid.mp4" alt="Demo">Watch this</video></actions>'
        result = parse_directives(text)
        assert len(result.directives) == 1
        d = result.directives[0]
        assert isinstance(d, VideoDirective)
        assert d.url == "https://example.com/vid.mp4"
        assert d.alt == "Demo"
        assert d.caption == "Watch this"

    def test_multiple_directives_in_one_block(self):
        text = (
            'Message <actions>'
            '<voice>Say this</voice>'
            '<image url="https://img.test/a.png" alt="pic">cap</image>'
            '<file url="https://files.test/b.pdf" alt="doc">dl</file>'
            '</actions>'
        )
        result = parse_directives(text)
        assert result.clean_text == "Message"
        assert len(result.directives) == 3
        assert isinstance(result.directives[0], VoiceDirective)
        assert isinstance(result.directives[1], ImageDirective)
        assert isinstance(result.directives[2], FileDirective)

    def test_multiple_actions_blocks(self):
        text = (
            'Start <actions><voice>One</voice></actions> '
            'middle <actions><voice>Two</voice></actions> end'
        )
        result = parse_directives(text)
        assert "Start" in result.clean_text
        assert "middle" in result.clean_text
        assert "end" in result.clean_text
        assert len(result.directives) == 2
        assert result.directives[0].text == "One"
        assert result.directives[1].text == "Two"

    def test_empty_voice_directive_skipped(self):
        text = '<actions><voice>  </voice></actions>'
        result = parse_directives(text)
        assert result.directives == []

    def test_empty_url_image_skipped(self):
        text = '<actions><image url="" alt="nope">cap</image></actions>'
        result = parse_directives(text)
        assert result.directives == []

    def test_multiline_voice_text(self):
        text = '<actions><voice>\nLine one\nLine two\n</voice></actions>'
        result = parse_directives(text)
        assert len(result.directives) == 1
        assert "Line one" in result.directives[0].text
        assert "Line two" in result.directives[0].text


class TestStripActionsBlock:

    def test_strips_single_block(self):
        assert strip_actions_block("Hello <actions>stuff</actions> world") == "Hello  world"

    def test_strips_multiple_blocks(self):
        result = strip_actions_block("A <actions>x</actions> B <actions>y</actions> C")
        assert "A" in result
        assert "B" in result
        assert "C" in result
        assert "<actions>" not in result

    def test_no_block_returns_unchanged(self):
        assert strip_actions_block("plain text") == "plain text"

    def test_empty_string(self):
        assert strip_actions_block("") == ""
