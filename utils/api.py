import os
import json
import datetime
from functools import wraps
from flask import Blueprint, jsonify, request

from utils.warnings_manager import load_warnings, save_warnings, add_warning, clear_warnings
from utils.cmd_access import get_allowed, add_user, remove_user

api = Blueprint("api", __name__)

_bot_ref = None

def set_bot(bot):
    global _bot_ref
    _bot_ref = bot

def _auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        expected = os.environ.get("API_KEY", "")
        if not expected or key != expected:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def _load_q():
    path = "data/quiz_questions.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def _save_q(data):
    os.makedirs("data", exist_ok=True)
    with open("data/quiz_questions.json", "w") as f:
        json.dump(data, f, indent=2)

def _load_ai_access():
    path = "ai_access.json"
    if os.path.exists(path):
        try:
            with open(path) as f:
                return set(json.load(f).get("allowed", []))
        except Exception:
            pass
    return set()

def _save_ai_access(allowed: set):
    with open("ai_access.json", "w") as f:
        json.dump({"allowed": list(allowed)}, f, indent=2)

def _load_welcome():
    path = "welcome_config.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def _save_welcome(data):
    with open("welcome_config.json", "w") as f:
        json.dump(data, f, indent=2)

# ── status ─────────────────────────────────────────────────────────────────────

@api.route("/api/status")
@_auth
def status():
    bot = _bot_ref
    if bot is None or not bot.is_ready():
        return jsonify({"online": False})
    uptime_secs = int((datetime.datetime.now(datetime.timezone.utc) - bot.start_time).total_seconds())
    quiz_cog = bot.cogs.get("QuizCog")
    active_sessions = []
    if quiz_cog:
        for gid, s in quiz_cog.sessions.items():
            active_sessions.append({
                "guild_id": gid,
                "category": s.get("category"),
                "mode": s.get("mode"),
                "state": s.get("state"),
                "players": len(s.get("players", set())),
            })
    return jsonify({
        "online": True,
        "username": str(bot.user),
        "latency_ms": round(bot.latency * 1000),
        "uptime_seconds": uptime_secs,
        "guild_count": len(bot.guilds),
        "active_quiz_sessions": active_sessions,
    })

# ── quiz: categories ───────────────────────────────────────────────────────────

@api.route("/api/quiz/categories")
@_auth
def quiz_categories():
    data = _load_q()
    result = {}
    for cat, qs in sorted(data.items()):
        mc    = sum(1 for q in qs if q.get("type", "mc") == "mc")
        typed = len(qs) - mc
        pts   = sum(q.get("points", 1) for q in qs)
        result[cat] = {"total": len(qs), "mc": mc, "typed": typed, "total_points": pts}
    return jsonify(result)

@api.route("/api/quiz/categories/<category>", methods=["DELETE"])
@_auth
def delete_category(category):
    cat  = category.lower()
    data = _load_q()
    if cat not in data:
        return jsonify({"error": f"Category '{cat}' not found"}), 404
    count = len(data.pop(cat))
    _save_q(data)
    return jsonify({"deleted": cat, "questions_removed": count})

# ── quiz: questions ────────────────────────────────────────────────────────────

@api.route("/api/quiz/questions/<category>")
@_auth
def quiz_questions(category):
    cat  = category.lower()
    data = _load_q()
    pool = data.get(cat)
    if pool is None:
        return jsonify({"error": f"Category '{cat}' not found"}), 404
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(50, int(request.args.get("per_page", 20)))
    total    = len(pool)
    start    = (page - 1) * per_page
    chunk    = pool[start: start + per_page]
    return jsonify({
        "category": cat,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "questions": chunk,
    })

@api.route("/api/quiz/questions/<category>/search")
@_auth
def quiz_search(category):
    cat     = category.lower() if category != "all" else None
    keyword = request.args.get("q", "").lower()
    kind    = request.args.get("type")
    pts     = request.args.get("points")
    data    = _load_q()
    cats    = [cat] if cat else list(data.keys())
    results = []
    for c in cats:
        for q in data.get(c, []):
            if keyword and keyword not in q.get("question", "").lower():
                continue
            if kind and q.get("type", "mc") != kind:
                continue
            if pts and str(q.get("points", 1)) != pts:
                continue
            results.append({**q, "category": c})
    return jsonify({"results": results, "total": len(results)})

@api.route("/api/quiz/questions", methods=["POST"])
@_auth
def add_question():
    body = request.get_json(force=True)
    if not body:
        return jsonify({"error": "No JSON body"}), 400
    cat  = str(body.get("category", "")).lower().strip()
    qtype = str(body.get("type", "mc")).lower()
    if not cat:
        return jsonify({"error": "category is required"}), 400
    data = _load_q()
    data.setdefault(cat, [])
    if len(data[cat]) >= 500:
        return jsonify({"error": f"Category '{cat}' is full (500 max)"}), 400
    qid = max((q["id"] for q in data[cat]), default=0) + 1
    if qtype == "mc":
        ans = str(body.get("answer", "A")).upper()
        if ans not in ("A", "B", "C", "D"):
            return jsonify({"error": "answer must be A, B, C, or D"}), 400
        q = {
            "id": qid, "type": "mc",
            "question": str(body.get("question", "")),
            "a": str(body.get("a", "")), "b": str(body.get("b", "")),
            "c": str(body.get("c", "")), "d": str(body.get("d", "")),
            "answer": ans, "category": cat,
            "points": max(1, min(2, int(body.get("points", 1)))),
        }
    elif qtype == "typed":
        answers = body.get("answers", [])
        if isinstance(answers, str):
            answers = [a.strip() for a in answers.split(";") if a.strip()]
        if not answers:
            return jsonify({"error": "answers list is required for typed questions"}), 400
        q = {
            "id": qid, "type": "typed",
            "question": str(body.get("question", "")),
            "answers": answers, "category": cat,
            "points": max(1, min(2, int(body.get("points", 1)))),
        }
    else:
        return jsonify({"error": "type must be 'mc' or 'typed'"}), 400
    data[cat].append(q)
    _save_q(data)
    return jsonify({"added": q}), 201

@api.route("/api/quiz/questions/<category>/<int:question_id>", methods=["DELETE"])
@_auth
def delete_question(category, question_id):
    cat  = category.lower()
    data = _load_q()
    pool = data.get(cat, [])
    new  = [q for q in pool if q["id"] != question_id]
    if len(new) == len(pool):
        return jsonify({"error": f"Question #{question_id} not found in '{cat}'"}), 404
    data[cat] = new
    _save_q(data)
    return jsonify({"deleted": question_id, "category": cat})

# ── warnings ───────────────────────────────────────────────────────────────────

@api.route("/api/warnings")
@_auth
def all_warnings():
    return jsonify(load_warnings())

@api.route("/api/warnings/<int:guild_id>/<int:user_id>")
@_auth
def user_warnings(guild_id, user_id):
    data = load_warnings()
    warns = data.get(str(guild_id), {}).get(str(user_id), [])
    return jsonify({"guild_id": guild_id, "user_id": user_id, "warnings": warns, "count": len(warns)})

@api.route("/api/warnings/<int:guild_id>/<int:user_id>", methods=["DELETE"])
@_auth
def clear_user_warnings(guild_id, user_id):
    count = clear_warnings(guild_id, user_id)
    return jsonify({"cleared": count, "guild_id": guild_id, "user_id": user_id})

# ── welcome ────────────────────────────────────────────────────────────────────

@api.route("/api/welcome")
@_auth
def get_welcome():
    return jsonify(_load_welcome())

@api.route("/api/welcome/<int:guild_id>", methods=["POST"])
@_auth
def update_welcome(guild_id):
    body = request.get_json(force=True) or {}
    data = _load_welcome()
    gid  = str(guild_id)
    data.setdefault(gid, {})
    if "channel_id" in body: data[gid]["channel_id"] = int(body["channel_id"])
    if "message"    in body: data[gid]["message"]    = str(body["message"])
    if "enabled"    in body: data[gid]["enabled"]    = bool(body["enabled"])
    if "color"      in body: data[gid]["color"]      = int(body["color"])
    _save_welcome(data)
    return jsonify({"updated": data[gid]})

# ── AI access ─────────────────────────────────────────────────────────────────

@api.route("/api/ai/access")
@_auth
def ai_access_list():
    allowed = _load_ai_access()
    cmd_allowed = get_allowed()
    bot = _bot_ref
    def resolve(uid):
        if bot:
            for guild in bot.guilds:
                m = guild.get_member(uid)
                if m:
                    return {"id": uid, "name": m.display_name, "avatar": str(m.display_avatar.url)}
        return {"id": uid, "name": str(uid), "avatar": None}
    return jsonify({
        "ai_access": [resolve(uid) for uid in allowed],
        "cmd_access": [resolve(uid) for uid in cmd_allowed],
    })

@api.route("/api/ai/access/<int:user_id>", methods=["POST"])
@_auth
def grant_ai_access(user_id):
    allowed = _load_ai_access()
    allowed.add(user_id)
    _save_ai_access(allowed)
    return jsonify({"granted": user_id})

@api.route("/api/ai/access/<int:user_id>", methods=["DELETE"])
@_auth
def revoke_ai_access(user_id):
    allowed = _load_ai_access()
    allowed.discard(user_id)
    _save_ai_access(allowed)
    return jsonify({"revoked": user_id})
