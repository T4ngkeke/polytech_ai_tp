> Part of the Edu-LLM documentation. Back to [README](../README.md).

# Picture captions for search (design)

Status: design, agreed on 2026-10-28, not built. This file says why the PDF
worker should write a short caption for each picture, how it fits into the
current pipeline, and how to test it before it reaches the teachers.

## Why

A student often remembers a slide by its picture, not by its words: "the
drawing with a small boat, a lighthouse and rocks", "the curve that rises in
an S up to 2". Today search only sees the text of the PDF, so it does not find
these pages.

Tests on corpus v3 (14 courses, 507 pages), 2026-10-28:

| Test | No captions (today) | Captions by the main model |
| --- | ---: | ---: |
| 15 questions that describe only how a picture looks: page found / first | 4 / 1 | 13 / 9 |
| Benchmark v4: facets, citation precision | 93/106, 68% | 96/106, 75% |
| Benchmark v3: facets, citation precision | 127/130, 71% | 125/130, 76% |
| Benchmark v3: blind content score, factual errors | 268/272, 2 | 268/272, 5 |

Of the five factual errors with captions, one most likely came from a
caption: the agent copied the order of nodes from a caption without looking
at the picture. The others had nothing to do with captions.

What did not work:

- MinerU's own picture text. It describes 135 pictures of the corpus; the
  structure (edges, arrows, values) is wrong in 70 of 117 diagrams, and many
  texts are empty ("Node 1 --> Node 2") or OCR noise from logos. In search it
  changed nothing on v3, v4 or the picture questions.
- Leaving out decorative pictures did not change the picture questions;
  pictures repeated on many pages are left out anyway to save time.

## What the student and the agent get

- Each picture block can have a caption: two or three French sentences on
  what the picture shows (kind of figure, objects, visible words and symbols,
  the shape of curves, what it illustrates).
- The caption is stored as the block's description with the origin "model"
  and marked unchecked. Search indexes it in its own chunks, apart from the
  text (this part was built and tested on 2026-10-28, not committed).
- The agent sees "(unchecked picture description by model)" in search results
  and "unchecked; view the image before relying on it" when it reads the
  page. The citation still points to the page; the picture is the reference.
- A teacher sees the caption in the document view and can edit it. An edit
  makes it the teacher's description and removes the unchecked mark.

### How the caption is written

The prompt asks for what is visible and what the picture is about, and not
for its structure:

- yes: kind of figure, objects, colours, words and symbols in the picture,
  general shape of curves ("rises then levels off", "oscillates strongly");
- no: lists of edges, node order, arrow directions, exact values read from
  a curve.

Reason: structure is where a model is most often wrong, and it is what the
agent copied in the one error above. A student does not search by edge list.
The agent gets structure from view_image when it needs it.

## Pipeline

The caption step runs inside the PDF parse job, after MinerU and before the
index. It needs MinerU's picture boxes, so it cannot run earlier.

```
MinerU reads every page → captions for the pictures → index → publish
                             │
                             └ model down, error, or time budget used up:
                               skip the remaining pictures, go on
```

- If the model server is down or slow, the document is published as today,
  without captions. Captions never block a publication.
- The revision report records how many pictures got a caption, came from the
  cache, were skipped as repeated or small, or were left without one (and
  why).
- A later reparse of the document (teacher "Read again", or the backfill
  below) tries again for the pictures without a caption.

### Which pictures

- Every MinerU picture block (`image` and `chart`), also natural images: a
  drawing like the cyclist with a sack is filed as a natural image by
  MinerU but is part of the lesson.
- Left out: a picture that appears on 3 or more pages of the same document
  (logos, title photos, template decoration), found by the hash of its pixels;
  and very small pictures (icons). The size limit is to be set from the
  corpus.

### Model and scheduling

- The main model (Flash-Next, served as `flash-next`), the same one that answers
  the students and runs view_image. No second model: memory on the Spark is
  short, and a small vision model would need its own tests and upkeep.
- The worker sends all caption requests of a document at once and lets the
  model server schedule them. Each request has a low priority, so a
  student's request always goes first. The worker does not count paths or
  watch the load: with a bigger server, captions get faster on their own.
- This needs vLLM's priority scheduling (`--scheduling-policy priority`) on
  the model server, which means a restart. vLLM serves a lower `priority`
  value first; caption requests send a higher number than chat requests (the
  default, 0). Captions use priority 10 (admin key
  `PICTURE_CAPTION_PRIORITY`).
- Measured on 2026-10-29 (vLLM build of the salient recipe, with MTP and our
  prefix cache options): the server starts and runs at the same speed as
  without the setting. 108 captions sent at once, a student request of about
  6,000 tokens: without priority the student waits 120 s for the first token;
  with priority 10, 4.1 s (median, 2.8 s with no load) and the answer takes
  18 s instead of 8.5 s. A student request does not stop running captions:
  it takes the next free place, then shares the GPU with them. The 108
  captions took 185 s instead of 137 s.
- Time budget: one limit for the whole document (15 minutes to start with),
  not per picture. A caption request may wait behind students in the queue;
  a per-picture limit would skip it for no reason.

### Cache

Teachers upload new versions of the same course; most pictures do not
change. A caption is stored with the hash of the picture's pixels (cropped
at a fixed scale), the model name and the prompt version. A picture already
captioned with the same model and prompt is not sent again. A new prompt
version or model writes new captions.

Storage: a small table `picture_captions (image_sha256, model, prompt_version,
caption, created_at)`, shared by all documents (a caption describes pixels,
not a course). Needs a migration.

## Existing documents

Documents read before this change have no captions. Proposal: an admin script
asks a reparse of each structured PDF document, one at a time, at night. This
is the existing path: it reads the PDF again (about 2 s per page), writes
captions, indexes and publishes a new revision; teacher edits are carried
forward as for any reparse. To decide: run it on all documents, or only on
demand.

## Teacher interface

- Upload page: one sentence, e.g. "Pour un cours avec beaucoup d'images, les
  descriptions d'images sont générées après la lecture ; les réponses aux
  étudiants peuvent être un peu plus lentes pendant ce temps."
- Document view: the caption is shown under the picture as a model
  description (already the case for descriptions).

## Code that exists (uncommitted, 2026-10-28)

Built and tested with MinerU's text as the description source; kept and reused:

- `ParsedBlock.figure_type` (MinerU sub_type), kept in the block structure.
- Description chunks in the index (`source_ref.indexed_field =
  "content_description"`), accepted by the publish check and the search tool.
- The unchecked mark (`description_from_reader` in the block structure,
  `description_checked()`), dropped by any teacher change.
- Agent text for unchecked descriptions in search, read and Search mode.

To change: the description source becomes the caption step above; MinerU's
picture text is no longer indexed (it stays in the block structure as raw
data). The flag name can become `caption_unchecked`.

## Tests before release

1. Unit tests: caption step (skip rules, cache hit, budget, model error),
   index and publish with captions, report counts.
2. Same time on dev, corpus v3 with captions written by the new step:
   benchmark v3 and v4, the 15 picture questions, blind content review of v3.
   Check how often the agent cites a picture without viewing it.
3. Model server with priority scheduling, at a time nobody uses it: start,
   speed of the usual bench (`bench2.py`), and student latency while a
   100-page document is captioned (web bench at 1, 2 and 4 students).
4. Release only after these, with the teachers told.

## Open questions

- Size limit for "very small" pictures.
- Backfill: all documents at night, or on demand.
