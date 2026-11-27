> Part of the Edu-LLM documentation. Back to [README](../README.md).

# Known issues

This file lists the problems we know of and have not fixed yet: what the user sees, why it happens, and where in the code. Found on 2026-11-26 from a teacher's feedback on the release stack (an 81-page Beamer PDF of a deep learning course) and from checks on the release database and logs.

## 1. "Some pages were not read" (fixed in 6233dad, on release since 2026-11-27)

- **What the teacher saw.** The document was marked "some pages not read".
- **Why.** MinerU (the PDF reader) returned a block type that our reader did not know: `page_footnote`, `aside_text`, `ref_text` or `phonetic`. An unknown type marked the whole page as not fully read, even when its text was read.
- **Fix.** `_content()` in `backend/worker/pdf_reader.py` keeps these four types as text. On dev, the 81-page course now has every page read, with its footnotes.
- **How much.** On release, 2 of 21 documents.

## 2. "Page non ouverte" in the answer's steps (fixed in 518d699, on release since 2026-11-27)

- **What the teacher saw.** A step "Could not open the page" while the agent works.
- **Why.** The model called `read` with a `cursor` and also a `document`, and the argument check refused that ("a cursor needs no document"). The model also sometimes gave a cursor from `open` to `read` ("unknown read cursor"). It tried again and read the page, so nothing was lost; only the step list showed a failure.
- **Fix.** A cursor of the run already names its tool, document and place: `follow_cursor()` in `backend/app/agent/tools/course.py` continues it with the cursor alone, by the tool that made it. A cursor with another anchor (a page) is still refused.

## 3. Some formulas are shown as red LaTeX source (fixed in 2baf8f1, on release since 2026-11-27)

- **What the teacher saw.** Two numbered formulas (`\tag{11}`, `\tag{12}`) were not drawn.
- **Why.** The model writes `$$ … $$` on one line. `remark-math` 6 reads that as a formula inside a sentence, and `\tag` only works in a formula on its own lines, so KaTeX showed the source in red.
- **Fix.** `frontend/src/lib/displayMath.js` splits a line that holds only `$$ … $$` onto its own lines before rendering (`MessageContent.jsx`). The answer from the feedback: 2 red formulas before, 0 after.

## 4. A section "what the course does not give", and a new search for a question about the answer itself (being worked on)

- **What the teacher saw.** The answer ended with what the course does not contain (here a continuous convolution, from the model's general knowledge). When asked "how do you know?", the model searched the course again instead of explaining its own sentence.
- **Why.** Our prompts asked for it: the system prompt said to "state the specific gap"; `FINISH_TURN` asked to "say which parts the evidence does not cover"; `NO_LEAD_TURN` asked to "say which part the course does not cover".
- **First try (a1dc672, reverted in 19ce645).** Prompts that ask for one short sentence where a part is missing, and an answer from the conversation for a question about an earlier answer. On dev (the teacher's question on the 81-page course, 6 runs each): a bold "not in the course" section 5 → 0; continuous convolution mentioned 5 → 2; "how do you know?" searched again 4 → 0. Benchmark v4 (one run each): pages 93 → 96/106, precision 72 → 75%.
- **Why it is not enough.** The teacher's PI asks for no such sentence at all: the course's formula is the definition, and nothing it lacks needs saying. Later prompt versions still open 5 or 6 answers of 6 with "the course gives no formal definition, it defines it by its formula", because the model first looks for a "Definition" box. With the PI's own follow-up on the release answer, the model still searches the course (9 to 31 tool calls) before saying the formula was its general knowledge.
- **Next.** A check of the finished answer that removes such sentences, and a small model call that tells a question about the previous answer from a question about the course (then no tools).

## 5. Tables are shown as HTML code (fixed in 53f75ea and 21c63b4, on release since 2026-11-27)

- **What the teacher saw.** In the document view, a table block showed `<table><tr><td>…`, with `&#x27;` instead of quotes. Formula blocks showed their LaTeX. Search cut a table every 400 to 600 bytes, often inside a cell, without its header: 89 of the 153 parts of release's 64 tables.
- **Fix: the display (53f75ea).** `frontend/src/components/BlockContent.jsx` draws table and formula blocks: rehype-raw parses the HTML, rehype-sanitize keeps what GitHub keeps (table elements, rowspan, colspan), `lib/cellMath.js` marks the `$…$` in cells for rehype-katex. On release: 64 tables (19 with merged cells) and 355 formulas drawn, none with an error. `<img>` stays allowed by GitHub's rules; it could be removed too.
- **Fix: search (21c63b4).** `backend/worker/table_chunks.py` cuts a table only between rows; each part is still an exact slice of the block. Search reads it as plain text (cells joined by " | ", entities decoded, merged cells repeated, the header row in front), stored in `doc_chunks.context`: the embedding, the tsv and the reranker read it, and the model sees it in search leads.
- **Existing documents.** All release PDFs were read again on 2026-11-27, so their search uses the new parts.

## 6. Reading a PDF is slow (improved in 6233dad and f7b9c4d, on release since 2026-11-27)

- **What the teacher saw.** The 81-page PDF took about 25 minutes before it could be published.
- **Where the time went.** MinerU reading the pages: about 20 minutes (15 s per page on average, 9 to 37 s). Picture descriptions: 90 s. Indexing: about 10 s. Waiting in the queue: 2 s.
- **Why.** Each page started a new Python process for MinerU, which loaded MinerU and its models again for every page.
- **Fix.** One MinerU process reads the whole document, 16 pages per MinerU call (`backend/worker/pdf_mineru.py`); a failed group of pages is read again page by page. MinerU's own picture analysis is off (nothing used it). On dev the same PDF took 7 min 20 s from upload to draft: reading 5.5 min, picture descriptions 1.5 min.
- **Then (f7b9c4d).** MinerU's three small models (layout, which finds the formulas inside sentences; formula recognition; OCR lines) took most of the reading time on the CPU. They now run on the GPU in the container `edullm-mineru-gpu`; the worker reads on its CPU when that service fails. The GPU service reads up to 32 pages per call, spread evenly, and warms up at start. On dev the same PDF read in 87 s instead of 333 s, with the same text and formulas. From upload to draft: about 3 minutes (87 s reading, about 90 s picture descriptions). The service holds about 3.5 GB when idle and up to about 6.5 GB while it reads.
- **Measured and not faster.** Skipping the layout model (it loses the formulas inside sentences: 46 of 335 text blocks of this course), more MinerU processes at once, a larger KV cache, lower image resolution, `effort='medium'`, picture descriptions at the same time as the reading.
- **Also fixed (13e9258).** A PDF text layer can contain control characters (U+0000); PostgreSQL refused them and the whole document failed. The reader now removes them.

## 7. Captions and notes of pictures and tables were not kept (fixed in 21c63b4, on release since 2026-11-27)

- **What the teacher and students saw.** A picture's caption ("118 telescopes, 210 000 TB of data per year") or a table's title ("Table: Total learnable parameters…") was in neither the document view nor search.
- **Why.** MinerU gives them as fields inside the block (`image_caption`, `image_footnote`, `table_caption`, `table_footnote`, `chart_caption`, `chart_footnote`, `code_caption`); the reader kept only the block's body.
- **Fix.** `backend/worker/pdf_reader.py` (`ATTACHED`, `_attached()`) makes each one a text block next to its block: a table's title and a code caption before it, the others after it. On dev, the 81-page course now has 114 such blocks.
- **Existing documents.** All release PDFs were read again on 2026-11-27.

## 8. Slide headers and footers repeated on every page were searched (fixed in 21c63b4, on release since 2026-11-27)

- **What students saw.** A search for a section name ("Affect measure") found the same navigation bar on many pages before the page that teaches it.
- **Why.** MinerU marks a Beamer slide's navigation bar, author, title and page counter as `header`, `footer` and `page_number`; the reader keeps them as `navigation` blocks, and every one with text was searched.
- **Fix.** `backend/worker/structured_ingest.py` (`repeated_navigation()`): a navigation text found on 3 pages or more is left out of search when the document is read (`index_state` excluded, `structure.exclusion.by` None: not a teacher's change, so a new reading decides again). One found on fewer pages stays searchable, since MinerU may mark content as a header. The teacher sees why and can put a block back.
- **Result on dev.** The 81-page course: 482 navigation blocks left out, 96 still searched; chunks for "Affect measure": 3 instead of 14; 855 chunks instead of 1248.
- **Existing documents.** All release PDFs were read again on 2026-11-27.

## 9. Files that students attach in the chat (limits of the current design; long files improved in f55e8ed)

This is how the feature works today (the Open WebUI way: no worker, no MinerU, no index), not a bug. Code: `backend/app/services/chat_file_service.py` and `backend/app/agent/context/attachments.py`.

- An image goes to the model as an image.
- A PDF keeps only the text of its text layer (PyMuPDF): formulas, table structure and pictures are lost. A scanned PDF has no text; the model asks for a screenshot.
- About 40 text and code extensions are read as text.
- Word, PowerPoint and Excel files (`.docx`, `.pptx`, `.xlsx`) are refused.
- At most 10 MB per file. The attached texts of a question share 30,000 characters in the question (`ATTACH_TEXT_CHARS`, 20,000 per file before 2026-11-27). The model reads the rest of a longer file with `read_file` and finds a passage with `grep_file` (`backend/app/agent/tools/files.py`), up to the 200,000 characters stored at upload (`TEXT_MAX_CHARS`); there is no semantic search in it (added 2026-11-27).
