"""Failures the user does not see are kept in memory and written for monitoring."""
import pytest

from backend.app.services import failure_log
from backend.app.services.retrieval_service import ChunkHit, apply_rerank


@pytest.fixture(autouse=True)
def empty():
    failure_log._pending.clear()
    yield
    failure_log._pending.clear()


class Session:
    def __init__(self, store, fail=False):
        self.store, self.fail = store, fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add_all(self, rows):
        self.rows = rows

    async def commit(self):
        if self.fail:
            raise ConnectionError('db down')
        self.store.extend(self.rows)


@pytest.mark.asyncio
async def test_failures_are_written_and_kept_when_the_write_fails():
    failure_log.record('rerank', 'ConnectTimeout')
    failure_log.record('embedding', 'APIConnectionError')
    stored = []
    assert await failure_log.flush(lambda: Session(stored, fail=True)) == 0
    assert len(failure_log._pending) == 2
    assert await failure_log.flush(lambda: Session(stored)) == 2
    assert [(r.component, r.kind) for r in stored] == [
        ('rerank', 'ConnectTimeout'), ('embedding', 'APIConnectionError')]
    assert not failure_log._pending


@pytest.mark.asyncio
async def test_a_failed_rerank_keeps_the_merged_order_and_is_recorded():
    hits = [ChunkHit(content=c, page_no=1, document_id=None, id=n) for n, c in enumerate('abc')]

    async def broken(query, documents):
        raise TimeoutError()

    ordered, _ = await apply_rerank('q', hits, broken, 2)
    assert [h.id for h in ordered] == [0, 1]
    assert [(c, k) for c, k, _ in failure_log._pending] == [('rerank', 'TimeoutError')]


def test_rule_check_reply_keeps_only_valid_pairs():
    from backend.app.services.rule_check import parse_conflicts
    reply = ('Here: {"conflicts": [{"rules": [3, 1], "reason": "French or English."}, '
             '{"rules": [1, 3], "reason": "again"}, {"rules": [2, 9]}, {"rules": [1, 1]}, "x"]}')
    assert parse_conflicts(reply, {1, 3}) == [{'rules': [1, 3], 'reason': 'French or English.'}]
    assert parse_conflicts('{"conflicts": []}', {1, 2}) == []
    with pytest.raises(ValueError):
        parse_conflicts('no json', {1, 2})
