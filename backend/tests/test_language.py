"""The answer language: French or English from the student's words."""
import pytest

from backend.app.agent.direct import DirectRunner
from backend.app.agent.language import RULE_SENTENCE, SENTENCES, answer_language, detect, language_sentence
from backend.app.agent.model import ModelReply


@pytest.mark.parametrize('text, language', [
    ('What are we doing?', 'en'),
    ('I am stuck, can you help?', 'en'),
    ('Help', 'en'),
    ('What is the next question about?', 'en'),
    ('On fait quoi ?', 'fr'),
    ('Et la suivante ?', 'fr'),
    ("Je suis bloqué à l'exercice 3", 'fr'),
    ('Pourquoi mon pointeur est NULL ?', 'fr'),
    ('Merci', 'fr'),
    ('malloc ?', None),
    ('question 7', None),
    ('question I.1 ?', None),
    ('Et la II.3.b ?', 'fr'),
    ('How do I do I.1?', 'en'),
    ('第三题', 'other'),
    ('第三题怎么做？malloc', 'other'),
    # Code does not count: its keywords and names are English in both languages.
    ('Pourquoi ça plante ?\n```c\nint main(void) { if (p == NULL) return the_error; }\n```', 'fr'),
])
def test_detect(text, language):
    assert detect(text) == language


def test_unknown_falls_back_to_the_student_history_then_french():
    history = [{'role': 'user', 'content': 'What does this exercise ask?'},
               {'role': 'assistant', 'content': "L'exercice demande une liste chaînée."}]
    assert answer_language('question 7', history) == 'en'
    assert answer_language('question 7', [history[1]]) == 'fr'
    assert answer_language('question 7') == 'fr'
    assert answer_language('Et la suivante ?', history) == 'fr'
    assert answer_language('第三题怎么做？', history) == 'other'
    assert language_sentence('第三题怎么做？') == ''


def test_a_teacher_rule_naming_a_language_decides_alone():
    assert language_sentence('What is a pointer?') == SENTENCES['en']
    assert language_sentence('What is a pointer?', teaching_rules='[CLASS RULES]\n1. Answer in French.') == RULE_SENTENCE
    assert language_sentence('Pointeur ?', teaching_rules='[LAB RULES]\n1. Réponds en anglais.') == RULE_SENTENCE
    assert language_sentence('What is a pointer?', teaching_rules='[CLASS RULES]\n1. Use the C language.') \
        == SENTENCES['en']


class Model:
    def __init__(self):
        self.seen = []

    async def __call__(self, messages, max_output, timeout, **_):
        self.seen.append(messages)
        return ModelReply('ok', model='m', input_tokens=1, output_tokens=1)


@pytest.mark.asyncio
async def test_direct_ends_the_question_with_the_language_sentence():
    model = Model()
    await DirectRunner(model).run('What is a pointer?')
    assert model.seen[0][-1]['content'] == 'What is a pointer?\n\n' + SENTENCES['en']
    # The system message does not depend on the language (cached prefix).
    other = Model()
    await DirectRunner(other).run("C'est quoi un pointeur ?")
    assert other.seen[0][0] == model.seen[0][0]
    assert other.seen[0][-1]['content'].endswith(SENTENCES['fr'])


def test_the_rule_block_says_which_level_wins():
    from backend.app.services.rule_service import RULES_PRECEDENCE, teaching_rules_text
    assert teaching_rules_text({'class_rules': None, 'lab_rules': None, 'student_rules': None}) == ''
    text = teaching_rules_text({'class_rules': '1. Court.', 'lab_rules': None, 'student_rules': '1. Long.'})
    assert text == RULES_PRECEDENCE + '\n\n[CLASS RULES]\n1. Court.\n\n[STUDENT RULES]\n1. Long.'
    # The precedence line names no language: it must not hide the language sentence.
    assert language_sentence('What is a pointer?', teaching_rules=text) == SENTENCES['en']


@pytest.mark.asyncio
async def test_direct_repeats_the_rules_before_the_language_sentence():
    from backend.app.agent.runner import ANSWER_RULES, answer_ending
    rules = '[CLASS RULES]\n1. Court.'
    assert answer_ending('What is a pointer?', None, '') == SENTENCES['en']
    assert answer_ending('What is a pointer?', None, rules) == ANSWER_RULES.format(rules=rules) + '\n\n' + SENTENCES['en']
    model = Model()
    await DirectRunner(model).run('What is a pointer?', teaching_rules=rules)
    assert model.seen[0][-1]['content'] == 'What is a pointer?\n\n' + answer_ending('What is a pointer?', None, rules)
    assert rules in model.seen[0][0]['content']   # still in the system message too
