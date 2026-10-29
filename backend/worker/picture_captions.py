"""Picture captions at ingest (documentation/PICTURE_CAPTIONS.md).

After MinerU has read a PDF, each picture block gets two or three French
sentences on what it shows, written by the main model. The caption only helps
search find the page; the agent is told it is unchecked and the picture stays
the reference.

Captions never hold a document back: when the model fails, or the time budget
for the document is used up, the remaining pictures simply have none.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import pymupdf
from sqlalchemy import select

from backend.app.models import PictureCaption
from backend.app.services.model_routing import Endpoint

PROMPT_VERSION = 'caption-fr-1'
# What is visible and what it is about, not the structure: a model is most
# often wrong on edges, node order and values, and the agent may copy them.
PROMPT = ("Voici une image extraite d'une diapositive de cours (titre de la diapositive : « {title} »). "
          "Décris-la en français en 2 ou 3 phrases courtes, pour qu'un étudiant qui se souvient seulement "
          "de son aspect puisse la retrouver : le type de figure (photo, dessin, schéma bloc, courbe, graphe, "
          "tableau…), ce qu'on y voit (objets, formes, couleurs, allure générale des courbes), les mots ou "
          "symboles visibles, et ce qu'elle illustre. Ne liste pas les liens, l'ordre des nœuds, le sens des "
          "flèches ni les valeurs lues sur une courbe. N'invente rien qui ne soit pas visible. "
          "Réponds par la description seule.")
CROP_SCALE = 2                # picture sent to the model and hashed for the cache
FINGERPRINT_SCALE = 0.25      # coarse picture that finds repeats across pages
REPEATED_PAGES = 3            # on this many pages or more: a logo or a template
MIN_AREA_SHARE = 0.01         # smaller than this share of the page: an icon
MIN_SIDE_POINTS = 40
MAX_TOKENS = 300

DescribeFn = Callable[[bytes, str], Awaitable[str]]

# Admin settings (system_configs). Captions use the VLM slot, the model that
# also serves view_image.
ENABLED_KEY = 'PICTURE_CAPTIONS'                     # 'on' turns captions on
PRIORITY_KEY = 'PICTURE_CAPTION_PRIORITY'            # empty: no priority sent
BUDGET_KEY = 'PICTURE_CAPTION_BUDGET_SECONDS'        # empty: DEFAULT_BUDGET_SECONDS
DEFAULT_BUDGET_SECONDS = 900
PRIORITY_RANGE = (-1000, 1000)
BUDGET_RANGE = (60, 7200)


def whole_number(value, low: int, high: int) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


@dataclass(frozen=True)
class CaptionSettings:
    endpoint: Endpoint
    # vLLM serves a lower value first (chat requests send none, i.e. 0). None:
    # send no priority, for a server without --scheduling-policy priority.
    priority: int | None = None
    budget_seconds: float = 900.0


def settings_from_config(configs, vlm: Endpoint) -> 'CaptionSettings | None':
    """The caption settings of one config snapshot; None when captions are off
    or no vision model is set. An invalid number falls back to the default."""
    if (configs.get(ENABLED_KEY) or '').strip().lower() != 'on' or not vlm.model or not vlm.base_url:
        return None
    return CaptionSettings(
        endpoint=vlm,
        priority=whole_number(configs.get(PRIORITY_KEY), *PRIORITY_RANGE),
        budget_seconds=whole_number(configs.get(BUDGET_KEY), *BUDGET_RANGE) or DEFAULT_BUDGET_SECONDS)


@dataclass
class Picture:
    block: object               # ParsedBlock
    title: str
    png: bytes = b''
    image_sha256: str = ''
    fingerprint: str = ''
    skip: str | None = None     # repeated, small, no_region
    pages: set = field(default_factory=set)


def _region(page, block):
    box = ((block.source_ref.legacy_ref or {}).get('mineru_bbox_per_mille') or None)
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    r = page.rect
    x0, y0, x1, y1 = (float(v) for v in box)
    rect = pymupdf.Rect(r.x0 + x0 * r.width / 1000, r.y0 + y0 * r.height / 1000,
                        r.x0 + x1 * r.width / 1000, r.y0 + y1 * r.height / 1000) & r
    return None if rect.is_empty else rect


def find_pictures(document, pdf_path: Path) -> list[Picture]:
    """Every picture block with its crop, and why it is skipped, if it is."""
    pictures = []
    with pymupdf.open(pdf_path) as pdf:
        for page in document.pages:
            heading = next((b.content for b in page.blocks if b.kind == 'heading' and b.content), '')
            pdf_page = pdf[page.source_slide_no - 1]
            for block in page.blocks:
                if block.kind != 'figure':
                    continue
                picture = Picture(block, (page.title or heading or '')[:120], pages={page.source_slide_no})
                pictures.append(picture)
                rect = _region(pdf_page, block)
                if rect is None:
                    picture.skip = 'no_region'
                    continue
                if (rect.width * rect.height < MIN_AREA_SHARE * pdf_page.rect.width * pdf_page.rect.height
                        or min(rect.width, rect.height) < MIN_SIDE_POINTS):
                    picture.skip = 'small'
                    continue
                picture.png = pdf_page.get_pixmap(matrix=pymupdf.Matrix(CROP_SCALE, CROP_SCALE),
                                                  clip=rect, alpha=False).tobytes('png')
                picture.image_sha256 = hashlib.sha256(picture.png).hexdigest()
                coarse = pdf_page.get_pixmap(matrix=pymupdf.Matrix(FINGERPRINT_SCALE, FINGERPRINT_SCALE),
                                             clip=rect, alpha=False)
                picture.fingerprint = hashlib.sha256(coarse.samples).hexdigest()
    seen: dict[str, set] = {}
    for picture in pictures:
        if picture.fingerprint:
            seen.setdefault(picture.fingerprint, set()).update(picture.pages)
    for picture in pictures:
        if picture.fingerprint and len(seen[picture.fingerprint]) >= REPEATED_PAGES:
            picture.skip = 'repeated'
    return pictures


def make_describe(settings: CaptionSettings) -> DescribeFn:
    """One caption from the model; raises on any failure or unfinished reply."""
    from openai import AsyncOpenAI
    endpoint = settings.endpoint
    client = AsyncOpenAI(base_url=endpoint.base_url, api_key=endpoint.api_key or 'local',
                         timeout=settings.budget_seconds, max_retries=0)
    extra = {'chat_template_kwargs': {'enable_thinking': False}}
    if settings.priority is not None:
        extra['priority'] = settings.priority

    async def describe(png: bytes, title: str) -> str:
        response = await client.chat.completions.create(
            model=endpoint.model, temperature=0, max_tokens=MAX_TOKENS, extra_body=extra,
            messages=[{'role': 'user', 'content': [
                {'type': 'text', 'text': PROMPT.format(title=title or 'sans titre')},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}}]}])
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or '').strip() if choice else ''
        if not choice or choice.finish_reason != 'stop' or not text:
            raise ValueError('caption_unfinished')
        return text
    describe.aclose = client.close   # one client per document: close() when done
    return describe


async def close(describe: DescribeFn) -> None:
    """Close the model client of a describe function from make_describe."""
    aclose = getattr(describe, 'aclose', None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:  # noqa: BLE001 - the captions are written; a close error changes nothing
            pass


async def cached_captions(db, model: str, hashes) -> dict[str, str]:
    hashes = sorted(set(hashes))
    if not hashes:
        return {}
    rows = await db.execute(select(PictureCaption.image_sha256, PictureCaption.caption).where(
        PictureCaption.model == model, PictureCaption.prompt_version == PROMPT_VERSION,
        PictureCaption.image_sha256.in_(hashes)))
    return dict(rows.all())


async def store_captions(db, model: str, written: dict[str, str]) -> None:
    from sqlalchemy.dialects.postgresql import insert
    if not written:
        return
    rows = [{'image_sha256': h, 'model': model, 'prompt_version': PROMPT_VERSION, 'caption': c}
            for h, c in written.items()]
    if db.bind.dialect.name == 'postgresql':
        await db.execute(insert(PictureCaption).values(rows).on_conflict_do_nothing())
    else:
        known = await cached_captions(db, model, written)
        db.add_all(PictureCaption(**row) for row in rows if row['image_sha256'] not in known)
    await db.commit()


async def write_captions(pictures: list[Picture], *, model: str, describe: DescribeFn,
                         cached: dict[str, str], budget_seconds: float,
                         clock=time.monotonic) -> tuple[dict, dict[str, str]]:
    """Set block.caption on the pictures. Returns the report and the new
    captions by image hash (for the cache). All requests go at once; the model
    server orders them (and puts students first by priority)."""
    started = clock()
    report = {'pictures': len(pictures), 'written': 0, 'cached': 0, 'skipped_repeated': 0,
              'skipped_small': 0, 'skipped_no_region': 0, 'failed': 0, 'not_reached': 0,
              'prompt_version': PROMPT_VERSION, 'model': model}
    todo: dict[str, list[Picture]] = {}
    for picture in pictures:
        if picture.skip:
            report['skipped_' + picture.skip] += 1
        elif picture.image_sha256 in cached:
            _set(picture, cached[picture.image_sha256], model, cached=True)
            report['cached'] += 1
        else:
            todo.setdefault(picture.image_sha256, []).append(picture)
    written: dict[str, str] = {}
    tasks = {asyncio.create_task(describe(group[0].png, group[0].title)): digest
             for digest, group in todo.items()}
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=budget_seconds)
        for task in pending:
            task.cancel()
            report['not_reached'] += len(todo[tasks[task]])
        for task in done:
            digest = tasks[task]
            if task.exception() is not None:
                report['failed'] += len(todo[digest])
                report.setdefault('error', type(task.exception()).__name__)
                continue
            written[digest] = task.result()
            for picture in todo[digest]:
                _set(picture, written[digest], model, cached=False)
                report['written'] += 1
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    report['seconds'] = round(clock() - started, 1)
    return report, written


def _set(picture: Picture, caption: str, model: str, *, cached: bool) -> None:
    picture.block.caption = caption
    picture.block.caption_source = {'model': model, 'prompt_version': PROMPT_VERSION,
                                    'image_sha256': picture.image_sha256, 'cached': cached}
