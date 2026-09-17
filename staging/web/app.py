from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from staging.db import db_session, fetch_all
from staging.config import VIASVET_PHOTOS_DIR
from staging.moderation.discrepancies import (
    PRODUCT_FIELDS,
    STATUSES,
    list_discrepancies,
    pending_summary,
    resolve as resolve_discrepancies,
)
from staging.moderation.service import (
    approve_queue_item,
    enqueue_supplier_products,
    get_dashboard_stats,
    get_queue_item,
    list_queue_items,
    reject_queue_item,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
    app.secret_key = "svetoyar-staging-moderation-dev"

    allowed_roots = [
        Path(VIASVET_PHOTOS_DIR).resolve(),
    ]

    @app.route("/media/local")
    def serve_local():
        raw_path = request.args.get("path", "")
        if not raw_path:
            abort(400)
        file_path = Path(raw_path).resolve()
        if not any(str(file_path).startswith(str(root)) for root in allowed_roots):
            abort(403)
        if not file_path.is_file():
            abort(404)
        return send_file(file_path)

    @app.route("/")
    def dashboard():
        with db_session() as conn:
            stats = get_dashboard_stats(conn)
            stats["discrepancies_pending"] = sum(row["cnt"] for row in pending_summary(conn))
        return render_template("dashboard.html", stats=stats)

    def _discrepancy_filters(source) -> dict:
        """Filters from a request, with anything unknown dropped rather than trusted."""
        field = source.get("field") or None
        return {
            "supplier_code": source.get("supplier") or None,
            "field_name": field if field in PRODUCT_FIELDS else None,
            "search": (source.get("search") or "").strip() or None,
        }

    @app.route("/discrepancies")
    def discrepancy_list():
        status = request.args.get("status", "pending")
        status = status if status in STATUSES else "pending"
        filters = _discrepancy_filters(request.args)
        try:
            page = max(int(request.args.get("page", 1)), 1)
        except ValueError:
            page = 1
        per_page = 50

        with db_session() as conn:
            items, total = list_discrepancies(conn, status=status, page=page, per_page=per_page, **filters)
            summary = pending_summary(conn)
            suppliers = fetch_all(conn, "SELECT code, name FROM suppliers ORDER BY name")

        return render_template(
            "discrepancies.html",
            items=items,
            total=total,
            status=status,
            supplier=filters["supplier_code"] or "",
            field=filters["field_name"] or "",
            search=filters["search"] or "",
            page=page,
            pages=max((total + per_page - 1) // per_page, 1),
            summary=summary,
            suppliers=suppliers,
            fields=list(PRODUCT_FIELDS),
            resolved=request.args.get("resolved"),
            resolved_action=request.args.get("action"),
        )

    @app.post("/discrepancies/resolve")
    def discrepancy_resolve():
        action = request.form.get("action")
        if action not in ("accept", "reject"):
            abort(400)
        reviewer = request.form.get("reviewer", "moderator").strip() or "moderator"
        filters = _discrepancy_filters(request.form)

        with db_session() as conn:
            if request.form.get("scope") == "filter":
                # Everything pending that matches the filters on screen.
                count = resolve_discrepancies(conn, action=action, reviewer=reviewer, **filters)
            else:
                ids = [int(value) for value in request.form.getlist("ids") if value.isdigit()]
                count = resolve_discrepancies(conn, action=action, reviewer=reviewer, ids=ids)

        return redirect(url_for(
            "discrepancy_list",
            status="pending",
            supplier=filters["supplier_code"] or "",
            field=filters["field_name"] or "",
            search=filters["search"] or "",
            resolved=count,
            action=action,
        ))

    @app.route("/queue")
    def queue_list():
        status = request.args.get("status", "pending")
        supplier = request.args.get("supplier") or None
        search = request.args.get("search") or None
        reason = request.args.get("reason") or None
        page = max(int(request.args.get("page", 1)), 1)

        with db_session() as conn:
            items, total = list_queue_items(
                conn,
                status=status,
                supplier_code=supplier,
                page=page,
                per_page=20,
                search=search,
                queue_reason=reason,
            )
            stats = get_dashboard_stats(conn)

        pages = max((total + 19) // 20, 1)
        return render_template(
            "queue.html",
            items=items,
            stats=stats,
            status=status,
            supplier=supplier or "",
            search=search or "",
            reason=reason or "",
            page=page,
            pages=pages,
            total=total,
        )

    @app.route("/review/<int:queue_id>")
    def review_item(queue_id: int):
        with db_session() as conn:
            item = get_queue_item(conn, queue_id)
        if not item:
            abort(404)
        return render_template("review.html", item=item)

    @app.post("/review/<int:queue_id>/approve")
    def approve_item(queue_id: int):
        reviewer = request.form.get("reviewer", "moderator").strip() or "moderator"
        notes = request.form.get("notes", "").strip() or None
        with db_session() as conn:
            ok = approve_queue_item(conn, queue_id, reviewer=reviewer, notes=notes)
        if not ok:
            abort(400)
        return redirect(url_for("queue_list", status="pending"))

    @app.post("/review/<int:queue_id>/reject")
    def reject_item(queue_id: int):
        reviewer = request.form.get("reviewer", "moderator").strip() or "moderator"
        notes = request.form.get("notes", "").strip() or None
        with db_session() as conn:
            ok = reject_queue_item(conn, queue_id, reviewer=reviewer, notes=notes)
        if not ok:
            abort(400)
        return redirect(url_for("queue_list", status="pending"))

    @app.post("/enqueue")
    def enqueue_form():
        supplier = request.form.get("supplier") or None
        limit_raw = request.form.get("limit", "50")
        limit = int(limit_raw) if limit_raw else 50
        with db_session() as conn:
            stats = enqueue_supplier_products(conn, supplier_code=supplier, limit=limit)
        return render_template(
            "enqueue_result.html",
            stats=stats,
            supplier=supplier or "all",
            limit=limit,
        )

    return app
