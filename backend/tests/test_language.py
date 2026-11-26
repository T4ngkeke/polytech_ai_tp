"""The answer language: named by a small model call (agent/context/language.py), then
one sentence at the end of the last user message."""
import pytest

from backend.app.agent.context.language import (
    ASK_PROMPT, DEFAULT_LANGUAGE, GENERIC_SENTENCE, ask_language, ask_messages, language_sentence,
    parse_language,
)
from backend.app.agent.model import ModelReply


class Model:
    def __init__(self, reply='ok'):
        self.seen, self.reply = [], reply

    async def __call__(self, messages, max_output, timeout, **kwargs):
        self.seen.append((messages, max_output, kwargs))
        return ModelReply(self.reply, model='m', input_tokens=1, output_tokens=1)


@pytest.mark.parametrize('reply, name', [
    ('German', 'German'),
    ('english.', 'English'),
    ('**Spanish**', 'Spanish'),
    ('Chinese\nThe student writes in Chinese.', 'Chinese'),
    ('Unknown', DEFAULT_LANGUAGE),
    ('', None),
    ('The student writes in German because the words are German.', None),
    ('Bonjour ! Voici la réponse : 42', None),
])
def test_parse_language(reply, name):
    assert parse_language(reply) == name


def test_the_call_sees_rules_and_the_student_words_only():
    history = [{'role': 'user', 'content': 'Explique la question 1'},
               {'role': 'assistant', 'content': 'COURSE ANSWER TEXT'},
               {'role': 'user', 'content': 'merci'}]
    system, user = ask_messages('and the next one?', history, '[LAB RULES]\n1. Antworte auf Deutsch.')
    assert system['content'] == ASK_PROMPT
    text = user['content']
    assert 'Antworte auf Deutsch' in text and 'Explique la question 1' in text and 'merci' in text
    assert text.rstrip().endswith('and the next one?')
    assert 'COURSE ANSWER TEXT' not in text            # no answers, no course text
    assert '(none)' in ask_messages('Hi', None, '')[1]['content']


def test_a_long_pasted_file_is_cut_in_the_call():
    question = 'Here is my file, what is wrong?\n' + 'int x = 0;\n' * 3000
    text = ask_messages(question, None, '')[1]['content']
    assert len(text) < 2000 and 'Here is my file' in text


@pytest.mark.asyncio
async def test_ask_language_returns_the_name_and_bills_its_purpose():
    model = Model('German')
    assert await ask_language(model, 'Was ist ein Zeiger?', None) == 'German'
    _, max_output, kwargs = model.seen[0]
    assert max_output <= 10 and kwargs['purpose'] == 'answer_language'


def test_the_sentence_names_the_language_or_stays_generic():
    assert language_sentence('German') == 'Write the answer in German.'
    assert language_sentence(None) == GENERIC_SENTENCE


@pytest.mark.asyncio
async def test_research_ends_the_question_with_the_language_sentence():
    from backend.app.agent.tools.course import CourseTools
    from backend.app.agent.runner import ResearchRunner
    from backend.tests.test_course_tools import ViewFixture
    from backend.tests.test_runner import Script
    model = Script('A pointer holds an address.', 'DONE')   # the answer, then CHECK_TURN
    await ResearchRunner(CourseTools(ViewFixture()), model).run('What is a pointer?', answer_language='English')
    assert model.seen[0][-1]['content'] == 'What is a pointer?\n\nWrite the answer in English.'
    # The system message does not depend on the language (cached prefix).
    other = Script('A pointer holds an address.', 'DONE')   # the answer, then CHECK_TURN
    await ResearchRunner(CourseTools(ViewFixture()), other).run("C'est quoi un pointeur ?",
                                                                 answer_language='French')
    assert other.seen[0][0] == model.seen[0][0]
    assert other.seen[0][-1]['content'].endswith('Write the answer in French.')
    # No language name (the call failed): the generic sentence.
    generic = Script('A pointer holds an address.', 'DONE')   # the answer, then CHECK_TURN
    await ResearchRunner(CourseTools(ViewFixture()), generic).run('Was ist ein Zeiger?')
    assert generic.seen[0][-1]['content'].endswith(GENERIC_SENTENCE)


def test_the_rule_block_says_which_level_wins():
    from backend.app.services.rule_service import RULES_PRECEDENCE, teaching_rules_text
    assert teaching_rules_text({'class_rules': None, 'lab_rules': None, 'student_rules': None}) == ''
    text = teaching_rules_text({'class_rules': '1. Court.', 'lab_rules': None, 'student_rules': '1. Long.'})
    assert text == RULES_PRECEDENCE + '\n\n[CLASS RULES]\n1. Court.\n\n[STUDENT RULES]\n1. Long.'
