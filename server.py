"""Symptom Journal: a small web app that helps someone talk through how they
feel, asks gentle follow-up questions, and turns the conversation into a
dated log entry and a printable summary for their doctor.

This is a note-taking helper, not a source of medical advice.

Run:  ANTHROPIC_API_KEY=... python server.py   then open http://localhost:5000
"""

import json
import os
import sqlite3
from datetime import date, datetime
from functools import wraps

import anthropic
from flask import Flask, Response, g, jsonify, request, send_from_directory

MODEL = os.environ.get("JOURNAL_MODEL", "claude-opus-5-5")
DB_PATH = os.environ.get("JOURNAL_DB", os.path.join(os.path.dirname(__file__), "journal.db"))
APP_PASSWORD = os.environ.get("APP_PASSWORD")  # optional; enables a simple login prompt
PATIENT_NAME = os.environ.get("PATIENT_NAME", "")

app = Flask(__name__, static_folder="static")
client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SAFETY_RULES = """\
You are NOT a doctor and you never give medical advice. Never diagnose, never
suggest what a symptom might mean, never recommend, change, or comment on
medications, doses or treatments, and never reassure that something is "fine"
or "nothing to worry about". If asked for an opinion, kindly say that's a great
question to write down for the doctor, and offer to add it to the list.

The one exception: if the person describes something that may be an emergency
(for example chest pain or pressure, trouble breathing, signs of a stroke such
as face drooping, arm weakness or slurred speech, fainting, severe bleeding, a
sudden severe headache, or thoughts of harming themselves), gently but clearly
tell them to call 911 (or their local emergency number) now, before anything
else."""

INTERVIEW_SYSTEM = f"""\
You help an older adult keep a symptom journal so she remembers what to tell
her doctor. She talks (often by voice, so expect run-on sentences and speech-
to-text mistakes) and you ask short, kind follow-up questions so the journal
entry has the details a doctor usually wants.

How to talk:
- Warm, plain, everyday words. No medical jargon.
- Ask ONE question at a time, and keep each reply to one or two short
  sentences. Your reply may be read aloud.
- Briefly acknowledge what she said before asking the next question.
- Don't repeat questions she already answered.

Useful details to gather, only when relevant to what she raised (skip what
doesn't apply, don't interrogate):
- what exactly she is feeling and where in the body
- when it started, and whether it comes and goes or is constant
- how bad it is (0 to 10, or "mild / medium / bad")
- what makes it better or worse; anything she tried
- what she was doing when it happened (eating, walking, lying down...)
- how it affects daily life, sleep, appetite, mood
- any new medicines, missed doses, falls, or other changes recently
- anything she wants to ask the doctor

After about 4 to 8 follow-ups, or when she seems tired or done, ask if there
is anything else. When she has nothing else, thank her and tell her she can
press "Save to my journal". Set ready_to_save to true at that point.

{SAFETY_RULES}

Set emergency to true only when you are telling her to call 911."""

ENTRY_SYSTEM = f"""\
You turn a conversation between a patient and a note-taking helper into a
clear, factual journal entry that the patient can show her doctor.

- Record only what the patient actually said. Don't add interpretations,
  possible causes, or advice. Don't invent details; leave a field empty if it
  wasn't mentioned.
- Write the summary in the first person, as if the patient wrote it ("My left
  knee has been aching since Tuesday..."), in plain language, 2 to 5
  sentences.
- Clean up obvious speech-to-text mistakes when the meaning is clear.
- Resolve relative dates ("yesterday", "last Tuesday") against the entry date
  given to you, and write them as dates when you can.

{SAFETY_RULES}"""

REPORT_SYSTEM = f"""\
You prepare a concise, well-organized summary of a patient's own symptom
journal for her to bring to a doctor's appointment. A busy doctor should be
able to read it in two minutes.

- Report only what is in the journal entries. Do not diagnose, speculate on
  causes, or suggest treatments.
- Group by symptom. For each: when it was first noted, how often it came up,
  severity range, patterns or triggers she mentioned, and whether it seems to
  be getting better, worse or staying the same based on her own words.
- Mention dates. Keep bullets short and factual.
- Collect every question she wanted to ask the doctor, de-duplicated.
- Note any medicines or changes she mentioned, exactly as she described them.

{SAFETY_RULES}"""

# ---------------------------------------------------------------------------
# Structured output schemas
# ---------------------------------------------------------------------------

INTERVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "ready_to_save": {"type": "boolean"},
        "emergency": {"type": "boolean"},
    },
    "required": ["reply", "ready_to_save", "emergency"],
    "additionalProperties": False,
}

ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short title, e.g. 'Dizzy after standing up'"},
        "summary": {"type": "string"},
        "symptoms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "body_area": {"type": "string"},
                    "started": {"type": "string"},
                    "how_often": {"type": "string"},
                    "severity": {"type": "string", "description": "As she described it, e.g. '6/10' or 'mild'"},
                    "better_with": {"type": "string"},
                    "worse_with": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["name", "body_area", "started", "how_often", "severity",
                             "better_with", "worse_with", "notes"],
                "additionalProperties": False,
            },
        },
        "daily_life": {"type": "string", "description": "Effects on sleep, appetite, mood, activity"},
        "medications_or_changes": {"type": "array", "items": {"type": "string"}},
        "questions_for_doctor": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "symptoms", "daily_life",
                 "medications_or_changes", "questions_for_doctor"],
    "additionalProperties": False,
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string", "description": "2-3 sentence plain summary of the period"},
        "symptoms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "details": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "details"],
                "additionalProperties": False,
            },
        },
        "medications_or_changes": {"type": "array", "items": {"type": "string"}},
        "questions_for_doctor": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overview", "symptoms", "medications_or_changes", "questions_for_doctor"],
    "additionalProperties": False,
}


class ModelDeclined(Exception):
    pass


def ask_claude(system, messages, schema, effort):
    """One structured-output call. Returns the parsed JSON object."""
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        # If a safety classifier declines, retry on Anthropic's recommended fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise ModelDeclined()
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def transcript_text(messages):
    lines = []
    for m in messages:
        who = "Patient" if m["role"] == "user" else "Helper"
        lines.append(f"{who}: {m['content']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute(
            """CREATE TABLE IF NOT EXISTS entries (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   entry_date TEXT NOT NULL,
                   created_at TEXT NOT NULL,
                   data TEXT NOT NULL,
                   transcript TEXT NOT NULL
               )"""
        )
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def row_to_entry(row):
    return {
        "id": row["id"],
        "entry_date": row["entry_date"],
        "created_at": row["created_at"],
        **json.loads(row["data"]),
        "transcript": json.loads(row["transcript"]),
    }


# ---------------------------------------------------------------------------
# Optional password protection (HTTP basic auth; any username works)
# ---------------------------------------------------------------------------

@app.before_request
def require_password():
    if not APP_PASSWORD or request.path == "/healthz":
        return None
    auth = request.authorization
    if auth and auth.password == APP_PASSWORD:
        return None
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="Symptom Journal"'})


def api_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ModelDeclined:
            return jsonify(error="Sorry, I couldn't help with that one. Please try saying it a different way."), 502
        except anthropic.APIConnectionError:
            return jsonify(error="I can't reach the internet right now. Please try again in a minute."), 503
        except anthropic.RateLimitError:
            return jsonify(error="I'm a little busy right now. Please wait a moment and try again."), 503
        except anthropic.APIStatusError as e:
            app.logger.exception("Claude API error")
            return jsonify(error=f"Something went wrong talking to the assistant ({e.status_code})."), 502
    return wrapper


def clean_messages(raw):
    msgs = []
    for m in raw or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    return msgs


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/api/config")
def config():
    return jsonify(patient_name=PATIENT_NAME)


@app.post("/api/chat")
@api_errors
def chat():
    body = request.get_json(force=True)
    messages = clean_messages(body.get("messages"))
    if not messages or messages[-1]["role"] != "user":
        return jsonify(error="Please say or type something first."), 400
    today = body.get("entry_date") or date.today().isoformat()
    # Ground the model with today's date as the first user turn's preamble.
    convo = [dict(m) for m in messages]
    convo[0]["content"] = f"(Today is {today}.)\n\n{convo[0]['content']}"
    result = ask_claude(INTERVIEW_SYSTEM, convo, INTERVIEW_SCHEMA, effort="low")
    return jsonify(result)


@app.post("/api/entries")
@api_errors
def create_entry():
    body = request.get_json(force=True)
    messages = clean_messages(body.get("messages"))
    if not any(m["role"] == "user" for m in messages):
        return jsonify(error="There's nothing to save yet."), 400
    entry_date = body.get("entry_date") or date.today().isoformat()
    prompt = (
        f"Entry date: {entry_date}\n\nConversation:\n<conversation>\n"
        f"{transcript_text(messages)}\n</conversation>\n\nWrite the journal entry."
    )
    data = ask_claude(ENTRY_SYSTEM, [{"role": "user", "content": prompt}], ENTRY_SCHEMA, effort="medium")
    cur = db().execute(
        "INSERT INTO entries (entry_date, created_at, data, transcript) VALUES (?, ?, ?, ?)",
        (entry_date, datetime.now().isoformat(timespec="seconds"), json.dumps(data), json.dumps(messages)),
    )
    db().commit()
    row = db().execute("SELECT * FROM entries WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_entry(row))


@app.get("/api/entries")
def list_entries():
    rows = db().execute("SELECT * FROM entries ORDER BY entry_date DESC, id DESC").fetchall()
    return jsonify([row_to_entry(r) for r in rows])


@app.delete("/api/entries/<int:entry_id>")
def delete_entry(entry_id):
    db().execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/report")
@api_errors
def report():
    body = request.get_json(force=True)
    start = body.get("start") or "0000-01-01"
    end = body.get("end") or "9999-12-31"
    rows = db().execute(
        "SELECT * FROM entries WHERE entry_date BETWEEN ? AND ? ORDER BY entry_date, id", (start, end)
    ).fetchall()
    if not rows:
        return jsonify(error="There are no journal entries in those dates."), 400
    entries = []
    for r in rows:
        e = row_to_entry(r)
        e.pop("transcript")
        entries.append(e)
    prompt = (
        f"Journal entries from {rows[0]['entry_date']} to {rows[-1]['entry_date']}:\n"
        f"<entries>\n{json.dumps(entries, indent=1)}\n</entries>\n\nWrite the summary for the doctor."
    )
    data = ask_claude(REPORT_SYSTEM, [{"role": "user", "content": prompt}], REPORT_SCHEMA, effort="medium")
    data["start"] = rows[0]["entry_date"]
    data["end"] = rows[-1]["entry_date"]
    data["entry_count"] = len(rows)
    data["entries"] = entries
    return jsonify(data)


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
