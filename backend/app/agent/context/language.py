"""The answer language: named by a small model call, then one sentence at the
end of the last user message.

With long French course pages in the prompt, the model often answered an
English question in French (measured 2026-11-16: Search 24/35 and Direct 29/35
English answers to 7 English questions, five runs each). "Answer in the
student's language" in the system message is too far from the answer. One
explicit sentence at the end of the last user message fixes the language.

The language is not guessed from word lists any more (2026-11-18): lists of
French and English words sent German questions to English or French, and
other languages to French. A separate small call, which sees only the
student's messages and the teacher's rules and no course text, names the
language; the sentence then names it. Measured 2026-11-18 (2 TPs of 19k and
57k characters, Search, Direct, Research, 140 answers in en/fr/de/es/zh):
word lists 2/28 German and 0/14 Spanish; one generic sentence without a
language name 42/56 English (short English questions drifted to French);
the small call 140/140.

The sentence is not put in the system message, so the cached prefix does not
change with the language.
"""

from __future__ import annotations

import re

ASK_PROMPT = (
    "You choose the language of a tutor's answer to a student. Reply with the English name "
    "of one language and nothing else (for example: French, English, German, Spanish, "
    "Chinese), or Unknown.\n"
    "- If the teacher's rules say in which language to answer, reply with that language.\n"
    "- Otherwise reply with the language of the student's last message, even when the "
    "earlier messages are in another language.\n"
    "- Only when the last message does not show its language (just code, a number, a file "
    "name or one technical word such as \"malloc?\"), reply with the language of the "
    "student's earlier messages, or Unknown when there are none.")
# What the small call sees of the conversation.
EARLIER_MESSAGES = 3
EARLIER_CHARS = 300
LAST_HEAD, LAST_TAIL = 1200, 300   # a long message is mostly pasted code
RULES_CHARS = 4000
ASK_TOKENS = 8
ASK_SECONDS = 15
NAME = re.compile(r"[A-Za-z][A-Za-z ()'-]{1,40}")

# When the call says Unknown: the courses are French.
DEFAULT_LANGUAGE = 'French'
# When the call fails: no language name.
GENERIC_SENTENCE = ("Write the answer in the language the student writes in, unless the "
                    "teacher's rules set the answer language.")


def _clip(text: str, head: int, tail: int = 0) -> str:
    text = (text or '').strip()
    if len(text) <= head + tail:
        return text
    return text[:head] + ' […] ' + (text[-tail:] if tail else '')


def ask_messages(question: str, history: list[dict] | None, teaching_rules: str) -> list[dict]:
    """The small call: the teacher's rules, the student's last few messages
    and the question; no course text and no answers."""
    earlier = [m.get('content') or '' for m in history or [] if m.get('role') == 'user']
    earlier = earlier[-EARLIER_MESSAGES:]
    lines = ["Teacher's rules:", _clip(teaching_rules, RULES_CHARS) or '(none)', '',
             "Student's earlier messages:"]
    lines += [f'- {_clip(m, EARLIER_CHARS)}' for m in earlier] or ['(none)']
    lines += ['', "Student's last message:", _clip(question, LAST_HEAD, LAST_TAIL)]
    return [{'role': 'system', 'content': ASK_PROMPT}, {'role': 'user', 'content': '\n'.join(lines)}]


def parse_language(reply: str) -> str | None:
    """The language name in the reply; DEFAULT_LANGUAGE for Unknown; None when
    the reply is not a language name."""
    first = (reply or '').strip().splitlines()[0] if (reply or '').strip() else ''
    name = first.strip(' .*_`"\'')
    if not NAME.fullmatch(name):
        return None
    if name.lower() == 'unknown':
        return DEFAULT_LANGUAGE
    return name[0].upper() + name[1:]


async def ask_language(model, question: str, history: list[dict] | None,
                       teaching_rules: str = '') -> str | None:
    """The answer language named by a small model call, or None when the reply
    is not a language name. model: a ResearchModel-like call; errors are
    raised to the caller."""
    reply = await model(ask_messages(question, history, teaching_rules), ASK_TOKENS, ASK_SECONDS,
                        purpose='answer_language')
    return parse_language(reply.content)


def language_sentence(language: str | None) -> str:
    """The sentence that ends the last user message."""
    return f'Write the answer in {language}.' if language else GENERIC_SENTENCE


def with_language(text: str, sentence: str) -> str:
    """text, then the language sentence when there is one."""
    return text + '\n\n' + sentence if sentence else text
