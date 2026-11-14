"""Shared lossless HTML-to-text conversion; no source-specific imports."""

import re
from html.parser import HTMLParser


class DescriptionParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3"}:
            self.parts.append("\n" + ("- " if tag == "li" else ""))

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li", "ul", "ol"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def description_text(html: str) -> str:
    parser = DescriptionParser()
    parser.feed(html)
    parser.close()
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)
