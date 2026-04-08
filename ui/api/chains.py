"""Delegation chain endpoints."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from core.integration.delegation import ChainStatus
from ui.services import get_chain_store

bp = Blueprint("chains", __name__, url_prefix="/api/chains")


def _chain_to_dict(chain) -> dict:
    """Serialize a DelegationChain to a dict."""
    return {
        "chain_id": chain.chain_id,
        "task_description": chain.task_description,
        "originator": chain.originator,
        "status": chain.status.value if hasattr(chain.status, "value") else str(chain.status),
        "created_at": chain.created_at.isoformat() if chain.created_at else None,
        "completed_at": chain.completed_at.isoformat() if hasattr(chain, "completed_at") and chain.completed_at else None,
        "hops": [_hop_to_dict(h) for h in chain.hops],
        "workers": chain.workers,
        "worker_results": chain.worker_results,
    }


def _hop_to_dict(hop) -> dict:
    return {
        "from_profile": hop.from_profile,
        "to_profile": hop.to_profile,
        "status": hop.status.value if hasattr(hop.status, "value") else str(hop.status),
        "message_id": hop.message_id,
        "delegated_at": hop.delegated_at.isoformat() if hasattr(hop, "delegated_at") and hop.delegated_at else None,
        "completed_at": hop.completed_at.isoformat() if hasattr(hop, "completed_at") and hop.completed_at else None,
    }


@bp.route("", methods=["GET"])
def list_chains():
    store = get_chain_store()
    status_filter = request.args.get("status")
    originator = request.args.get("originator")

    kwargs = {}
    if status_filter:
        try:
            kwargs["status"] = ChainStatus(status_filter)
        except ValueError:
            pass
    if originator:
        kwargs["originator"] = originator

    chains = store.list(**kwargs)
    return jsonify([_chain_to_dict(c) for c in chains])


@bp.route("/<chain_id>", methods=["GET"])
def get_chain(chain_id: str):
    try:
        chain = get_chain_store().get(chain_id)
        return jsonify(_chain_to_dict(chain))
    except Exception as e:
        return jsonify({"error": str(e)}), 404
