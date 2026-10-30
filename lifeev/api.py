"""HTTP boundary: validate all input before computation or persistence."""
import asyncio
from contextlib import asynccontextmanager
import hmac
import os
import logging
from threading import BoundedSemaphore
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import settings
from .storage import Store, VersionConflict, SnapshotCapacityError, SnapshotThrottled
from .simulation import SimulationBusy
from .skeleton import validate, assess, accept_override
from .stats import analyze
from .example import example


def create_app(store=None):
    if store is None:
        directory, key = settings()
        store = Store(directory / 'lifeev.sqlite', key)
    from .controller import Controller, propose_memory
    from .controls import route, VersionCache
    controller, cache = Controller(store), VersionCache()
    intake_slot = BoundedSemaphore(1)

    async def daily_backup():
        try:
            await asyncio.to_thread(store._daily)
        except (SnapshotCapacityError, OSError):
            # A full backup budget must not disable reads or health checks.
            logging.getLogger(__name__).warning('Daily backup could not complete; check snapshot storage limits and disk space')

    async def daily():
        while True:
            await asyncio.sleep(3600)
            await daily_backup()

    @asynccontextmanager
    async def lifespan(app):
        await daily_backup()
        task = asyncio.create_task(daily())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title='LLess', lifespan=lifespan)
    app.state.store = store
    extra_hosts = [h.strip() for h in os.getenv('LIFEEV_ALLOWED_HOSTS', '').split(',') if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'] + extra_hosts)

    @app.middleware('http')
    async def guard(request, call_next):
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            return JSONResponse({'detail': 'Invalid content length'}, 400)
        if length < 0:
            return JSONResponse({'detail': 'Invalid content length'}, 400)
        if length > 1_000_000:
            return JSONResponse({'detail': 'Request exceeds 1 MB limit'}, 413)
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'Cross-origin writes are disabled'}, 403)
        token = os.getenv('LIFEEV_API_TOKEN')
        if token and request.url.path.startswith('/api/'):
            header = request.headers.get('authorization', '')
            supplied = header[7:] if header.startswith('Bearer ') else ''
            if not hmac.compare_digest(token.encode(), supplied.encode()):
                return JSONResponse({'detail': 'API token required'}, 401)
        if request.method in ('POST', 'PUT', 'PATCH'):
            body_bytes = bytearray()
            async for chunk in request.stream():
                body_bytes.extend(chunk)
                if len(body_bytes) > 1_000_000:
                    return JSONResponse({'detail': 'Request exceeds 1 MB limit'}, 413)
            request._body = bytes(body_bytes)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValueError)
    @app.exception_handler(OverflowError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, 422)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail': 'Record not found'}, 404)

    @app.exception_handler(VersionConflict)
    async def conflict(request, exc):
        return JSONResponse({'detail': str(exc)}, 409)

    @app.exception_handler(SnapshotCapacityError)
    async def snapshot_capacity(request, exc):
        return JSONResponse({'detail': str(exc)}, 507)

    @app.exception_handler(SnapshotThrottled)
    async def snapshot_cooldown(request, exc):
        return JSONResponse({'detail': str(exc)}, 429, headers={'Retry-After': str(exc.retry_after)})

    @app.exception_handler(SimulationBusy)
    async def simulation_busy(request, exc):
        return JSONResponse({'detail': str(exc)}, 429, headers={'Retry-After': '1'})

    def checked(document):
        errors = validate(document)
        if errors:
            raise ValueError('; '.join(errors))
        return document

    def results(document, force_tier=None, upstream=None):
        checked(document)
        assessment = assess(document)
        routing = route(document, force_tier=force_tier, confidence=assessment.get('confidence', 0))
        upstream = upstream or {'weight_version': document.get('weight_version'), 'bankroll': document.get('bankroll')}
        value = cache.get(document, upstream, routing['tier'])
        hit = value is not None
        if not hit:
            value = analyze(document)
            cache.put(document, upstream, routing['tier'], value)
        return {**value, 'assessment': assessment, 'routing': routing, 'cache_hit': hit}

    @app.get('/api/health')
    def health():
        configured = all(os.getenv(name, '').strip() for name in ('LIFEEV_MODEL_BASE_URL', 'LIFEEV_MODEL_API_KEY'))
        return {'status': 'ok', 'mode': 'single-user', 'agent': 'deterministic offline controller', 'retrieval': 'hashed vector + lexical rerank fallback',
                'model_provider': 'configured' if configured else 'not configured'}

    @app.get('/api/example')
    def illustrative():
        return example()

    @app.get('/api/nodes')
    def nodes(include_archived: bool = False, summary: bool = False):
        return store.list_nodes(include_archived, summary)

    @app.post('/api/nodes')
    def create(body: dict):
        doc = checked(body.get('document', {}))
        node = store.create(doc, body.get('parent_id'), body.get('parent_version'))
        return node

    @app.get('/api/nodes/{id}')
    def node(id: str, version: int | None = None):
        return store.get(id, version)

    @app.post('/api/nodes/{id}/versions')
    def revise(id: str, body: dict):
        return store.revise(id, checked(body.get('document', {})), body.get('source_version'))

    @app.post('/api/analyze')
    def statistics(body: dict):
        return results(body.get('document', {}), body.get('force_tier'))

    @app.post('/api/intake/preview')
    def intake_preview(body: dict):
        from .intake_service import preview
        if not intake_slot.acquire(blocking=False):
            return JSONResponse({'detail': 'Another intake request is running; retry shortly.'}, 429, headers={'Retry-After': '1'})
        try:
            return preview(body.get('text'), body.get('document', {}), body.get('use_model', False))
        finally:
            intake_slot.release()

    @app.post('/api/quick')
    def quick(body: dict):
        # Parse and describe only: no model, session, retrieval, tier routing, cache or storage write.
        from .quick import quick_document, quick_text
        if body.get('document') is not None:
            return quick_document(checked(body['document']))
        return quick_text(body.get('text'))

    @app.get('/api/nodes/{id}/stats')
    def node_stats(id: str, version: int | None = None, force_tier: str | None = None):
        n = store.get(id, version)
        return results(n['document'], force_tier, {'version': n['version'], 'parent_version': n['parent_version'], 'frozen_parent': n['frozen_parent']})

    @app.post('/api/sessions')
    def start(body: dict):
        if body.get('pins') is not None and (not isinstance(body['pins'], list) or any(not isinstance(p, str) for p in body['pins'])):
            raise ValueError('pins must be a list of node IDs')
        return controller.start(checked(body.get('document', {})), body.get('parent_id'), body.get('pins'), body.get('force_tier'), body.get('parent_version'))

    @app.get('/api/sessions')
    def sessions(summary: bool = False):
        return store.list_sessions(summary)

    @app.get('/api/sessions/{id}')
    def resume(id: str):
        return controller.resume(id)

    @app.post('/api/sessions/{id}/answers')
    def answer(id: str, body: dict):
        return controller.answer(id, body.get('field'), body.get('value'), body.get('skip', False))

    @app.post('/api/sessions/{id}/finalize')
    def finalize(id: str):
        return controller.finalize(id)

    @app.post('/api/overrides')
    def override(body: dict):
        return accept_override(checked(body.get('document', {})), body.get('field'), body.get('accepted'))

    @app.post('/api/nodes/{id}/archive')
    def archive(id: str):
        return store.archive(id)

    @app.post('/api/nodes/{id}/restore')
    def restore(id: str):
        return store.restore(id)

    @app.get('/api/profiles')
    def profiles():
        return store.list_profiles()

    @app.post('/api/profiles')
    def profile(body: dict):
        if not isinstance(body.get('bankroll'), dict) or not isinstance(body.get('weights'), dict):
            raise ValueError('Profiles require bankroll and weights objects')
        checked({k: body[k] for k in ('bankroll', 'weights', 'weight_version') if k in body})
        if not body.get('weight_version'):
            raise ValueError('weight_version is required')
        from datetime import datetime, timezone
        body = {k: v for k, v in body.items() if k != 'id'}
        body['created_at'] = datetime.now(timezone.utc).isoformat()
        with store._lock:
            if any(p.get('weight_version') == body['weight_version'] for p in store.list_profiles()):
                raise ValueError('Weight version already exists; create a new version')
            return store.save_profile(body)

    @app.get('/api/memory')
    def memory():
        return store.list_memory()

    @app.post('/api/memory')
    def memory_write(body: dict):
        if body.get('reviewed_conflicts') is not None and (not isinstance(body['reviewed_conflicts'], list) or any(not isinstance(p, str) for p in body['reviewed_conflicts'])):
            raise ValueError('reviewed_conflicts must be a list of IDs')
        return propose_memory(store, body.get('key'), body.get('value'), body.get('confirmed', False), body.get('reviewed_conflicts'))

    @app.get('/api/audit')
    def audit(action: str | None = None, limit: int | None = None):
        return store.audit(action, limit)

    @app.post('/api/snapshots')
    def snapshot():
        return {'path': store.snapshot('manual')}

    @app.get('/api/maintenance/candidates')
    def candidates():
        from .maintenance import candidates
        return candidates(store)

    @app.post('/api/maintenance/archive')
    def prune_selected(body: dict):
        from .maintenance import prune
        for field in ('ids', 'pins'):
            if not isinstance(body.get(field, []), list) or any(not isinstance(i, str) for i in body.get(field, [])):
                raise ValueError(field + ' must be a list of IDs')
        return prune(store, body.get('ids', []), body.get('pins', []))

    @app.post('/api/retrieve')
    def retrieve(body: dict):
        if not isinstance(body.get('query', ''), str):
            raise ValueError('query must be text')
        if body.get('pins') is not None and (not isinstance(body['pins'], list) or any(not isinstance(p, str) for p in body['pins'])):
            raise ValueError('pins must be a list of IDs')
        return controller.retrieve(body.get('query', ''), body.get('pins'))

    @app.post('/api/simulate')
    def simulation(body: dict):
        from .simulation import simulate
        ids = body.get('node_ids', [])
        if not isinstance(ids, list) or any(not isinstance(id, str) for id in ids):
            raise ValueError('node_ids must be a list of node IDs')
        if not 1 <= len(ids) <= 2 or len(set(ids)) != len(ids):
            raise ValueError('Select one node or two distinct directly linked nodes')
        selected = [store.get(id) for id in ids]
        linked = len(selected) == 1 or any(n['parent_id'] == other['id'] and n['parent_version'] == other['version'] for n in selected for other in selected if n is not other)
        if not linked:
            raise ValueError('Pair must link to the selected historical parent version')
        children = store.list_nodes(summary=True)
        if any(sum(c['parent_id'] == n['id'] for c in children) >= 3 for n in selected):
            raise ValueError('Monte Carlo disabled at three or more branches')
        return simulate([n['document'] for n in selected], samples=body.get('samples', 10000), seed=body.get('seed', 42), linked=linked)

    app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent.parent / 'web', html=True), name='web')
    return app


app = create_app()
