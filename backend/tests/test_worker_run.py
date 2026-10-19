"""
test_worker_run.py — the worker loop's decision logic (one tick).

`run_tick` waits on the chat-load gate and the GPU gate, runs at most one
structured job (parse, then index), sweeps when there is nothing to do, and
sleeps when gated or idle. The jobs themselves are replaced by recorders.
"""

import pytest

from backend.app.config import settings
from backend.worker import structured_index, structured_ingest
from backend.worker.gpu_gate import ChatLoadGate
from backend.worker.main import make_fn_builder, run_tick


# --- config hot-reload: rebuild model clients only when config changes --------
# The worker reads SystemConfig each loop iteration; an admin config edit takes
# effect without a restart, but the (heavier) client rebuild only runs on a change.

@pytest.mark.asyncio
async def test_fn_builder_rebuilds_only_when_config_changes():
    configs = [{"model": "a"}, {"model": "a"}, {"model": "b"}]
    i = {"n": 0}

    async def fake_load(_db):
        cfg = configs[i["n"]]
        i["n"] += 1
        return cfg

    builds: list[dict] = []

    def fake_build(cfg):
        builds.append(dict(cfg))
        return f"fns:{cfg['model']}"

    build = make_fn_builder(load_config=fake_load, build_fns=fake_build)

    assert await build(None) == "fns:a"   # first call → build
    assert await build(None) == "fns:a"   # unchanged config → reuse cached fns
    assert await build(None) == "fns:b"   # config changed → rebuild
    assert builds == [{"model": "a"}, {"model": "b"}]  # rebuilt only on change


class FakeGate:
    def __init__(self, is_open: bool):
        self._open = is_open

    def should_run(self) -> bool:
        return self._open


class Settings:
    lease_seconds, max_attempts = 1800, 3


async def fake_embed(texts):
    return [[0.0] for _ in texts]


@pytest.fixture
def jobs(monkeypatch):
    """Record the job steps; `parse` and `index` say whether a job was waiting."""
    monkeypatch.setattr(settings, "STRUCTURED_INGEST_ENABLED", True)
    state = {"parse": False, "index": False, "calls": []}

    async def parse(db, *, settings):
        state["calls"].append("parse")
        return state["parse"]

    async def index(db, *, embed_fn, lease_seconds, max_attempts):
        state["calls"].append("index")
        return state["index"]

    async def sweep(db, settings):
        state["calls"].append("sweep")

    monkeypatch.setattr(structured_ingest, "process_structured_job", parse)
    monkeypatch.setattr(structured_ingest, "sweep", sweep)
    monkeypatch.setattr(structured_index, "process_index_job", index)
    return state


async def tick(gate, *, chat_gate=None, chat_load=0.0, structured=Settings()):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def load(_db):
        return chat_load

    ran = await run_tick(None, gate, embed_fn=fake_embed, sleep_fn=fake_sleep,
                         idle_seconds=5, gate_seconds=10, chat_gate=chat_gate,
                         chat_load_fn=load if chat_gate else None, structured=structured)
    return ran, sleeps


@pytest.mark.asyncio
async def test_a_closed_gpu_gate_runs_nothing_and_sleeps(jobs):
    jobs["parse"] = True
    ran, sleeps = await tick(FakeGate(False))
    assert (ran, sleeps, jobs["calls"]) == (False, [10], [])


@pytest.mark.asyncio
async def test_a_busy_chat_pauses_the_worker_even_if_the_gpu_is_idle(jobs):
    jobs["parse"] = True
    chat_gate = ChatLoadGate(window=1)
    ran, sleeps = await tick(FakeGate(True), chat_gate=chat_gate, chat_load=1000.0)
    assert (ran, sleeps, jobs["calls"]) == (False, [10], [])


@pytest.mark.asyncio
async def test_a_parse_job_runs_first(jobs):
    jobs["parse"] = True
    ran, sleeps = await tick(FakeGate(True))
    assert (ran, sleeps, jobs["calls"]) == (True, [], ["parse"])


@pytest.mark.asyncio
async def test_an_index_job_runs_when_no_parse_waits(jobs):
    jobs["index"] = True
    ran, sleeps = await tick(FakeGate(True))
    assert (ran, sleeps, jobs["calls"]) == (True, [], ["parse", "index"])


@pytest.mark.asyncio
async def test_with_nothing_to_do_the_worker_sweeps_and_sleeps(jobs):
    ran, sleeps = await tick(FakeGate(True))
    assert (ran, sleeps, jobs["calls"]) == (False, [5], ["parse", "index", "sweep"])


@pytest.mark.asyncio
async def test_without_structured_ingestion_there_is_nothing_to_run(jobs, monkeypatch):
    monkeypatch.setattr(settings, "STRUCTURED_INGEST_ENABLED", False)
    jobs["parse"] = True
    ran, sleeps = await tick(FakeGate(True))
    assert (ran, sleeps, jobs["calls"]) == (False, [5], [])
