# MindReader — Real-Time EEG Mental State Classifier

A Flask web application that classifies your mental state in real time from a **Muse S EEG headset**. Detects four states — **Focus**, **Relaxation**, **Stress**, and **Flow** — using spectral band power analysis with a personalised 60-second baseline calibration.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python) ![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask) ![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-D71F00) ![SciPy](https://img.shields.io/badge/SciPy-1.12-8CAAE6)

---

## Mental State Classification

| State | EEG Signature |
|-------|---------------|
| **Focus** | Elevated beta (13–30 Hz), low theta |
| **Relaxation** | Dominant alpha (8–13 Hz) |
| **Stress** | High beta/gamma, elevated asymmetry |
| **Flow** | Alpha-theta crossover, low frontal beta |

---

## Features

- **Real-time classification** — sub-second state updates via SSE / polling
- **Personal baseline** — 60-second eyes-open rest session calibrates thresholds to your individual brain
- **Live dashboard** — engagement score, band power chart, session timeline
- **Session logging** — all sessions persisted to SQLite via SQLAlchemy for longitudinal analysis
- **Muse S integration** — receives data via OSC from the mind-monitor app

---

## Getting Started

### Prerequisites

- Python 3.9+
- Muse S EEG headset + [mind-monitor](https://mind-monitor.com/) app (streams via OSC)
- Or: any EEG device that can stream OSC to port 5000

### Installation

```bash
git clone https://github.com/SkylordAK/MindReader.git
cd MindReader
pip install -r requirements.txt
```

### Run

```bash
# Start the web app
python web_app.py

# Open http://localhost:5000 in your browser
# Start mind-monitor on your phone, set OSC host to your PC IP, port 5000
```

---

## How the Baseline Works

1. Navigate to `/calibrate` in the browser
2. Sit quietly with eyes open for 60 seconds
3. The app records your personal alpha, beta, theta, and delta band means
4. All subsequent classifications use these as your individual reference point

Without calibration, the app falls back to population-average thresholds.

---

## Architecture

```
Muse S headset
      |
mind-monitor (OSC stream, port 5000)
      |
mind_reader.py  — OSC listener, band power extractor
      |
web_app.py (Flask) — REST endpoints + SSE stream
      |
Browser dashboard — live state display + history chart
      |
SQLAlchemy / SQLite — session persistence
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main dashboard |
| `/calibrate` | GET | Baseline calibration page |
| `/api/state` | GET | Current classified state (JSON) |
| `/api/metrics` | GET | Band powers + engagement score |
| `/api/history` | GET | Last N classified states |

---

## Requirements

```
flask>=3.0
sqlalchemy>=2.0
scipy>=1.12
numpy>=1.24
python-osc>=1.8
```

---

## Related Projects

- [SSVEPControl](https://github.com/SkylordAK/SSVEPControl) — SSVEP-based BCI control
- [EpilepsyDetection](https://github.com/SkylordAK/EpilepsyDetection) — Real-time seizure detector

---

## Disclaimer

Research and educational project. Not a medical device.

## License

MIT
