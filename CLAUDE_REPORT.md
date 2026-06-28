# MindReader — Codebase Audit Report

**Audited by:** Claude Sonnet 4.6 (Senior Software Architect mode)
**Date:** 2026-06-28

---

## 1. Project Overview

**Purpose:** Real-time EEG brain-state monitoring app using a Muse S headset (OSC). Provides a Flask web server with login/register, WebSocket streaming of live EEG metrics (valence, arousal, engagement, flow, SSVEP), a Chart.js dashboard UI, and SQLite session logging.

**Tech Stack:**
- Python / Flask / Flask-Login / Flask-SQLAlchemy / Flask-CORS
- `pythonosc` (OSC receiver), `scipy` (FFT), `numpy`
- `websockets` (standalone WebSocket server on port 8765)
- SQLite (mind_reader.db)
- Vanilla JS / Chart.js / Tailwind CDN (no build step)
- Standalone `mind_reader.py` also exists (matplotlib desktop variant)

**Architecture:**
```
web_app.py
  ├── Flask HTTP server (:5005)   — auth, export, static HTML
  ├── websockets server (:8765)   — real-time EEG stream
  └── pythonosc UDP server (:5239)— Muse S EEG input

mind_reader.py (standalone matplotlib variant, separate entry point)
```

---

## 2. Issues Found

### CRITICAL (FIXED)

#### C-1: ~~Triple `return metrics` statement — dead code after first return~~
**File:** `web_app.py` (was lines 265–269)

`process_window()` contained `return metrics` written three times in a row. The second and third returns were unreachable dead code, caused by erroneous copy-paste. This was confusing and could mask future bugs inserted after the first return.

**Status: FIXED** — reduced to a single `return metrics`.

---

#### C-2: ~~Hardcoded Flask `SECRET_KEY`~~
**File:** `web_app.py` (was line 47)

```python
app.config['SECRET_KEY'] = 'mind-reader-secret-key-123'
```

A hardcoded, weak secret key makes Flask session cookies forgeable. Any attacker knowing the source code can sign arbitrary session cookies and hijack user accounts.

**Status: FIXED** — now reads from `os.environ.get('FLASK_SECRET_KEY', 'change-me-in-production')`. Set `FLASK_SECRET_KEY` to a strong random value in production.

---

### HIGH

#### H-1: ~~Duplicate `set_color` call in `mind_reader.py`~~
**File:** `mind_reader.py` (was lines 132–133)

`self.state_text.set_color(color)` was called twice in succession. Harmless but indicates copy-paste error.

**Status: FIXED** — duplicate removed.

---

#### H-2: WebSocket reconnection loop has no max retry / backoff
**File:** `web_app.py` (JavaScript, line 950 in the inline HTML)

```javascript
ws.onclose = (e) => {
    setTimeout(initNeuralStream, 2000);
};
```

On permanent server failure, the browser will reconnect forever at 2-second intervals — a CPU/network waste and potential DoS against the server.

**Fix:** Add a retry counter and exponential backoff (e.g., max 5 retries, cap at 30s).

---

#### H-3: Band power ratio (`engagement`) computed in dB domain — incorrect
**File:** `web_app.py` (line 229)

```python
engagement = float(powers["Beta"] / (powers["Theta"] + powers["Alpha"] + 1e-6))
```

`powers["Beta"]`, `powers["Theta"]`, and `powers["Alpha"]` are all in **dB** (log scale). A ratio of dB values is not a valid signal — the correct neuroscience formula for the engagement index requires linear power values. The result will be wrong (often near 1.0) and not meaningful.

**Fix:** Either compute engagement before converting to dB, or convert back to linear (`10 ** (p / 10)`) before the ratio.

---

#### H-4: `MindState._logger_worker` uses `User.query.get()` — deprecated SQLAlchemy API
**File:** `web_app.py` (line 129)

```python
user = User.query.get(metrics["user_id"])
```

`Query.get()` is deprecated in SQLAlchemy 2.0. Use `db.session.get(User, metrics["user_id"])` instead.

---

#### H-5: `EEGLog.query.order_by()` in `/export` returns ALL logs regardless of user
**File:** `web_app.py` (line 1021)

```python
logs = EEGLog.query.order_by(EEGLog.timestamp).all()
```

The export endpoint returns **every user's EEG logs** — not just the currently authenticated user's. This is a data privacy leak. Any logged-in user can export all other users' brain data.

**Fix:** Filter by `EEGLog.session_id` joined to `MindSession.user_id == current_user.id`.

---

### MEDIUM

#### M-1: Single global `MindState` — multiple simultaneous users share one EEG stream
**File:** `web_app.py` (line 1043)

`ms_instance = MindState()` is a module-level singleton. All users share the same EEG buffer, calibration state, and session. A second user logging in resets calibration for the first user (via `ms_instance.reset()`).

This is a fundamental architectural limitation for a multi-user app. Acceptable for single-user local use.

---

#### M-2: WebSocket server runs on a different port from the HTTP server
**File:** `web_app.py` (lines 28–30, 787)

The WS URL is hardcoded as `ws://${window.location.hostname}:8765`. This breaks if the app is served behind a reverse proxy that handles both ports under a single domain.

---

#### M-3: `mind_reader.py` logs to a `Logs/` directory that is not gitignored

The standalone script creates CSV log files in a `Logs/` folder. These should be gitignored.

---

### LOW

#### L-1: No `.gitignore` in MindReader project

Session databases (`mind_reader.db`, `mind_reader.db-wal`), log CSVs, and `__pycache__` are not excluded.

---

## 3. Fixes Applied

| Issue | Fix |
|-------|-----|
| C-1: Triple `return metrics` | Removed duplicate returns in `web_app.py` |
| C-2: Hardcoded SECRET_KEY | Now reads from `FLASK_SECRET_KEY` env var |
| H-1: Duplicate `set_color` | Removed duplicate in `mind_reader.py` |

---

## 4. Recommendations

1. **Set `FLASK_SECRET_KEY`** in your environment to a strong random 32-byte hex string before running in any non-local context.
2. Fix the `/export` endpoint to filter by current user (H-5) — this is a privacy violation.
3. Add exponential backoff to the WebSocket reconnect logic (H-2).
4. Fix the `engagement` band power ratio to use linear domain values (H-3).
5. Create a `.gitignore` excluding `*.db`, `*.db-wal`, `*.db-shm`, `Logs/`, `__pycache__/`.
