"""
NOVA MONITOR — Server-Sent Events (SSE) Live Feed
==================================================

Drop this file in the novawatch/ folder alongside app.py.

Usage:
    Register the blueprint in app.py (see bottom of this file for instructions),
    then open /monitor in a browser.

Routes added:
    GET  /monitor                  → serves monitor.html
    GET  /api/monitor/stream       → SSE live event feed
    GET  /api/monitor/snapshot     → DB snapshot (JSON) for initial load
    POST /api/monitor/event        → internal: emit an SSE event (call from app.py)


Architecture:
    - A global in-process pub/sub bus holds a list of SSE queues (one per connected client).
    - Every route in app.py that produces a meaningful event calls monitor.emit(type, **data).
    - The /api/monitor/stream SSE endpoint subscribes a queue, streams events, and
      unsubscribes on disconnect.
    - The /api/monitor/snapshot endpoint hits the DB directly and returns the last N
      rows from each key table so the monitor page can populate on first load.

Integration with app.py:
    At the top of app.py, after the `app` object is created, add:

        from monitor import monitor_bp, monitor
        app.register_blueprint(monitor_bp)

    Then call monitor.emit(...) at the right points inside each route.
    Ready-made emit calls for every route are listed at the bottom of this file.

Security:
    All monitor routes require the MONITOR_SECRET env var to be set.
    Set it to any strong random string; pass it as ?secret=<value> in the browser URL,
    OR set MONITOR_SKIP_AUTH=1 in dev to skip authentication entirely.

    Example secure URL: /monitor?secret=mysecret123
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Any, Iterator

from flask import Blueprint, Response, jsonify, request, send_from_directory, stream_with_context

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONITOR_SECRET: str   = os.getenv("MONITOR_SECRET", "").strip()
MONITOR_SKIP_AUTH: bool = os.getenv("MONITOR_SKIP_AUTH", "0") == "1"

# How many events to buffer per client before dropping the oldest
_CLIENT_QUEUE_MAXSIZE = 200

# Ping interval in seconds — keeps the connection alive through proxies
_PING_INTERVAL = 20

# Snapshot limits
_SNAPSHOT_LIMIT = 20   # rows per table


# ---------------------------------------------------------------------------
# Internal pub/sub bus
# ---------------------------------------------------------------------------

class _MonitorBus:
    """Thread-safe in-process pub/sub bus for SSE clients."""

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._clients: list[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=_CLIENT_QUEUE_MAXSIZE)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def emit(self, event_type: str, **data: Any) -> None:
        """Broadcast an event to all connected SSE clients."""
        payload = json.dumps({"type": event_type, **data}, default=str)
        dead: list[queue.Queue] = []
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                # Drop oldest item and try again — slow clients lose history
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    dead.append(q)
        for q in dead:
            self.unsubscribe(q)


# Singleton — import this in app.py:  from monitor import monitor
monitor = _MonitorBus()


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

monitor_bp = Blueprint(
    "monitor",
    __name__,
    # monitor.html lives in the same folder as app.py / monitor.py
    template_folder=".",
    static_folder="static",
)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_auth() -> bool:
    """Return True if the request is authorised to view the monitor."""
    if MONITOR_SKIP_AUTH:
        return True
    if not MONITOR_SECRET:
        # No secret configured → block access
        return False
    return request.args.get("secret", "") == MONITOR_SECRET or \
           request.headers.get("X-Monitor-Secret", "") == MONITOR_SECRET


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@monitor_bp.get("/monitor")
def monitor_page():
    """Serve the monitor.html dashboard."""
    if not _check_auth():
        return (
            "<h2>403 — Monitor access denied.</h2>"
            "<p>Set MONITOR_SECRET in your environment and pass ?secret=&lt;value&gt; in the URL.</p>",
            403,
        )
    # Serve monitor.html from the project root (same dir as app.py)
    return send_from_directory(".", "monitor.html")


@monitor_bp.get("/api/monitor/stream")
def monitor_stream():
    """
    SSE endpoint — one long-lived HTTP response per browser tab.
    Events are JSON-encoded and sent as `data: {...}\\n\\n`.
    """
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 403

    client_q = monitor.subscribe()

    # Send an immediate CONNECTED event
    monitor.emit("CONNECTED")

    def _generate() -> Iterator[str]:
        last_ping = time.time()
        try:
            while True:
                now = time.time()
                # Ping to keep the connection alive
                if now - last_ping >= _PING_INTERVAL:
                    yield "data: " + json.dumps({"type": "PING"}) + "\n\n"
                    last_ping = now

                # Drain the queue (non-blocking)
                try:
                    payload = client_q.get(timeout=1)
                    yield "data: " + payload + "\n\n"
                except queue.Empty:
                    pass
        except GeneratorExit:
            pass
        finally:
            monitor.unsubscribe(client_q)

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":  "no-cache",
            "X-Accel-Buffering": "no",   # Disable nginx buffering
            "Connection":     "keep-alive",
        },
    )


@monitor_bp.get("/api/monitor/snapshot")
def monitor_snapshot():
    """
    Return a JSON snapshot of recent DB activity so the monitor page can
    populate on first load without waiting for real-time events.

    Imports DatabaseManager lazily to avoid circular-import issues when
    monitor.py is imported before app.py fully initialises.
    """
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 403

    try:
        # Lazy import — app.py defines DatabaseManager in the same package
        from app import DatabaseManager  # noqa: PLC0415
    except ImportError:
        return jsonify({"ok": False, "error": "DatabaseManager not available"}), 500

    try:
        conn = DatabaseManager.get_connection()
        cur  = conn.cursor()

        # ── Aggregate stats ──────────────────────────────────────────────
        cur.execute("SELECT COUNT(*) FROM NAMES")
        total_users = (cur.fetchone() or [0])[0]

        cur.execute("SELECT COUNT(*), COALESCE(SUM(AMOUNT),0) FROM TRANSACTIONS")
        row = cur.fetchone() or [0, 0]
        total_tx    = int(row[0])
        total_coins = int(row[1])

        cur.execute("SELECT COUNT(*) FROM STORE_ORDERS")
        total_orders = (cur.fetchone() or [0])[0]

        cur.execute("SELECT COUNT(*) FROM COIN_PURCHASES WHERE STATUS='SUCCESS'")
        total_purchased = (cur.fetchone() or [0])[0]

        stats = {
            "total_users":     int(total_users),
            "total_tx":        total_tx,
            "total_coins":     total_coins,
            "total_orders":    int(total_orders),
            "total_purchased": int(total_purchased),
        }

        # ── Recent transactions ──────────────────────────────────────────
        cur.execute(
            """SELECT PAYER_CODE_NAME, RECEIVER_CODE_NAME, AMOUNT, CREATED_AT
               FROM TRANSACTIONS ORDER BY CREATED_AT DESC LIMIT %s""",
            (_SNAPSHOT_LIMIT,),
        )
        transactions = [
            {
                "payer_code_name":    r[0],
                "receiver_code_name": r[1],
                "amount":             int(r[2]),
                "created_at":         str(r[3]),
            }
            for r in (cur.fetchall() or [])
        ]

        # ── Recent registrations ─────────────────────────────────────────
        cur.execute(
            """SELECT CODE_NAME, FULL_NAME, EMAIL, CREATED_AT
               FROM NAMES ORDER BY CREATED_AT DESC LIMIT %s""",
            (_SNAPSHOT_LIMIT,),
        )
        users = [
            {
                "code_name":  r[0],
                "full_name":  r[1] or "",
                "email":      r[2] or "",
                "created_at": str(r[3]),
            }
            for r in (cur.fetchall() or [])
        ]

        # ── Recent broadcasts ────────────────────────────────────────────
        cur.execute(
            """SELECT TITLE, BODY, SENT_BY, CREATED_AT
               FROM BROADCASTS ORDER BY CREATED_AT DESC LIMIT %s""",
            (_SNAPSHOT_LIMIT,),
        )
        broadcasts = [
            {
                "title":      r[0],
                "body":       r[1],
                "by":         r[2],
                "created_at": str(r[3]),
            }
            for r in (cur.fetchall() or [])
        ]

        # ── Recent run sessions ──────────────────────────────────────────
        cur.execute(
            """SELECT n.CODE_NAME, rs.DISTANCE_KM, rs.COINS_EARNED, rs.CREATED_AT
               FROM RUN_SESSIONS rs
               JOIN NAMES n ON rs.USER_ID = n.USER_ID
               ORDER BY rs.CREATED_AT DESC LIMIT %s""",
            (_SNAPSHOT_LIMIT,),
        )
        runs = [
            {
                "code_name":   r[0],
                "distance_km": float(r[1]),
                "coins":       int(r[2]),
                "created_at":  str(r[3]),
            }
            for r in (cur.fetchall() or [])
        ]

        # ── Recent coin purchases ────────────────────────────────────────
        cur.execute(
            """SELECT CODE_NAME, COINS, ROUND(AMOUNT_PAISE/100,0) AS INR, INVOICE_NO, CREATED_AT
               FROM COIN_PURCHASES WHERE STATUS='SUCCESS'
               ORDER BY CREATED_AT DESC LIMIT %s""",
            (_SNAPSHOT_LIMIT,),
        )
        purchases = [
            {
                "code_name":  r[0],
                "coins":      int(r[1]),
                "amount_inr": int(r[2] or 0),
                "invoice_no": r[3] or "",
                "created_at": str(r[4]),
            }
            for r in (cur.fetchall() or [])
        ]

        # ── Recent store orders ──────────────────────────────────────────
        cur.execute(
            """SELECT ORDER_NO, CODE_NAME, PRODUCT_NAME, PAYMENT_METHOD,
                      COINS_SPENT, INR_PAID, STATUS, CREATED_AT
               FROM STORE_ORDERS ORDER BY CREATED_AT DESC LIMIT %s""",
            (_SNAPSHOT_LIMIT,),
        )
        orders = [
            {
                "order_no":   r[0],
                "code_name":  r[1],
                "product":    r[2],
                "method":     r[3],
                "coins_spent": float(r[4] or 0),
                "inr_paid":   int(r[5] or 0),
                "status":     r[6],
                "created_at": str(r[7]),
            }
            for r in (cur.fetchall() or [])
        ]

        cur.close()
        conn.close()

        return jsonify({
            "ok":           True,
            "stats":        stats,
            "transactions": transactions,
            "users":        users,
            "broadcasts":   broadcasts,
            "runs":         runs,
            "purchases":    purchases,
            "orders":       orders,
        })

    except Exception as exc:
        logging.exception("[Monitor] Snapshot error")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Convenience emit wrappers
# (import `monitor` from this module and call these from app.py routes)
# ---------------------------------------------------------------------------

def emit_register(full_name: str, code_name: str, email: str, created_at: str = "") -> None:
    monitor.emit(
        "REGISTER",
        full_name=full_name,
        code_name=code_name,
        email=email,
        created_at=created_at or _now(),
    )


def emit_login(code_name: str, method: str = "email") -> None:
    monitor.emit("LOGIN", code_name=code_name, method=method)


def emit_logout(code_name: str) -> None:
    monitor.emit("LOGOUT", code_name=code_name)


def emit_otp(target: str, purpose: str) -> None:
    monitor.emit("OTP", target=target, purpose=purpose)


def emit_coin_send(payer: str, receiver: str, amount: int, created_at: str = "") -> None:
    monitor.emit(
        "COIN_SEND",
        payer=payer,
        receiver=receiver,
        amount=amount,
        created_at=created_at or _now(),
    )
    monitor.emit(
        "COIN_RECEIVE",
        payer=payer,
        receiver=receiver,
        amount=amount,
    )


def emit_qr_generated(code_name: str) -> None:
    monitor.emit("QR_GENERATED", code_name=code_name)


def emit_qr_scanned(code_name: str, receiver: str) -> None:
    monitor.emit("QR_SCANNED", code_name=code_name, receiver=receiver)


def emit_run(code_name: str, distance_km: float, coins: int) -> None:
    monitor.emit("RUN",      code_name=code_name, distance_km=round(distance_km, 3), coins=coins)
    monitor.emit("RUN_EARN", code_name=code_name, distance_km=round(distance_km, 3), coins=coins)


def emit_profile(code_name: str) -> None:
    monitor.emit("PROFILE", code_name=code_name)


def emit_message(from_user: str, to_user: str) -> None:
    monitor.emit("MESSAGE", from_user=from_user, to_user=to_user)


def emit_connect(from_user: str, to_user: str, status: str) -> None:
    monitor.emit("CONNECT", from_user=from_user, to_user=to_user, status=status)


def emit_coin_purchase(code_name: str, coins: int, amount_inr: int, invoice_no: str = "") -> None:
    monitor.emit(
        "COIN_PURCHASE",
        code_name=code_name,
        coins=coins,
        amount_inr=amount_inr,
        invoice_no=invoice_no,
    )


def emit_coin_refund(receiver: str, amount: float) -> None:
    monitor.emit("COIN_REFUND", receiver=receiver, amount=amount)


def emit_store_order_coin(code_name: str, product: str, coins_spent: float, order_no: str) -> None:
    monitor.emit(
        "STORE_ORDER_COIN",
        code_name=code_name,
        product=product,
        coins_spent=coins_spent,
        order_no=order_no,
    )


def emit_store_order_inr(code_name: str, product: str, inr_paid: int, order_no: str) -> None:
    monitor.emit(
        "STORE_ORDER_INR",
        code_name=code_name,
        product=product,
        inr_paid=inr_paid,
        order_no=order_no,
    )


def emit_order_status(order_no: str, code_name: str, status: str) -> None:
    monitor.emit("ORDER_STATUS", order_no=order_no, code_name=code_name, status=status)


def emit_coin_to_cash(code_name: str, coins: float) -> None:
    monitor.emit("COIN_TO_CASH", code_name=code_name, coins=coins, amount=coins)


def emit_contact(name: str, email: str) -> None:
    monitor.emit("CONTACT", name=name, email=email)


def emit_broadcast(title: str, body: str, by: str) -> None:
    monitor.emit("BROADCAST", title=title, body=body, by=by)


def emit_admin(admin: str, action: str, detail: str = "") -> None:
    monitor.emit("ADMIN", admin=admin, action=action, detail=detail)


def emit_admin_coin_adj(admin: str, target: str, amount: float, note: str = "") -> None:
    monitor.emit("ADMIN_COIN_ADJ", admin=admin, target=target, amount=amount, note=note)


def emit_error(route: str, msg: str) -> None:
    monitor.emit("ERROR", route=route, msg=msg)


def _now() -> str:
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Integration guide — paste these emit calls into the matching routes in app.py
# ---------------------------------------------------------------------------
#
# 1. At the very top of app.py (after `app = Flask(...)` and before the routes):
#
#        from monitor import monitor_bp, monitor, emit_register, emit_login, \
#            emit_logout, emit_otp, emit_coin_send, emit_qr_generated, \
#            emit_qr_scanned, emit_run, emit_profile, emit_message, emit_connect, \
#            emit_coin_purchase, emit_coin_refund, emit_store_order_coin, \
#            emit_store_order_inr, emit_order_status, emit_coin_to_cash, \
#            emit_contact, emit_broadcast, emit_admin, emit_admin_coin_adj, \
#            emit_error
#        app.register_blueprint(monitor_bp)
#
# 2. Route-by-route emit calls:
#
#    api_register         → emit_register(full_name, code_name, email)
#    api_login            → emit_login(code_name, "email")
#    api_google_login     → emit_login(code_name, "google")
#    api_logout           → emit_logout(code_name)
#    api_otp_send_email   → emit_otp(email, purpose)
#    api_transaction_pay  → emit_coin_send(payer_code_name, receiver_code_name, amount)
#    api_qr_mine          → emit_qr_generated(code_name)
#    api_qr_verify        → emit_qr_scanned(code_name, receiver_code_name)
#    api_run_earn         → emit_run(code_name, valid_km, coins_to_award)
#    api_profile_save     → emit_profile(code_name)
#    api_msg_send         → emit_message(from_code_name, to_code_name)
#    api_connect_request  → emit_connect(from_code_name, to_code_name, "REQUESTED")
#    api_connect_respond  → emit_connect(from_code_name, to_code_name, status)
#    api_payment_verify   → emit_coin_purchase(code_name, coins, amount_inr, invoice_no)
#    api_store_order_coin → emit_store_order_coin(code_name, product_name, coins_spent, order_no)
#    api_store_rz_verify  → emit_store_order_inr(code_name, product_name, inr_paid, order_no)
#    api_admin_update_order → emit_order_status(order_no, code_name, status)
#    api_coin_to_cash_request → emit_coin_to_cash(code_name, coins)
#    api_contact          → emit_contact(name, email)
#    api_admin_broadcast  → emit_broadcast(title, body, admin_code_name)
#    api_admin_add_product    → emit_admin(admin, "ADD_PRODUCT", product_name)
#    api_admin_edit_product   → emit_admin(admin, "EDIT_PRODUCT", product_id)
#    api_admin_delete_product → emit_admin(admin, "DELETE_PRODUCT", product_id)
#    api_admin_adjust_coins   → emit_admin_coin_adj(admin, target, amount, note)
#    api_admin_delete_user    → emit_admin(admin, "DELETE_USER", code_name)
#    api_admin_drop_all_tables → emit_admin(admin, "DROP_ALL_TABLES", "DANGER")
