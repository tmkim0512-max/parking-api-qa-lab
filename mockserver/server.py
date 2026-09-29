"""Lightweight mock server for HTTP dependencies (WireMock-like stub + request journal).

Control API
  POST /__admin/stubs     {"method": "GET", "path": "/route",
                           "responses": [{"status": 503}, {"status": 200, "body": {...}, "delay_ms": 0}]}
                          responses are served in order; the last one repeats.
                          body may be JSON or a raw string (to simulate broken payloads).
  GET  /__admin/requests  journal of every non-admin request received
  POST /__admin/reset     clear stubs and journal
Unmatched requests get 404 {"error": "no stub"}.
"""
import asyncio
import json

from fastapi import FastAPI, Request, Response

app = FastAPI(title="Mock server")
stubs: dict[tuple[str, str], dict] = {}
journal: list[dict] = []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/__admin/stubs", status_code=201)
def add_stub(stub: dict):
    stubs[(stub["method"].upper(), stub["path"])] = {"responses": stub["responses"], "calls": 0}
    return {"registered": f'{stub["method"].upper()} {stub["path"]}'}


@app.get("/__admin/requests")
def requests():
    return journal


@app.post("/__admin/reset")
def reset():
    stubs.clear()
    journal.clear()
    return {"reset": True}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def serve(path: str, request: Request):
    key = (request.method, "/" + path)
    journal.append({"method": key[0], "path": key[1], "query": dict(request.query_params)})
    stub = stubs.get(key)
    if stub is None:
        return Response(json.dumps({"error": "no stub"}), 404, media_type="application/json")
    spec = stub["responses"][min(stub["calls"], len(stub["responses"]) - 1)]
    stub["calls"] += 1
    await asyncio.sleep(spec.get("delay_ms", 0) / 1000)
    body = spec.get("body", "")
    content = body if isinstance(body, str) else json.dumps(body)
    return Response(content, spec.get("status", 200), media_type="application/json")
