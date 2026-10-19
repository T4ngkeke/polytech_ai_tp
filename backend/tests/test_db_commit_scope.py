"""Every request's database session commits before its response is sent.

FastAPI ends a dependency with yield after the response unless it is declared
with scope="function". With the default, a client that sends its next request
as soon as it has the answer (log in after an admin created the account, list
the labs after joining a class) did not see the new row yet (2026-10-16).
"""
from fastapi.routing import APIRoute

from backend.app.database import get_db
from backend.app.main import app


def db_dependencies(dependant, path=()):
    for dep in dependant.dependencies:
        if dep.call is get_db:
            yield path + (dep.call.__name__,), dep.scope
        yield from db_dependencies(dep, path + (getattr(dep.call, '__name__', '?'),))


def api_routes(routes):
    """The app's routes, inside included routers too (FastAPI wraps them)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif getattr(route, 'original_router', None) is not None:
            yield from api_routes(route.original_router.routes)


def test_every_route_commits_before_it_answers():
    found = [(route.path, chain, scope) for route in api_routes(app.routes)
             for chain, scope in db_dependencies(route.dependant)]
    assert len(found) > 50
    wrong = [(path, chain) for path, chain, scope in found if scope != 'function']
    assert not wrong, wrong
