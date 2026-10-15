"""
test_teaching_reports.py — the facts a teacher report is written from, the
report itself, and who may ask for one.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.agent.model import ModelReply
from backend.app.models import (
    AgentRun, AuditReport, Class, ClassStudent, Document, Lab, Message, MessageCitation,
    MessageFeedback, SenderType, Session, UserRole,
)
from backend.app.services import teaching_reports
from backend.tests.conftest import make_client, make_user


async def _lab(db):
    teacher, ana, ben = make_user(role=UserRole.teacher), make_user(username="ana"), make_user(username="ben")
    db.add_all([teacher, ana, ben])
    await db.flush()
    cls = Class(id=uuid.uuid4(), name="BD", teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6])
    db.add(cls)
    await db.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="TP SQL")
    doc = Document(id=uuid.uuid4(), class_id=cls.id, filename="SQL.pdf", storage_path="x", content_hash="h",
                   uploaded_by=teacher.id)
    db.add_all([lab, doc, ClassStudent(class_id=cls.id, student_id=ana.id),
                ClassStudent(class_id=cls.id, student_id=ben.id)])
    await db.flush()
    return teacher, ana, ben, lab, doc


async def _exchange(db, session, question, *, mode="search", down=False):
    asked = Message(id=uuid.uuid4(), session_id=session.id, sender=SenderType.user, content=question)
    db.add(asked)
    await db.flush()
    answer = Message(id=uuid.uuid4(), session_id=session.id, sender=SenderType.llm, content="…",
                     answer_mode=mode, feedback=MessageFeedback.down if down else None)
    db.add(answer)
    await db.flush()
    return asked, answer


async def _activity(db):
    teacher, ana, ben, lab, doc = await _lab(db)
    s1, s2 = Session(id=uuid.uuid4(), user_id=ana.id, lab_id=lab.id), Session(id=uuid.uuid4(), user_id=ben.id, lab_id=lab.id)
    drive = Session(id=uuid.uuid4(), user_id=teacher.id, lab_id=lab.id, is_test=True)
    db.add_all([s1, s2, drive])
    await db.flush()
    _, cited = await _exchange(db, s1, "Quelle est la différence entre WHERE et HAVING ?")
    db.add(MessageCitation(message_id=cited.id, label="E1", document_id=doc.id, revision_id=uuid.uuid4(),
                           page_id=uuid.uuid4(), pdf_page_no=12, origin="text"))
    await _exchange(db, s1, "et GROUP BY ça sert à quoi pour les groupes ?", down=True)
    asked, _ = await _exchange(db, s2, "comment faire une jointure naturelle", mode="research")
    db.add(AgentRun(user_id=ben.id, session_id=s2.id, client_request_id="r", question=asked.content,
                    status="finished", stop_reason="budget_exhausted", user_message_id=asked.id))
    await _exchange(db, drive, "test drive question")
    await db.commit()
    return teacher, ana, ben, lab


@pytest.mark.asyncio
async def test_facts_are_counted_not_written(db_session):
    _, ana, _, lab = await _activity(db_session)
    facts = await teaching_reports.gather_facts(db_session, lab)
    assert (facts["questions"], facts["students"], facts["thumbs_down"], facts["answers_not_finished"]) == (3, 2, 1, 1)
    assert facts["modes"] == {"search": 2, "research": 1}
    assert facts["documents_by_questions"] == [{"file": "SQL.pdf", "questions": 1}]
    assert facts["pages_by_questions"] == [{"file": "SQL.pdf", "page": 12, "questions": 1}]
    assert facts["language"] == "French"
    # Students are S1, S2; their names never reach the model.
    assert {q["student"] for q in facts["question_list"]} == {"S1", "S2"}
    text = str(teaching_reports.build_messages(facts))
    assert "ana" not in text and "test drive" not in text
    assert "[tool limit reached]" in text and "[thumbs down]" in text

    alone = await teaching_reports.gather_facts(db_session, lab, student_id=ana.id)
    assert alone["questions"] == 2 and alone["students"] == 1


class FakeModel:
    def __init__(self, reply="## Summary\nOK", error=None):
        self.reply, self.error, self.calls = reply, error, []

    async def __call__(self, messages, max_output, timeout, *, purpose=""):
        self.calls.append((messages, purpose))
        if self.error:
            raise self.error
        return ModelReply(content=self.reply)


@pytest.mark.asyncio
@pytest.mark.parametrize("model, status, error", [
    (FakeModel(), "done", None),
    (FakeModel(error=TimeoutError()), "failed", "TimeoutError"),
    (FakeModel(reply="  "), "failed", "empty_reply"),
])
async def test_the_report_is_written_or_says_why_not(db_session, model, status, error):
    teacher, _, _, lab = await _activity(db_session)
    report = AuditReport(lab_id=lab.id, created_by=teacher.id)
    db_session.add(report)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    await teaching_reports.write_report(factory, report.id, model, model_name="flash")
    async with factory() as db:
        saved = await db.get(AuditReport, report.id)
        assert (saved.status, saved.error) == (status, error)
        assert saved.facts["questions"] == 3 and saved.finished_at is not None
        assert model.calls[0][1] == "teacher_report"


@pytest.mark.asyncio
async def test_a_lab_without_questions_gets_no_model_call(db_session):
    teacher, _, _, lab, _ = await _lab(db_session)
    report = AuditReport(lab_id=lab.id, created_by=teacher.id)
    db_session.add(report)
    await db_session.commit()
    model = FakeModel()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    await teaching_reports.write_report(factory, report.id, model)
    async with factory() as db:
        assert (await db.get(AuditReport, report.id)).error == "no_questions"
    assert model.calls == []


@pytest.mark.asyncio
async def test_only_the_lab_teacher_asks_and_one_runs_at_a_time(db_session, monkeypatch):
    teacher, ana, _, lab = await _activity(db_session)
    started = []
    monkeypatch.setattr(teaching_reports, "start_report", lambda factory, report_id, model, **kw: started.append(report_id))
    client = await make_client(db_session, teacher)
    async with client:
        first = await client.post(f"/api/teacher/labs/{lab.id}/reports", json={})
        again = await client.post(f"/api/teacher/labs/{lab.id}/reports", json={})
        student = await client.post(f"/api/teacher/labs/{lab.id}/reports", json={"student_id": str(ana.id)})
        nobody = await client.post(f"/api/teacher/labs/{lab.id}/reports", json={"student_id": str(uuid.uuid4())})
        latest = await client.get(f"/api/teacher/labs/{lab.id}/reports")
    assert first.status_code == 202 and first.json()["status"] == "running"
    assert again.json()["id"] == first.json()["id"] and len(started) == 2   # lab + student
    assert student.json()["student_id"] == str(ana.id)
    assert nobody.status_code == 404
    assert latest.json()["report"]["id"] == first.json()["id"]
    stranger = make_user(role=UserRole.teacher)
    db_session.add(stranger)
    await db_session.commit()
    other = await make_client(db_session, stranger)
    async with other:
        assert (await other.get(f"/api/teacher/labs/{lab.id}/reports")).status_code in (403, 404)
