"""Profile CRUD API endpoints."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from dashboard.api.serializers import profile_to_dict
from templates import build_variables, generate_profile_docs
from ui.config import PROFILES_DIR
from ui.services import get_registry

bp = Blueprint("profiles", __name__, url_prefix="/api/profiles")


@bp.route("", methods=["GET"])
def list_profiles():
    reg = get_registry()
    profiles = reg.list_profiles()
    role = request.args.get("role")
    status = request.args.get("status")
    department = request.args.get("department")
    if role:
        profiles = [p for p in profiles if p.role == role]
    if status:
        profiles = [p for p in profiles if p.status == status]
    if department:
        profiles = [p for p in profiles if p.department == department]
    return jsonify([profile_to_dict(p) for p in profiles])


@bp.route("/<name>", methods=["GET"])
def get_profile(name: str):
    try:
        p = get_registry().get_profile(name)
        return jsonify(profile_to_dict(p))
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@bp.route("", methods=["POST"])
def create_profile():
    data = request.get_json(force=True)
    try:
        reg = get_registry()
        reg.create_profile(
            name=data["name"],
            display_name=data.get("display_name", data["name"]),
            role=data["role"],
            parent=data.get("parent"),
            department=data.get("department"),
            description=data.get("description"),
            _skip_onboarding=data.get("skip_onboarding", False),
        )
        p = reg.get_profile(data["name"])

        # Generate template docs for the new profile
        if data.get("generate_docs", True):
            variables = build_variables(
                profile_name=p.profile_name,
                display_name=p.display_name,
                role=p.role,
                parent_profile=p.parent_profile or "",
                department=p.department or "",
                description=p.description or "",
            )
            profile_dir = PROFILES_DIR / p.profile_name
            docs = generate_profile_docs(profile_dir, p.role, variables)
        else:
            docs = []

        result = profile_to_dict(p)
        result["generated_docs"] = docs
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/<name>", methods=["PATCH"])
def update_profile(name: str):
    data = request.get_json(force=True)
    try:
        reg = get_registry()
        reg.update_profile(name, **data)
        p = reg.get_profile(name)
        return jsonify(profile_to_dict(p))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/<name>", methods=["DELETE"])
def delete_profile(name: str):
    try:
        get_registry().delete_profile(name)
        return jsonify({"deleted": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/<name>/activate", methods=["POST"])
def activate_profile(name: str):
    try:
        get_registry().activate(name)
        p = get_registry().get_profile(name)
        return jsonify(profile_to_dict(p))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/<name>/suspend", methods=["POST"])
def suspend_profile(name: str):
    try:
        get_registry().suspend(name)
        p = get_registry().get_profile(name)
        return jsonify(profile_to_dict(p))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/<name>/reports", methods=["GET"])
def list_reports(name: str):
    try:
        reports = get_registry().list_reports(name)
        return jsonify([profile_to_dict(p) for p in reports])
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/<name>/generate-docs", methods=["POST"])
def generate_docs(name: str):
    """Generate or regenerate template docs for an existing profile."""
    data = request.get_json(force=True) if request.is_json else {}
    overwrite = data.get("overwrite", False)
    try:
        p = get_registry().get_profile(name)
        variables = build_variables(
            profile_name=p.profile_name,
            display_name=p.display_name,
            role=p.role,
            parent_profile=p.parent_profile or "",
            department=p.department or "",
            description=p.description or "",
        )
        profile_dir = PROFILES_DIR / p.profile_name
        docs = generate_profile_docs(profile_dir, p.role, variables, overwrite=overwrite)
        return jsonify({"profile": name, "generated": docs, "overwrite": overwrite})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/<name>/generate-ai-docs", methods=["POST"])
def generate_ai_docs(name: str):
    """Generate tailored docs using AI based on a purpose description.

    Body: { "purpose": "what this agent should do", "overwrite": true }
    This runs hermes to generate each doc — takes ~30-60s per doc.
    """
    data = request.get_json(force=True)
    purpose = data.get("purpose", "").strip()
    if not purpose:
        return jsonify({"error": "purpose is required"}), 400

    overwrite = data.get("overwrite", True)
    target_docs = data.get("docs")  # Optional: specific docs to generate

    try:
        from templates.generator import generate_all_docs

        p = get_registry().get_profile(name)
        profile_dir = PROFILES_DIR / p.profile_name

        results = generate_all_docs(
            profile_dir=profile_dir,
            profile_name=p.profile_name,
            display_name=p.display_name,
            role=p.role,
            parent_profile=p.parent_profile or "",
            department=p.department or "",
            purpose=purpose,
            overwrite=overwrite,
            docs=target_docs,
        )

        return jsonify({
            "profile": name,
            "purpose": purpose,
            "results": results,
            "generated": [k for k, v in results.items() if v],
            "failed": [k for k, v in results.items() if not v],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/<name>/chain-of-command", methods=["GET"])
def chain_of_command(name: str):
    try:
        chain = get_registry().get_chain_of_command(name)
        return jsonify([profile_to_dict(p) for p in chain])
    except Exception as e:
        return jsonify({"error": str(e)}), 400
