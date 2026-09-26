"""Application API (plan §15).

* Bearer-token authentication; every watch route checks ownership.
* Mutations require ``If-Match: <expected_version>`` (or ``expected_version`` in
  the body) and an ``Idempotency-Key`` header; replays return the stored response.
* ``/demo/*`` controls exist only in the development profile.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from common.clock import iso
from common.money import minor_to_display
from common.util import canonical_json, sha256_b64url
from persistence import repo
from purchase_coordinator.context import Context
from watch_domain.models import WatchDraft, normalize_rule
from watch_domain.parser import parse_request
from watch_domain.service import ServiceError, WatchService

log = logging.getLogger("pricewatch.api")


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return iso(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


class ParseRequest(BaseModel):
    text: str
    timezone: str = "Asia/Kolkata"


class MutationBody(BaseModel):
    expected_version: Optional[int] = None


class PriceScenario(BaseModel):
    sku: str
    item_price_minor: Optional[int] = None
    shipping_minor: Optional[int] = None
    tax_minor: Optional[int] = None
    fees_minor: Optional[int] = None
    stock: Optional[int] = None
    tax_unknown: Optional[bool] = None


class FaultBody(BaseModel):
    target: str  # 'adapter' or a merchant id
    fault: str
    count: int = 1


def create_api(ctx: Context, service: Optional[WatchService] = None) -> FastAPI:
    svc = service or WatchService(ctx)
    app = FastAPI(title="Autonomous Price-Watch Buyer API", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=ctx.settings.api_cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.state.ctx = ctx
    app.state.service = svc

    # ---------------------------------------------------------------- auth
    def current_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, {"code": "unauthenticated", "message": "Bearer token required"})
        token = authorization.split(" ", 1)[1].strip()
        with ctx.engine.begin() as conn:
            user = repo.user_by_token(conn, hashlib.sha256(token.encode("utf-8")).hexdigest())
        if not user:
            raise HTTPException(401, {"code": "unauthenticated", "message": "unknown token"})
        return user

    def owned_watch(watch_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
        with ctx.engine.begin() as conn:
            w = repo.get_watch(conn, watch_id)
        if w is None or w["user_id"] != user["id"]:
            raise HTTPException(404, {"code": "not_found", "message": "watch not found"})
        return w

    def expected_version(w: Dict[str, Any], if_match: Optional[str], body: Optional[MutationBody]) -> int:
        if if_match is not None:
            try:
                return int(if_match.strip('"'))
            except ValueError:
                raise HTTPException(400, {"code": "bad_if_match", "message": "If-Match must be the watch version"})
        if body and body.expected_version is not None:
            return body.expected_version
        raise HTTPException(428, {"code": "version_required", "message": "send If-Match: <version> or expected_version"})

    async def idempotent(request: Request, user: Dict[str, Any], route: str, key: Optional[str], compute):
        if not key:
            raise HTTPException(428, {"code": "idempotency_key_required", "message": "Idempotency-Key header required for mutations"})
        raw = await request.body()
        digest = sha256_b64url(raw or b"{}")
        with ctx.engine.begin() as conn:
            prior = repo.get_idempotent(conn, key, user["id"], route)
        if prior:
            if prior["request_digest"] != digest:
                raise HTTPException(409, {"code": "idempotency_conflict", "message": "Idempotency-Key reused with a different payload"})
            return JSONResponse(status_code=prior["response_status"], content=prior["response_body"])
        try:
            status, body = compute()
        except ServiceError as e:
            status, body = e.status, {"code": e.code, "message": e.message}
        body = _json_safe(body)
        with ctx.engine.begin() as conn:
            try:
                repo.put_idempotent(conn, key, user["id"], route, digest, status, body, ctx.now())
            except Exception:  # pragma: no cover - concurrent identical request
                pass
        return JSONResponse(status_code=status, content=body)

    @app.exception_handler(ServiceError)
    async def _svc_error(_req, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message})

    # ---------------------------------------------------------- catalog
    @app.get("/v1/catalog")
    def catalog(user=Depends(current_user)):
        return {
            "products": list(svc.products.values()),
            "merchants": [{"id": mid, "name": c["name"], "website": c["website"], "items": [{"sku": i["sku"], "title": i["title"], "canonical_product_id": i["canonical_product_id"], "attributes": i.get("attributes", {}), "bundle": i.get("bundle", False)} for i in c["items"]]} for mid, c in svc.catalogs.items()],
            "profiles": {"ap2": ctx.profile("ap2").version, "vi": ctx.profile("vi").version},
        }

    @app.get("/v1/destinations")
    def destinations(user=Depends(current_user)):
        with ctx.engine.begin() as conn:
            return {"destinations": repo.list_destinations(conn, user["id"])}

    @app.post("/v1/watches/parse")
    def parse(body: ParseRequest, user=Depends(current_user)):
        known = {mid: c["name"] for mid, c in svc.catalogs.items()}
        return parse_request(body.text, now=ctx.now(), timezone=body.timezone, known_merchants=known)

    @app.post("/v1/watches/preview")
    async def preview(request: Request, user=Depends(current_user)):
        try:
            draft = WatchDraft.model_validate(await request.json())
        except ValidationError as e:
            raise HTTPException(422, {"code": "invalid_draft", "message": json.loads(e.json())})
        return normalize_rule(draft, ctx.merchant_names).model_dump()

    # ----------------------------------------------------------- watches
    @app.post("/v1/watches", status_code=201)
    async def create_watch(request: Request, user=Depends(current_user), idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
        payload = await request.json()
        try:
            draft = WatchDraft.model_validate(payload)
        except ValidationError as e:
            raise HTTPException(422, {"code": "invalid_draft", "message": json.loads(e.json())})
        return await idempotent(request, user, "POST /v1/watches", idempotency_key, lambda: (201, _present(svc.create(user["id"], draft))))

    @app.get("/v1/watches")
    def list_watches(user=Depends(current_user)):
        with ctx.engine.begin() as conn:
            return {"watches": [_present(w) for w in repo.list_watches(conn, user["id"])]}

    @app.get("/v1/watches/{watch_id}")
    def get_watch(watch_id: str, user=Depends(current_user)):
        w = owned_watch(watch_id, user)
        with ctx.engine.begin() as conn:
            auth = repo.active_authorization(conn, watch_id)
            attempt = repo.active_attempt(conn, watch_id)
        out = _present(w)
        out["authorization"] = _json_safe(auth) if auth else None
        out["active_attempt"] = _json_safe(attempt) if attempt else None
        out["clock"] = {"now": iso(ctx.now()), "simulated": ctx.clock.__class__.__name__ != "SystemClock"}
        return out

    @app.post("/v1/watches/{watch_id}/authorization-proposal")
    async def authorization_proposal(watch_id: str, request: Request, user=Depends(current_user), idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
        w = owned_watch(watch_id, user)
        return await idempotent(request, user, "POST /v1/watches/%s/authorization-proposal" % watch_id, idempotency_key, lambda: (200, svc.proposal(w)))

    async def _mutation(watch_id, request, user, if_match, idempotency_key, action):
        w = owned_watch(watch_id, user)
        body = None
        raw = await request.body()
        if raw:
            try:
                body = MutationBody.model_validate_json(raw)
            except ValidationError:
                body = None
        version = expected_version(w, if_match, body)
        return await idempotent(request, user, "POST /v1/watches/%s/%s" % (watch_id, action.__name__), idempotency_key, lambda: (200, _present(action(w, version))))

    @app.post("/v1/watches/{watch_id}/activate")
    async def activate(watch_id: str, request: Request, user=Depends(current_user), if_match: Optional[str] = Header(default=None, alias="If-Match"), idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
        return await _mutation(watch_id, request, user, if_match, idempotency_key, svc.activate)

    @app.post("/v1/watches/{watch_id}/pause")
    async def pause(watch_id: str, request: Request, user=Depends(current_user), if_match: Optional[str] = Header(default=None, alias="If-Match"), idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
        return await _mutation(watch_id, request, user, if_match, idempotency_key, svc.pause)

    @app.post("/v1/watches/{watch_id}/resume")
    async def resume(watch_id: str, request: Request, user=Depends(current_user), if_match: Optional[str] = Header(default=None, alias="If-Match"), idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
        return await _mutation(watch_id, request, user, if_match, idempotency_key, svc.resume)

    @app.post("/v1/watches/{watch_id}/cancel")
    async def cancel(watch_id: str, request: Request, user=Depends(current_user), if_match: Optional[str] = Header(default=None, alias="If-Match"), idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
        return await _mutation(watch_id, request, user, if_match, idempotency_key, svc.cancel)

    @app.get("/v1/watches/{watch_id}/observations")
    def observations(watch_id: str, user=Depends(current_user)):
        owned_watch(watch_id, user)
        rows = svc.observations(watch_id)
        for r in rows:
            if r.get("total_minor") is not None:
                r["total_display"] = minor_to_display(r["total_minor"], r.get("currency") or "USD")
        return {"observations": _json_safe(rows)}

    @app.get("/v1/watches/{watch_id}/timeline")
    def timeline(watch_id: str, user=Depends(current_user)):
        owned_watch(watch_id, user)
        with ctx.engine.begin() as conn:
            runs = repo.list_runs(conn, watch_id)
        return {"events": _json_safe(svc.timeline(watch_id)), "evaluation_runs": _json_safe(runs)}

    @app.get("/v1/watches/{watch_id}/purchase")
    def purchase(watch_id: str, user=Depends(current_user)):
        owned_watch(watch_id, user)
        return _json_safe(svc.purchase(watch_id))

    @app.get("/v1/watches/{watch_id}/evidence/{artifact_id}")
    def evidence(watch_id: str, artifact_id: str, user=Depends(current_user)):
        owned_watch(watch_id, user)
        with ctx.engine.begin() as conn:
            row = repo.get_artifact(conn, artifact_id)
            if not row or row["watch_id"] != watch_id:
                raise HTTPException(404, {"code": "not_found", "message": "artifact not found"})
            content = ctx.vault.load(conn, artifact_id)
        return {"artifact_id": artifact_id, "kind": row["kind"], "digest": row["digest"], "content": _redact(content)}

    @app.get("/v1/metrics")
    def metrics(user=Depends(current_user)):
        with ctx.engine.begin() as conn:
            m = repo.all_metrics(conn)
            m["open_reconciliation_cases"] = sum(1 for c in repo.list_cases(conn) if c["state"] == "open")
        return {"metrics": m, "clock": {"now": iso(ctx.now()), "simulated": ctx.clock.__class__.__name__ != "SystemClock"}}

    # ------------------------------------------------------ demo controls
    if ctx.settings.demo_enabled:

        @app.post("/demo/merchants/{merchant_id}/price-scenario")
        def demo_price(merchant_id: str, body: PriceScenario, user=Depends(current_user)):
            ep = ctx.ucp.endpoints.get(merchant_id)
            if not ep:
                raise HTTPException(404, {"code": "unknown_merchant"})
            r = ep.client.post(ep.base_url + "/demo/price-scenario", json={k: v for k, v in body.model_dump().items() if v is not None})
            return r.json()

        @app.post("/demo/watches/{watch_id}/evaluate-now")
        def demo_evaluate(watch_id: str, user=Depends(current_user)):
            w = owned_watch(watch_id, user)
            now = ctx.now()
            with ctx.engine.begin() as conn:
                repo.update_watch_versioned(conn, watch_id, w["version"], {"next_check_at": now}, bump_version=False, now=now)
            return {"results": _json_safe(app.state.worker.tick())} if getattr(app.state, "worker", None) else {"scheduled": iso(now), "note": "the worker process will pick this up on its next tick"}

        @app.post("/demo/faults")
        def demo_faults(body: FaultBody, user=Depends(current_user)):
            if body.target == "adapter":
                with ctx.engine.begin() as conn:
                    fid = repo.add_fault(conn, body.target, body.fault, body.count, ctx.now())
                return {"ok": True, "fault_id": fid}
            ep = ctx.ucp.endpoints.get(body.target)
            if not ep:
                raise HTTPException(404, {"code": "unknown_target"})
            return ep.client.post(ep.base_url + "/demo/faults", json={"fault": body.fault, "count": body.count}).json()

        @app.post("/demo/clock/advance")
        def demo_clock(seconds: int, user=Depends(current_user)):
            clock = ctx.clock
            if not hasattr(clock, "advance"):
                raise HTTPException(409, {"code": "real_clock", "message": "the API is running on the system clock"})
            return {"now": iso(clock.advance(seconds=seconds)), "simulated": True}

        @app.get("/demo/merchants/{merchant_id}/orders")
        def demo_orders(merchant_id: str, user=Depends(current_user)):
            ep = ctx.ucp.endpoints.get(merchant_id)
            if not ep:
                raise HTTPException(404, {"code": "unknown_merchant"})
            return ep.client.get(ep.base_url + "/demo/orders").json()

    return app


def _present(w: Dict[str, Any]) -> Dict[str, Any]:
    out = _json_safe(dict(w))
    out.pop("lease_token", None)
    out["threshold_display"] = minor_to_display(w["threshold_minor"], w["currency"])
    out["inclusive_ceiling_minor"] = w["threshold_minor"] - 1 if w["price_operator"] == "lt" else w["threshold_minor"]
    return out


def _redact(content: Any) -> Any:
    """Evidence view: show structure and digests, never a reusable credential token."""
    if isinstance(content, dict):
        out = {}
        for k, v in content.items():
            if k in ("token", "credential") and isinstance(v, str):
                out[k] = "<redacted:%s>" % sha256_b64url(v.encode("utf-8"))[:16]
            else:
                out[k] = _redact(v)
        return out
    if isinstance(content, list):
        return [_redact(v) for v in content]
    if isinstance(content, str) and len(content) > 600:
        return content[:120] + "…<%d chars, sha256=%s>" % (len(content), sha256_b64url(content.encode("utf-8"))[:16])
    return content


def create_app() -> FastAPI:  # pragma: no cover - uvicorn entry point
    from purchase_coordinator.bootstrap import build_context

    logging.basicConfig(level=logging.INFO)
    ctx = build_context()
    return create_api(ctx)


if __name__ == "__main__":  # pragma: no cover
    import os

    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
