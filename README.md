# medical-journal

A simple symptom journal for someone who has trouble remembering what to tell
their doctor. They talk (or type) about how they're feeling, a friendly helper
asks a few follow-up questions, and the conversation becomes a dated journal
entry. Before an appointment, one button turns the journal into a one-page
summary to print or show the doctor.

**It's a note-taking tool, not medical advice.** The helper is instructed never
to diagnose, interpret symptoms, or comment on medications. It only records
what was said. The one exception: if someone describes a possible emergency
(chest pain, stroke signs, trouble breathing...), it tells them to call 911.

## What it does

- **Talk**: tap the big button and speak; tap again when finished. The helper
  asks one short question at a time (onset, location, severity, what helps,
  effect on sleep, etc.) and can read its questions out loud. Typing works too.
  An unfinished conversation survives a page refresh.
- **My journal**: every saved entry, with symptoms broken out (where, when,
  how bad, what helps/worsens), medicines or changes mentioned, and questions
  for the doctor. The original conversation is kept with each entry.
- **For my doctor**: pick a date range and get a summary grouped by symptom,
  with all the questions she wanted to ask in one place. Print it or bring it
  up on a phone.

Voice input uses the browser's built-in speech recognition, which works in
Chrome, Edge and Safari (iPhone/iPad included). In Firefox, type or use the
keyboard's microphone key.

## Setup

You need Python 3.10+ and an Anthropic API key (https://console.anthropic.com).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python server.py
```

Open http://localhost:5000.

### Settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Your Claude API key |
| `PATIENT_NAME` | (blank) | Shows "Mom's Symptom Journal" and puts the name on the report |
| `APP_PASSWORD` | (none) | If set, the browser asks for this password (any username) |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Use `HOST=0.0.0.0` to reach it from a phone or tablet on your home Wi-Fi |
| `JOURNAL_DB` | `journal.db` | Where entries are stored (a SQLite file) |
| `JOURNAL_MODEL` | `claude-opus-5-5` | Claude model to use |

### Using it from Mom's phone or tablet

Run it on a computer at home with `HOST=0.0.0.0 APP_PASSWORD=something`, then
open `http://<that-computer's-ip>:5000` on her device and "Add to Home Screen".

Note: browsers only allow microphone access on `https://` sites or
`localhost`. Over plain home Wi-Fi, typing and the keyboard's microphone key
work, but the big talk button needs HTTPS. The easiest way to get HTTPS is to
put it behind something like Tailscale Serve, Cloudflare Tunnel, or a small
cloud host with a certificate. Always set `APP_PASSWORD` when it's reachable
from anywhere other than your own computer.

## Privacy

Entries are stored only in `journal.db` on the machine running the server.
Each conversation is sent to Anthropic's API to generate follow-up questions
and summaries. Back up `journal.db` if the journal matters to you.
