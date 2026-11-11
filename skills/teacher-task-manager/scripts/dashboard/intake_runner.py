"""Student DM intake rounds on this PC (MAIN §7.13, D15, D16).

The server reads the students' DMs and keeps the text rules. This PC only asks for a round, then
sends each clear item to Gemini with this PC's key and hands the reply back, one item at a time.
Nothing is sent to a student. A lost apply answer is replayed once with the same request ID (C7).
"""
import uuid

from dashboard.attendance_ai import _call, _default_transport, _request_allowed, _send_to_gemini

POLL_ROUTE = "/v1/data/intake/poll"
NEXT_ROUTE = "/v1/data/intake/next"
APPLY_ROUTE = "/v1/data/ai/apply"
MAX_ITEMS = 30


def run_intake_round(data_call, get_gemini_key, reason, transport=None, sleep=None):
    """One round: poll, then process pending items while a Gemini key is on this PC."""
    import time
    transport = transport or _default_transport
    sleep = sleep or time.sleep
    poll = _call(data_call, POLL_ROUTE, {"requestId": uuid.uuid4().hex, "reason": reason})
    summary = {"poll": (poll or {}).get("body", {}).get("status") if poll else "unanswered", "applied": 0, "review": 0}
    try:
        key = str(get_gemini_key() or "").strip()
    except Exception:  # noqa: BLE001 - no key: items wait as pending for a PC that has one (D16).
        key = ""
    if not key:
        summary["key"] = "missing"
        return summary
    for _ in range(MAX_ITEMS):
        nxt = _call(data_call, NEXT_ROUTE, {"requestId": uuid.uuid4().hex})
        answer = (nxt or {}).get("body") or {}
        if not nxt or nxt.get("status") != 200:
            break
        if answer.get("status") == "review":
            summary["review"] += 1
            continue
        if answer.get("status") != "claimed":
            break
        request, input_id, item_id = answer.get("request"), answer.get("inputId"), answer.get("itemId")
        if not _request_allowed(request) or not isinstance(input_id, str) or not isinstance(item_id, str):
            break
        sends_left = answer.get("sendsLeft") if isinstance(answer.get("sendsLeft"), int) else 0
        status, reply, used = _send_to_gemini(request, key, sends_left, transport, sleep)
        body = {"requestId": uuid.uuid4().hex, "inputId": input_id, "itemId": item_id, "httpStatus": status,
                "body": reply, "sendsUsed": used}
        if isinstance(answer.get("year"), int):
            body["year"] = answer["year"]
        applied = _call(data_call, APPLY_ROUTE, body) or _call(data_call, APPLY_ROUTE, body)
        if not applied:
            break  # No answer twice: the item's lease runs out and a later round takes it again.
        result = (applied.get("body") or {}).get("status")
        summary["applied" if result == "applied" else "review"] += 1
    return summary
