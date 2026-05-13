from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request):
    body: dict = {"status": "ok", "service": "omega-runtime-studio"}
    snap = getattr(request.app.state, "backend_snapshot", None)
    if snap is not None:
        body["backend"] = snap.to_public_dict()
    return body
