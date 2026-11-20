"""test_memories.py — what the assistant remembers about a student, per class.

Covers storing (length, per-class cap), the student's list, deleting one or
all for good, the memory switch, and the teacher's view of one class.
"""
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Class, ClassStudent, StudentMemory, UserRole
from backend.app.services import memory_service as ms
from tests.conftest import make_client, make_user


@pytest_asyncio.fixture
async def seed(db_session: AsyncSession) -> dict:
    teacher, other = make_user(role=UserRole.teacher, username='teacher'), make_user(role=UserRole.teacher, username='other')
    lea, hugo = make_user(username='lea'), make_user(username='hugo')
    db_session.add_all([teacher, other, lea, hugo])
    await db_session.flush()
    py = Class(name='Python', teacher_id=teacher.id, invite_code='MEMO01')
    algo = Class(name='Algo', teacher_id=other.id, invite_code='MEMO02')
    db_session.add_all([py, algo])
    await db_session.flush()
    db_session.add_all([ClassStudent(class_id=py.id, student_id=lea.id),
                        ClassStudent(class_id=algo.id, student_id=lea.id)])
    await ms.add(db_session, user_id=lea.id, class_id=py.id, content='Préfère un indice avant la réponse.',
                 category='préférences')
    await ms.add(db_session, user_id=lea.id, class_id=py.id, content='Maîtrise le cas de base de factorielle.')
    await ms.add(db_session, user_id=lea.id, class_id=algo.id, content='Hésite sur le tri fusion.')
    await ms.add(db_session, user_id=hugo.id, class_id=py.id, content='Not lea.')
    await db_session.commit()
    return {'teacher': teacher, 'other': other, 'lea': lea, 'hugo': hugo, 'py': py, 'algo': algo}


@pytest.mark.asyncio
async def test_storing_checks_length_and_the_per_class_cap(db_session, seed, monkeypatch):
    with pytest.raises(ValueError, match='memory_length'):
        await ms.add(db_session, user_id=seed['lea'].id, class_id=seed['py'].id, content='x' * 201)
    with pytest.raises(ValueError, match='memory_length'):
        await ms.add(db_session, user_id=seed['lea'].id, class_id=seed['py'].id, content='   ')
    monkeypatch.setattr(ms, 'MAX_PER_CLASS', 2)
    with pytest.raises(ValueError, match='memory_full'):
        await ms.add(db_session, user_id=seed['lea'].id, class_id=seed['py'].id, content='One more.')
    # Another class has its own room.
    await ms.add(db_session, user_id=seed['lea'].id, class_id=seed['algo'].id, content='Fine.')


@pytest.mark.asyncio
async def test_the_student_lists_and_deletes_for_good(db_session, seed):
    async with await make_client(db_session, seed['lea']) as c:
        out = (await c.get('/api/student/memories')).json()
        assert out['enabled'] is True and len(out['items']) == 3
        py = (await c.get('/api/student/memories', params={'class_id': str(seed['py'].id)})).json()['items']
        assert {m['class_name'] for m in py} == {'Python'} and len(py) == 2
        assert (await c.delete(f"/api/student/memories/{py[0]['id']}")).status_code == 204
        assert (await c.delete(f"/api/student/memories/{py[0]['id']}")).status_code == 404
        out = await c.delete('/api/student/memories', params={'class_id': str(seed['algo'].id)})
        assert out.json() == {'deleted': 1}
        assert len((await c.get('/api/student/memories')).json()['items']) == 1
        assert (await c.delete('/api/student/memories')).json() == {'deleted': 1}
    left = await db_session.scalar(select(func.count()).select_from(StudentMemory))
    assert left == 1                                        # only hugo's is left
    async with await make_client(db_session, seed['hugo']) as c:
        assert len((await c.get('/api/student/memories')).json()['items']) == 1


@pytest.mark.asyncio
async def test_a_student_cannot_delete_another_ones_memory(db_session, seed):
    hugo_memory = await db_session.scalar(select(StudentMemory.id).where(StudentMemory.user_id == seed['hugo'].id))
    async with await make_client(db_session, seed['lea']) as c:
        assert (await c.delete(f'/api/student/memories/{hugo_memory}')).status_code == 404


@pytest.mark.asyncio
async def test_the_memory_switch(db_session, seed):
    async with await make_client(db_session, seed['lea']) as c:
        out = (await c.put('/api/auth/settings', json={'memory_enabled': False})).json()
        assert out['memory_enabled'] is False
        assert (await c.get('/api/student/memories')).json()['enabled'] is False


@pytest.mark.asyncio
async def test_a_teacher_sees_the_memories_of_their_class_only(db_session, seed):
    url = f"/api/teacher/classes/{seed['py'].id}/students/{seed['lea'].id}/memories"
    async with await make_client(db_session, seed['teacher']) as c:
        out = (await c.get(url)).json()
        assert out['enabled'] is True
        assert sorted(m['content'] for m in out['items']) == [
            'Maîtrise le cas de base de factorielle.', 'Préfère un indice avant la réponse.']
        not_in_class = f"/api/teacher/classes/{seed['py'].id}/students/{seed['hugo'].id}/memories"
        assert (await c.get(not_in_class)).status_code == 404
    async with await make_client(db_session, seed['other']) as c:
        assert (await c.get(url)).status_code == 403
