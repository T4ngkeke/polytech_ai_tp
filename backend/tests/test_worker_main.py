"""
test_worker_main.py — what the worker entrypoint is made of.

The loop itself is tested in test_worker_run.py.
"""


def test_only_the_structured_path_is_left():
    """The pre-MinerU pipeline (one job per legacy row, chunk context, formula
    repair, page reader) and the exercise classifier are gone."""
    import importlib.util

    import backend.worker.main as worker_main

    for name in ("process_one", "_make_real_context_fn", "_parse_classified_lines", "run_hint_job"):
        assert not hasattr(worker_main, name), name
    for module in ("ingest", "page_reader", "vlm", "context", "chunking", "parsing"):
        assert importlib.util.find_spec(f"backend.worker.{module}") is None, module


def test_the_admin_mineru_server_replaces_the_deployment_one_for_a_tick(tmp_path):
    from backend.worker.main import with_mineru_server
    from backend.worker.structured_ingest import StructuredSettings
    deployed = StructuredSettings.from_env(tmp_path, {'PDF_MINERU_SERVER_URL': 'http://mineru-a:8000/v1',
                                                      'PDF_MINERU_PYTHON': '/opt/mineru/bin/python'})
    assert with_mineru_server(deployed, '').pdf_server_url == 'http://mineru-a:8000/v1'
    switched = with_mineru_server(deployed, 'http://mineru-b:8000/v1')
    assert switched.pdf_server_url == 'http://mineru-b:8000/v1' and switched.pdf_python == '/opt/mineru/bin/python'
    assert deployed.pdf_server_url == 'http://mineru-a:8000/v1'  # the deployment value is kept
    assert with_mineru_server(None, 'http://mineru-b:8000/v1') is None


def test_caption_settings_follow_the_admin_map(tmp_path):
    from backend.app.services.model_routing import Endpoint
    from backend.worker.main import with_captions
    from backend.worker.picture_captions import CaptionSettings, settings_from_config
    from backend.worker.structured_ingest import StructuredSettings
    vlm = Endpoint('http://model:8000/v1', 'k', 'ornith')
    assert settings_from_config({}, vlm) is None                                   # off by default
    assert settings_from_config({'PICTURE_CAPTIONS': 'on'}, Endpoint('http://model', 'k', '')) is None
    on = settings_from_config({'PICTURE_CAPTIONS': 'on', 'PICTURE_CAPTION_PRIORITY': '10',
                               'PICTURE_CAPTION_BUDGET_SECONDS': '600'}, vlm)
    assert on == CaptionSettings(vlm, priority=10, budget_seconds=600)
    loose = settings_from_config({'PICTURE_CAPTIONS': 'ON', 'PICTURE_CAPTION_PRIORITY': '',
                                  'PICTURE_CAPTION_BUDGET_SECONDS': 'x'}, vlm)
    assert loose.priority is None and loose.budget_seconds == 900
    deployed = StructuredSettings.from_env(tmp_path, {})
    assert with_captions(deployed, on).captions == on and deployed.captions is None
    assert with_captions(None, on) is None
