import re
from dataclasses import dataclass
from typing import List


@dataclass
class VoiceDirective:
    type: str = "voice"
    text: str = ""


@dataclass
class ParseResult:
    clean_text: str
    directives: List[VoiceDirective]


_ACTIONS_BLOCK_REGEX = re.compile(r"^\s*<actions>([\s\S]*?)</actions>")
_VOICE_DIRECTIVE_REGEX = re.compile(r"<voice>([\s\S]*?)</voice>")


def parse_directives(text: str) -> ParseResult:
    match = _ACTIONS_BLOCK_REGEX.match(text)
    if not match:
        return ParseResult(clean_text=text, directives=[])

    actions_content = match.group(1)
    clean_text = text[match.end():].strip()
    directives: List[VoiceDirective] = []

    for voice_match in _VOICE_DIRECTIVE_REGEX.finditer(actions_content):
        directive_text = voice_match.group(1).strip()
        if directive_text:
            directives.append(VoiceDirective(text=directive_text))

    return ParseResult(clean_text=clean_text, directives=directives)


def strip_actions_block(text: str) -> str:
    return _ACTIONS_BLOCK_REGEX.sub("", text, count=1).strip()
