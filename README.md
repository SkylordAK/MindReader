# Mind Reader — Neural State Estimation

Mind Reader is an advanced EEG interpretation engine that transforms raw neural telemetry from the Muse headband into actionable psychological insights. It uses real-time heuristics to estimate mood, engagement, and the "Flow" state.

## Core Metrics
- **Mood Matrix (Valence/Arousal)**: Estimates emotional state by comparing frontal alpha asymmetry and beta-to-alpha ratios.
- **Engagement Index**: Monitors cognitive involvement using the ratio of Beta to (Theta + Alpha).
- **Flow State Detection**: Identifies the high-performance "Flow" state characterized by synchronous frontal theta and moderate alpha levels.
- **Cognitive Load**: Estimates mental effort via gamma/theta ratios.

## System Components
1. **EEG Engine (`mind_reader.py`)**: The core processor that calculates band powers and metrics.
2. **Web Dashboard (`web_app.py`)**: A modern, high-fidelity interface built with Flask and Tailwind CSS for real-time visualization.
3. **WebSocket Bridge (`web_bridge.py`)**: Translates OSC data into WebSocket messages for low-latency web updates.

## Architecture
- **Phase-based Calibration**: 
  - 30s Discard: Filters initial noise.
  - 60s Baseline: Establishes your personal neural "normal".
  - Live Mode: Real-time prediction and logging.
- **No-Build Frontend**: The dashboard uses a CDN-based architecture for instant deployment without complex toolchains.

## Setup
1. **OSC Source**: Use Mind Monitor or the Muse App to stream data to port `5239`.
2. **Install Requirements**:
   ```bash
   pip install numpy scipy python-osc flask flask-sqlalchemy flask-login websockets
   ```
3. **Launch Server**:
   ```bash
   python web_app.py
   ```
4. **Access Dashboard**: Open `http://localhost:5005` in your browser.

## Features
- **User Authentication**: Securely save your neural baseline and session history.
- **Dynamic 2D Mood Grid**: Watch your emotional state shift in real-time.
- **Spectral Radar**: Visualize the intensity of different brainwave bands.
- **Neural Mirror**: An interactive engagement "orb" that reflects your cognitive state.
