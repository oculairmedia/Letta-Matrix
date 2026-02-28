import re
from dataclasses import dataclass, field
from typing import List, Union


@dataclass
class VoiceDirective:
    type: str = "voice"
    text: str = ""


@dataclass
class ImageDirective:
    type: str = "image"
    url: str = ""
    alt: str = ""
    caption: str = ""


@dataclass
class FileDirective:
    type: str = "file"
    url: str = ""
    filename: str = ""
    caption: str = ""


@dataclass
class VideoDirective:
    type: str = "video"
    url: str = ""
    alt: str = ""
    caption: str = ""


@dataclass
class ParseResult:
    clean_text: str
    directives: List[Union[VoiceDirective, ImageDirective, FileDirective, VideoDirective]]


_ACTIONS_BLOCK_REGEX = re.compile(r"<actions>([\s\S]*?)</actions>")
_VOICE_DIRECTIVE_REGEX = re.compile(r"<voice>([\s\S]*?)</voice>")
_IMAGE_DIRECTIVE_REGEX = re.compile(
    r'<image\s+url="([^"]+)"(?:\s+alt="([^"]*)")?\s*>([\s\S]*?)</image>'
)
_FILE_DIRECTIVE_REGEX = re.compile(
    r'<file\s+url="([^"]+)"(?:\s+alt="([^"]*)")?\s*>([\s\S]*?)</file>'
)
_VIDEO_DIRECTIVE_REGEX = re.compile(
    r'<video\s+url="([^"]+)"(?:\s+alt="([^"]*)")?\s*>([\s\S]*?)</video>'
)


def parse_directives(text: str) -> ParseResult:
    match = _ACTIONS_BLOCK_REGEX.search(text)
    if not match:
        return ParseResult(clean_text=text, directives=[])

    actions_content = match.group(1)
    # Remove the entire <actions>...</actions> block from the text
    clean_text = (text[:match.start()] + text[match.end():]).strip()
    directives: List[Union[VoiceDirective, ImageDirective, FileDirective, VideoDirective]] = []

    for voice_match in _VOICE_DIRECTIVE_REGEX.finditer(actions_content):
        directive_text = voice_match.group(1).strip()
        if directive_text:
            directives.append(VoiceDirective(text=directive_text))

    for image_match in _IMAGE_DIRECTIVE_REGEX.finditer(actions_content):
        url = image_match.group(1).strip()
        alt = (image_match.group(2) or "").strip()
        caption = (image_match.group(3) or "").strip()
        if url:
            directives.append(ImageDirective(url=url, alt=alt, caption=caption))

    for file_match in _FILE_DIRECTIVE_REGEX.finditer(actions_content):
        url = file_match.group(1).strip()
        filename = (file_match.group(2) or "").strip()
        caption = (file_match.group(3) or "").strip()
        if url:
            directives.append(FileDirective(url=url, filename=filename, caption=caption))

    for video_match in _VIDEO_DIRECTIVE_REGEX.finditer(actions_content):
        url = video_match.group(1).strip()
        alt = (video_match.group(2) or "").strip()
        caption = (video_match.group(3) or "").strip()
        if url:
            directives.append(VideoDirective(url=url, alt=alt, caption=caption))

    return ParseResult(clean_text=clean_text, directives=directives)


def strip_actions_block(text: str) -> str:
    return _ACTIONS_BLOCK_REGEX.sub("", text, count=1).strip()
