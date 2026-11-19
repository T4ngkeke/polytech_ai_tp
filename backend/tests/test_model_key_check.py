"""The key check that the vLLM model servers load (model_servers/edullm_key_check.py)."""
import importlib.util
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

_path = Path(__file__).resolve().parents[2] / 'model_servers' / 'edullm_key_check.py'
if not _path.is_file():  # the backend image holds backend/ only
    pytest.skip('model_servers/ is not here', allow_module_level=True)
_spec = importlib.util.spec_from_file_location('edullm_key_check', _path)
key_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(key_check)


def _server(monkeypatch, key='s3cret'):
    monkeypatch.setenv(key_check.KEY_VARIABLE, key)
    ok = lambda request: PlainTextResponse('ok')  # noqa: E731
    paths = ['/health', '/metrics', '/v1/chat/completions', '/invocations', '/rerank', '/tokenize']
    app = Starlette(routes=[Route(p, ok, methods=['GET', 'POST']) for p in paths])
    app.add_middleware(key_check.KeyCheck)  # as vLLM's --middleware does
    return TestClient(app)


@pytest.mark.parametrize('path', ['/v1/chat/completions', '/invocations', '/rerank', '/tokenize'])
def test_every_model_path_needs_the_key(monkeypatch, path):
    client = _server(monkeypatch)
    assert client.post(path).status_code == 401
    assert client.post(path, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert client.post(path, headers={'Authorization': 'Basic s3cret'}).status_code == 401
    assert client.post(path, headers={'Authorization': 'Bearer s3cret'}).status_code == 200
    assert client.post(path, headers={'authorization': 'bearer s3cret'}).status_code == 200


@pytest.mark.parametrize('path', ['/health', '/metrics'])
def test_health_and_metrics_stay_open(monkeypatch, path):
    assert _server(monkeypatch).get(path).status_code == 200


def test_a_server_without_a_key_does_not_start(monkeypatch):
    monkeypatch.delenv(key_check.KEY_VARIABLE, raising=False)
    with pytest.raises(RuntimeError, match='EDULLM_MODEL_API_KEY'):
        key_check.KeyCheck(lambda *a: None)
