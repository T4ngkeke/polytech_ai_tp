"""The answer language: French or English, decided from the student's words.

With long French course pages in the prompt, the model often answered an
English question in French (measured 2026-11-16: Search 24/35 and Direct 29/35
English answers to 7 English questions, five runs each). "Answer in the
student's language" in the system message is too far from the answer. One
explicit sentence at the end of the last user message fixes the language.

Only French and English are told apart: the students write one or the other.
For any other language no sentence is added, and the system message's "answer
in the student's language" stays the only rule. The sentence is not put in the
system message, so the cached prefix does not change with the language.
"""

from __future__ import annotations

import re

# Words that are frequent in one language and do not exist, or are rare, in the
# other. C keywords (if, for, do, while, int, return...) are left out: students
# paste code in both languages.
ENGLISH = frozenset('''
the what how why which who where when is are was were does did can could should would
will i we you they it this that these those there here my your our their with without
from about into have has had am be been not and but please help stuck explain mean
next previous exercise understand know want need get got does doesn't don't isn't
can't i'm what's how's it's thanks thank hello hi any some
'''.split())
FRENCH = frozenset('''
le la les un une des du est sont était quoi que qui quel quelle quels quelles comment
pourquoi où quand je tu nous vous il elle ils elles ce cette ces mon ma mes ton ta tes
notre votre leur leurs avec sans dans sur sous pour par pas ne plus mais et donc car
fait faire faut peux peut veux dois suis sommes êtes ai avons avez bloqué bloquée aide
aider explique expliquer suivante suivant précédente exercice comprends comprendre
merci bonjour salut c'est qu'est-ce est-ce j'ai je suis n'est d'un d'une l'exercice
'''.split())
FRENCH_LETTERS = re.compile(r'[éèêàùçôîâûëï]')
CODE = re.compile(r'```.*?(```|$)|`[^`]*`', re.S)
# Exercise numbers such as I.1, II.3.b: the I is not the English word.
NUMBERING = re.compile(r'\b[IVXivx]+\.\d+(?:\.\w+)*')
WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
LATIN = re.compile(r'[a-zA-Z\u00C0-\u024F]')
LETTER = re.compile(r'[^\W\d_]')

SENTENCES = {
    'en': 'Write the answer in English.',
    'fr': 'Write the answer in French.',
}


def detect(text: str) -> str | None:
    """'fr', 'en', 'other' (mostly letters of another script, e.g. Chinese), or
    None when the words do not tell."""
    text = NUMBERING.sub(' ', CODE.sub(' ', text or ''))
    words = WORD.findall(text.lower().replace('’', "'"))
    # Another script: more of its letters than Latin words (a Chinese
    # character is about one word).
    other = len(LETTER.findall(text)) - len(LATIN.findall(text))
    if other > sum(bool(LATIN.search(word)) for word in words):
        return 'other'
    english = sum(word in ENGLISH for word in words)
    french = sum(word in FRENCH or bool(FRENCH_LETTERS.search(word)) for word in words)
    if english > french:
        return 'en'
    if french > english:
        return 'fr'
    return None


def answer_language(question: str, history: list[dict] | None = None) -> str:
    """'fr', 'en' or 'other': the question's language; else that of the
    student's earlier messages, the latest first; else French (the courses
    are French)."""
    found = detect(question)
    for message in reversed(history or []):
        if found:
            break
        if message.get('role') == 'user':
            found = detect(message.get('content') or '')
    return found or 'fr'


# A teacher rule that names a language decides: with the rule and a sentence
# naming the student's language, the model followed the sentence in 2 of 8 runs
# (2026-11-17). Without any sentence, Research answered a French question in
# French 4 of 4 times under the rule "Always answer in English": the sentence
# then points to the rule instead. "language" alone is left out: "the C
# language" is not an answer language.
RULE_LANGUAGE = re.compile(r'\b(french|english|fran[cç]ais|anglais|langue)\b', re.I)
RULE_SENTENCE = "Write the answer in the language that the teacher's rules ask for."


def language_sentence(question: str, history: list[dict] | None = None,
                      teaching_rules: str = '') -> str:
    """The sentence to end the last user message with: the rule's language
    when a teacher rule names one, '' for another language."""
    if RULE_LANGUAGE.search(teaching_rules or ''):
        return RULE_SENTENCE
    return SENTENCES.get(answer_language(question, history), '')


def with_language(text: str, sentence: str) -> str:
    """text, then the language sentence when there is one."""
    return text + '\n\n' + sentence if sentence else text
