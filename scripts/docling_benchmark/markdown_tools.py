"""Markdown clean-up shared by the tools that return one Markdown page (MinerU, PaddleOCR)."""
import re

IMAGE_LINK = re.compile(r'!\[[^\]]*\]\([^)]*\)')
IMAGE_TAG = re.compile(r'<img\b[^>]*>', re.IGNORECASE)
EMPTY_DIV = re.compile(r'<div\b[^>]*>\s*</div>', re.IGNORECASE)


def strip_images(markdown):
    """Remove references to extracted image files: a file name is not searchable content.
    Text the tool wrote about an image (captions, descriptions) is kept. Other lines are unchanged;
    a line left empty by the removal is dropped together with the blank line that followed it."""
    out, dropped = [], False
    for line in markdown.split('\n'):
        cleaned = EMPTY_DIV.sub('', IMAGE_TAG.sub('', IMAGE_LINK.sub('', line)))
        if cleaned != line and not cleaned.strip():
            dropped = True
            continue
        if dropped and not line.strip() and (not out or not out[-1].strip()):
            continue
        dropped = False
        out.append(cleaned)
    return '\n'.join(out).strip('\n')
