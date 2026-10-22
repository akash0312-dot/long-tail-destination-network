"""Discovers connectors: every folder under app/connectors with a manifest.yaml and a connector.py."""
import importlib
import logging
from pathlib import Path

from app.config import get_settings
from app.connectors.base import BaseConnector
from app.connectors.manifest import load_manifest
from app.errors import AppError, ErrorCode

log = logging.getLogger("app.connectors.registry")
_connectors: dict[str, BaseConnector] | None = None


def _discover() -> dict[str, BaseConnector]:
    root = Path(__file__).parent
    found: dict[str, BaseConnector] = {}
    for manifest_path in sorted(root.glob("*/manifest.yaml")):
        manifest = load_manifest(manifest_path)
        if manifest.key != manifest_path.parent.name:
            raise ValueError(f"manifest key '{manifest.key}' must match folder '{manifest_path.parent.name}'")
        module = importlib.import_module(f"app.connectors.{manifest.key}.connector")
        found[manifest.key] = module.Connector(manifest)
    log.info("connectors loaded", extra={"connectors": sorted(found)})
    return found


def all_connectors() -> dict[str, BaseConnector]:
    global _connectors
    if _connectors is None:
        _connectors = _discover()
    return _connectors


def get_connector(key: str, *, check_enabled: bool = True) -> BaseConnector:
    conns = all_connectors()
    if key not in conns:
        raise AppError(ErrorCode.CONNECTOR_NOT_FOUND, f"Unknown connector '{key}'", {"available": sorted(conns)})
    if check_enabled and key in get_settings().disabled_connector_set():
        raise AppError(ErrorCode.CONNECTOR_DISABLED, f"Connector '{key}' is disabled by the kill switch",
                       {"connector": key})
    return conns[key]
