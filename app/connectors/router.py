"""Connector contract over HTTP: POST /connector/v1/{connector_key}/{method}."""
from fastapi import APIRouter, Body

from app.connectors.registry import all_connectors, get_connector
from app.connectors.schemas import (AssistPackRequest, MetricsRequest, PostRefRequest, PublishRequest,
                                    RefreshAuthRequest, ValidateRequest)
from app.errors import AppError, ErrorCode

router = APIRouter(prefix="/connector/v1", tags=["connector-contract"])

METHOD_MODELS = {
    "validate": ValidateRequest,
    "publish": PublishRequest,
    "get_status": PostRefRequest,
    "delete": PostRefRequest,
    "fetch_metrics": MetricsRequest,
    "refresh_auth": RefreshAuthRequest,
    "build_assist_pack": AssistPackRequest,
}


def call_method(connector_key: str, method: str, body: dict) -> dict:
    """Shared by the HTTP route and by in-process callers (orchestrator, certification)."""
    if method not in METHOD_MODELS:
        raise AppError(ErrorCode.METHOD_NOT_SUPPORTED, f"Unknown contract method '{method}'",
                       {"methods": sorted(METHOD_MODELS)})
    connector = get_connector(connector_key)
    req = METHOD_MODELS[method].model_validate(body)
    if method == "validate":
        return connector.validate(req.rendition, req.destination, req.connection)
    if method == "publish":
        return connector.publish(req.rendition, req.destination, req.connection, req.idempotency_key)
    if method == "get_status":
        return connector.get_status(req.platform_post_id, req.connection, req.destination)
    if method == "delete":
        return connector.delete(req.platform_post_id, req.connection, req.destination)
    if method == "fetch_metrics":
        return connector.fetch_metrics(req.platform_post_id, req.connection, req.since, req.destination)
    if method == "refresh_auth":
        return connector.refresh_auth(req.connection, req.destination)
    return connector.build_assist_pack(req.rendition, req.destination)


@router.get("", summary="List connectors and their manifests")
def list_connectors() -> dict:
    return {"connectors": [c.manifest.model_dump() for c in all_connectors().values()]}


@router.get("/{connector_key}/manifest", summary="Connector manifest")
def get_manifest(connector_key: str) -> dict:
    return get_connector(connector_key, check_enabled=False).manifest.model_dump()


@router.post("/{connector_key}/{method}", summary="Call a contract method")
def contract_call(connector_key: str, method: str, body: dict = Body(...)) -> dict:
    from pydantic import ValidationError
    try:
        return call_method(connector_key, method, body)
    except ValidationError as e:
        errs = [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()]
        raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid contract request body", {"errors": errs})
