"""
Mind Reader — All-in-One Web Server
Combines OSC Bridge, WebSocket Broadcaster, and Flask Web Server.
No-Build Architecture (CDN-based Frontend).
"""

import asyncio
import json
import os
import numpy as np
import threading
import time
from datetime import datetime
import io
import csv
from flask import Flask, render_template_string, request, redirect, url_for, flash, jsonify, Response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from pythonosc import dispatcher, osc_server
from scipy.fft import rfft, rfftfreq
import websockets

# ──────────────────────── CONFIG ────────────────────────
OSC_IP = "0.0.0.0"
OSC_PORT = 5239
WS_IP = "0.0.0.0"
WS_PORT = 8765
WEB_PORT = 5005

FS = 256
WINDOW_SEC = 2.0
STEP_SEC = 0.2
WINDOW_SAMPLES = int(FS * WINDOW_SEC)
STEP_SAMPLES = int(FS * STEP_SEC)

BANDS = {
    "Delta": (1, 4),
    "Theta": (4, 8),
    "Alpha": (8, 13),
    "Beta": (13, 30),
    "Gamma": (30, 45)
}

# ──────────────────────── DATABASE & AUTH ────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'change-me-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///mind_reader.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
CORS(app)
db = SQLAlchemy(app)

# Phase 4: Enable SQLite WAL mode for high concurrency
with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(db.text('PRAGMA journal_mode=WAL'))

login_manager = LoginManager(app)
login_manager.login_view = 'index'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    sessions = db.relationship('MindSession', backref='user', lazy=True)

class MindSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start_time = db.Column(db.DateTime, default=datetime.utcnow)
    end_time = db.Column(db.DateTime)
    logs = db.relationship('EEGLog', backref='session', lazy=True)

class EEGLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('mind_session.id'), nullable=False)
    username = db.Column(db.String(80)) # Redundant but helpful for flat CSV exports
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    valence = db.Column(db.Float)
    arousal = db.Column(db.Float)
    engagement = db.Column(db.Float)
    mood = db.Column(db.String(50))
    delta = db.Column(db.Float)
    theta = db.Column(db.Float)
    alpha = db.Column(db.Float)
    beta = db.Column(db.Float)
    gamma = db.Column(db.Float)
    coherence = db.Column(db.Float)
    cog_load = db.Column(db.Float)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ──────────────────────── STATE ─────────────────────────
class MindState:
    def __init__(self):
        self.buffer = np.empty((0, 4))
        self.ws_clients = {} # client: metadata_dict
        self.data_received = False
        self.loop: asyncio.AbstractEventLoop = None
        self.start_time = time.time()
        
        # Session Tracking (Legacy global fallback)
        self.current_user_id = None
        self.current_session_id = None
        self.log_counter = 0 
        
        # Calibration State (Global for the single Muse stream)
        self.phase = "DISCARD"
        self.baseline_data = {"valence": [], "arousal": []}
        self.stats = {"v_mean": 0, "v_std": 1, "a_mean": 0, "a_std": 1}
        self.phase_start = time.time()
        
        # Phase 4: Async Logging Queue
        from queue import Queue
        from threading import Thread
        self.log_queue = Queue()
        self.logger_thread = Thread(target=self._logger_worker, daemon=True)
        self.logger_thread.start()

    def _logger_worker(self):
        """Background thread for non-blocking DB writes"""
        while True:
            metrics = self.log_queue.get()
            if metrics is None: break
            try:
                with app.app_context():
                    user = User.query.get(metrics["user_id"])
                    uname = user.username if user else "unknown"
                    log = EEGLog(
                        session_id=metrics["session_id"],
                        username=uname,
                        valence=metrics["valence"],
                        arousal=metrics["arousal"],
                        engagement=metrics["engagement"],
                        mood=metrics["mood"],
                        delta=metrics["powers"]["Delta"],
                        theta=metrics["powers"]["Theta"],
                        alpha=metrics["powers"]["Alpha"],
                        beta=metrics["powers"]["Beta"],
                        gamma=metrics["powers"]["Gamma"],
                        coherence=metrics["coherence"],
                        cog_load=metrics["cog_load"]
                    )
                    db.session.add(log)
                    db.session.commit()
            except Exception as e:
                print(f">>> [ASYNC DB ERROR] {e}")
            finally:
                self.log_queue.task_done()

    def reset(self):
        self.buffer = np.empty((0, 4))
        self.phase = "DISCARD"
        self.baseline_data = {"valence": [], "arousal": []}
        self.stats = {"v_mean": 0, "v_std": 1, "a_mean": 0, "a_std": 1}
        self.phase_start = time.time()
        self.start_time = time.time()
        print(">>> [SESSION] Restarted by user.")

    def get_band_power(self, data, low, high):
        yf = rfft(data)
        xf = rfftfreq(len(data), 1/FS)
        idx = np.logical_and(xf >= low, xf <= high)
        if any(idx):
            mean_power = float(np.mean(np.abs(yf[idx])**2))
            # Mind Monitor standard: Absolute Band Power in Decibels (dB)
            return 10 * np.log10(mean_power + 1e-6)
        return -20.0 # Floor for dB

    def log_to_db(self, metrics):
        if not self.current_session_id:
            return
        
        # Push to async queue instead of blocking
        log_payload = {**metrics, "user_id": self.current_user_id, "session_id": self.current_session_id}
        self.log_queue.put(log_payload)

    def process_window(self, window):
        af7, af8 = window[:, 0], window[:, 1]
        all_channels = np.mean(window, axis=1)
        
        powers = {name: self.get_band_power(all_channels, *range) for name, range in BANDS.items()}
        
        alpha_af8 = self.get_band_power(af8, *BANDS["Alpha"])
        alpha_af7 = self.get_band_power(af7, *BANDS["Alpha"])
        
        # Raw Metrics (Valence/Arousal heuristics)
        v_raw = float(alpha_af8 - alpha_af7) # Difference in dB
        a_raw = float(powers["Beta"] - powers["Alpha"]) # Difference in dB
        
        # Calibration Logic
        elapsed_phase = time.time() - self.phase_start
        phase_remaining = 0
        instruction = ""
        
        if self.phase == "DISCARD":
            phase_remaining = max(0, int(30 - elapsed_phase))
            instruction = "Sit still and breathe normally. Discarding initial noise..."
            if elapsed_phase > 30:
                self.phase = "BASELINE"
                self.phase_start = time.time()
            mood = "CALIBRATING (DISCARD)"
        elif self.phase == "BASELINE":
            phase_remaining = max(0, int(60 - elapsed_phase))
            instruction = "Close your eyes and stay relaxed. Establishing your neural baseline..."
            self.baseline_data["valence"].append(v_raw)
            self.baseline_data["arousal"].append(a_raw)
            if elapsed_phase > 60:
                self.stats["v_mean"] = np.mean(self.baseline_data["valence"])
                self.stats["v_std"] = np.std(self.baseline_data["valence"]) or 1.0
                self.stats["a_mean"] = np.mean(self.baseline_data["arousal"])
                self.stats["a_std"] = np.std(self.baseline_data["arousal"]) or 1.0
                self.phase = "LIVE"
            mood = "CALIBRATING (BASELINE)"
        
        # Z-Score Normalization
        v_z = (v_raw - self.stats["v_mean"]) / self.stats["v_std"]
        a_z = (a_raw - self.stats["a_mean"]) / self.stats["a_std"]
        
        if self.phase == "LIVE":
            instruction = "Neural Stream Active. Predictions saved to your profile."
            if v_z > 0:
                mood = "Happy/Excited" if a_z > 0 else "Calm/Zen"
            else:
                mood = "Stressed/Anxious" if a_z > 0 else "Sad/Bored"

        engagement = float(powers["Beta"] / (powers["Theta"] + powers["Alpha"] + 1e-6))
        frontal_theta = self.get_band_power((af7 + af8) / 2, *BANDS["Theta"])
        flow = bool((frontal_theta > 1.2 * powers["Theta"]) and (0.8 < powers["Alpha"] < 1.5))
        
        # Simplified coherence: Correlation of Alpha/Beta power between hemispheres
        v1, v2 = af7 - np.mean(af7), af8 - np.mean(af8)
        raw_coherence = float(np.correlate(v1, v2) / (np.sqrt(np.sum(v1**2) * np.sum(v2**2)) + 1e-6))
        
        # "Cognitive Load" estimate using Gamma/Theta ratio
        cog_load = float(powers["Gamma"] / (powers["Theta"] + 1e-6))

        # --- Phase 3: SSVEP BCI Control ---
        # Detect absolute band power at flicker frequencies
        ssvep_10 = self.get_band_power(all_channels, 9.5, 10.5)
        ssvep_15 = self.get_band_power(all_channels, 14.5, 15.5)

        metrics = {
            "powers": powers, "valence": v_z, "arousal": a_z,
            "engagement": engagement, "flow": flow, "mood": mood,
            "coherence": raw_coherence,
            "ssvep": {"10hz": ssvep_10, "15hz": ssvep_15},
            "cog_load": cog_load,
            "session_duration": int(time.time() - self.start_time),
            "phase": self.phase,
            "phase_remaining": phase_remaining,
            "instruction": instruction,
            "timestamp": datetime.now().isoformat()
        }

        # Log to DB every few windows (e.g., every 2 seconds of data / 4 windows)
        if self.phase == "LIVE":
            self.log_counter += 1
            if self.log_counter >= 5: # Adjusted for 0.2s steps to ~1s
                self.log_to_db(metrics)
                self.log_counter = 0

        return metrics

    def handle_osc(self, address, *args):
        if not self.data_received:
            print(">>> [SUCCESS] Receiving EEG data!")
            self.data_received = True
            self.start_time = time.time()
            self.phase_start = time.time()

        sample = np.array(args[:4]).reshape(1, 4)
        self.buffer = np.vstack([self.buffer, sample])
        
        if self.buffer.shape[0] >= WINDOW_SAMPLES:
            window = self.buffer[:WINDOW_SAMPLES]
            self.buffer = self.buffer[STEP_SAMPLES:]
            metrics = self.process_window(window)
            if self.loop:
                asyncio.run_coroutine_threadsafe(self.broadcast(metrics), self.loop)

    async def broadcast(self, data):
        if not self.ws_clients:
            return
            
        # Create user-specific payloads based on toggles
        for client, meta in list(self.ws_clients.items()):
            try:
                # Filter/Modify data based on per-client preferences
                client_data = data.copy()
                client_data["ml_enabled"] = meta.get("ml_enabled", True)
                client_data["ssvep_enabled"] = meta.get("ssvep_enabled", True)
                
                # If ML disabled for this client, strip ML metrics
                if not client_data["ml_enabled"]:
                    client_data["valence"] = 0
                    client_data["arousal"] = 0
                    client_data["mood"] = "ML DISABLED"
                    client_data["engagement"] = 0
                    client_data["coherence"] = 0
                    client_data["cog_load"] = 0
                
                # If SSVEP disabled for this client, strip SSVEP data
                if not client_data["ssvep_enabled"]:
                    client_data["ssvep"] = {"10hz": -20, "15hz": -20}

                await client.send(json.dumps(client_data))
            except Exception as e:
                print(f">>> [BROADCAST ERROR] Client failed: {e}")

    async def ws_handler(self, ws):
        print(f">>> [WS] New client connected from {ws.remote_address}")
        # Initialize with default metadata
        self.ws_clients[ws] = {
            "ml_enabled": True, 
            "ssvep_enabled": True,
            "user_id": None,
            "username": None
        }
        try: 
            async for message in ws:
                data = json.loads(message)
                if "type" in data:
                    if data["type"] == "IDENTIFY":
                        self.ws_clients[ws]["user_id"] = data.get("user_id")
                        self.ws_clients[ws]["username"] = data.get("username")
                        print(f">>> [WS] Client identified as {data.get('username')}")
                    elif data["type"] == "TOGGLE_ML":
                        self.ws_clients[ws]["ml_enabled"] = data.get("value", True)
                        print(f">>> [WS] User {self.ws_clients[ws]['username']} toggled ML: {data.get('value')}")
                    elif data["type"] == "TOGGLE_SSVEP":
                        self.ws_clients[ws]["ssvep_enabled"] = data.get("value", True)
                        print(f">>> [WS] User {self.ws_clients[ws]['username']} toggled SSVEP: {data.get('value')}")
        except Exception as e:
            print(f">>> [WS ERROR] {e}")
        finally: 
            if ws in self.ws_clients:
                self.ws_clients.pop(ws)
            print(f">>> [WS] Client disconnected from {ws.remote_address}")

# ──────────────────────── FLASK ─────────────────────────
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mind Reader | Neural Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;800&family=Orbitron:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #020617;
            --glass-bg: rgba(15, 23, 42, 0.6);
            --neon-blue: #38bdf8;
            --neon-purple: #c084fc;
            --neon-green: #4ade80;
            --neon-rose: #fb7185;
        }
        body {
            background-color: var(--bg-dark);
            color: #f8fafc;
            font-family: 'Inter', sans-serif;
            overflow-y: auto;
            min-height: 100vh;
        }
        .glass {
            background: rgba(15, 23, 42, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
        }
        .neon-border { position: relative; }
        .neon-border::after {
            content: ''; position: absolute; inset: 0;
            border-radius: inherit; border: 1px solid transparent;
            background: linear-gradient(45deg, var(--neon-blue), var(--neon-purple)) border-box;
            mask: linear-gradient(#fff 0 0) padding-box, linear-gradient(#fff 0 0);
            mask-composite: exclude; pointer-events: none; opacity: 0.2;
        }
        .orb {
            width: 160px; height: 160px; border-radius: 50%;
            background: #1e293b;
            border: 4px solid var(--neon-blue);
            transition: all 0.2s ease-out;
            position: relative;
            overflow: hidden;
        }
        .orb-fill {
            position: absolute; bottom: 0; left: 0; right: 0;
            background: linear-gradient(to top, var(--neon-blue), var(--neon-purple));
            transition: height 0.3s ease-out;
            opacity: 0.5;
        }
        input { background: rgba(0,0,0,0.2) !important; border-color: rgba(255,255,255,0.1) !important; color: white !important; }
        .hidden-screen { display: none !important; }

        /* PHASE 3: NEURAL MIRROR & SSVEP */
        @keyframes flicker-10 { 0%, 100% { opacity: 1; filter: drop-shadow(0 0 10px #38bdf8); } 50% { opacity: 0.1; } }
        @keyframes flicker-15 { 0%, 100% { opacity: 1; filter: drop-shadow(0 0 10px #c084fc); } 50% { opacity: 0.1; } }
        .ssvep-10 { animation: flicker-10 0.1s infinite; }
        .ssvep-15 { animation: flicker-15 0.066s infinite; }
        
        .neural-mirror {
            transition: all 0.5s cubic-bezier(0.4, 0, 0.2, 1);
            will-change: transform, box-shadow;
            z-index: 10;
        }
        .orb-glow { display: none; }

        /* TOGGLE STYLES */
        .toggle-container {
            display: inline-flex;
            align-items: center;
            cursor: pointer;
            position: relative;
        }
        .toggle-container input { display: none; }
        .toggle-slider {
            width: 32px; height: 16px;
            background: rgba(255,255,255,0.1);
            border-radius: 20px;
            position: relative;
            transition: 0.3s;
            border: 1px solid rgba(255,255,255,0.05);
        }
        .toggle-slider::before {
            content: ''; position: absolute;
            width: 12px; height: 12px;
            background: white; border-radius: 50%;
            top: 1px; left: 1px;
            transition: 0.3s;
            box-shadow: 0 0 10px rgba(255,255,255,0.5);
        }
        input:checked + .toggle-slider { background: var(--neon-blue); }
        input:checked + .toggle-slider::before { transform: translateX(16px); }
    </style>
</head>
<body class="min-h-screen flex flex-col p-4 md:p-6 gap-6">

    <!-- AUTH SCREEN -->
    <div id="authScreen" class="fixed inset-0 z-50 flex items-center justify-center p-6 bg-slate-950/80 backdrop-blur-xl">
        <div class="glass p-10 rounded-3xl w-full max-w-md flex flex-col gap-6 shadow-2xl relative overflow-hidden">
            <div class="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-blue-500 to-purple-600"></div>
            <div class="text-center">
                <h2 class="text-3xl font-black tracking-tight" style="font-family: 'Orbitron'">MIND READER</h2>
                <p class="text-xs text-slate-400 mt-2 uppercase tracking-[0.3em]">Neural Interface Access</p>
            </div>
            
            <div id="authForms" class="flex flex-col gap-4 mt-4">
                <div class="space-y-4">
                    <input type="text" id="username" placeholder="IDENTIFIER" class="w-full px-4 py-3 rounded-xl border focus:border-blue-500 outline-none transition-all placeholder:text-slate-600">
                    <input type="password" id="password" placeholder="ACCESS CODE" class="w-full px-4 py-3 rounded-xl border focus:border-blue-500 outline-none transition-all placeholder:text-slate-600">
                </div>
                <div class="flex gap-4 mt-2">
                    <button type="button" onclick="handleAuth('login')" class="flex-1 bg-blue-600 hover:bg-blue-500 text-white font-bold py-3 rounded-xl shadow-lg transition-all active:scale-95">LOGIN</button>
                    <button type="button" onclick="handleAuth('register')" class="flex-1 border border-slate-700 hover:bg-white/5 font-bold py-3 rounded-xl transition-all active:scale-95">SIGN UP</button>
                </div>
            </div>
            <p id="authError" class="text-xs text-rose-500 text-center mt-2 h-4"></p>
        </div>
    </div>

    <!-- MAIN DASHBOARD -->
    <header class="flex justify-between items-end">
        <div>
            <div class="flex items-center gap-3">
                <h1 class="text-3xl font-black tracking-tighter text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-purple-500" style="font-family: 'Orbitron'">MIND READER <span class="text-[10px] text-slate-500 align-top opacity-50">PRO 3.0</span></h1>
                <span id="userBadge" class="bg-blue-500/10 text-blue-400 text-[10px] px-2 py-0.5 rounded-full border border-blue-500/20 font-bold uppercase tracking-widest hidden">SYNCING...</span>
            </div>
            <div class="flex items-center gap-4 mt-1 opacity-50 text-[10px] uppercase font-bold tracking-[0.2em]">
                <span id="sessionTimer">00:00:00</span>
                <span>•</span>
                <span id="neuralStatus">CONNECTING...</span>
                <span>•</span>
                <span id="coherenceStatus" class="text-emerald-400">SYM: 0%</span>
            </div>
        </div>
        <div class="flex gap-4 items-center mr-4">
            <label class="toggle-container" title="Toggle Neural ML Calculations">
                <input type="checkbox" id="mlToggle" checked onchange="toggleFeature('ML', this.checked)">
                <span class="toggle-slider"></span>
                <span class="text-[8px] font-bold opacity-60 ml-2">ML</span>
            </label>
            <label class="toggle-container" title="Toggle SSVEP Calculations">
                <input type="checkbox" id="ssvepToggle" checked onchange="toggleFeature('SSVEP', this.checked)">
                <span class="toggle-slider"></span>
                <span class="text-[8px] font-bold opacity-60 ml-2">SSVEP</span>
            </label>
        </div>
        <div class="flex gap-3">
            <!-- SSVEP COMMANDS -->
            <div id="ssvepControls" class="flex gap-2 mr-6 items-center transition-opacity duration-500">
                <div class="flex flex-col items-center gap-1">
                    <div class="w-8 h-8 rounded-lg bg-blue-500/20 border border-blue-500/40 ssvep-10 cursor-pointer" title="Focus to Mute"></div>
                    <span class="text-[8px] opacity-40">MUTE (10Hz)</span>
                </div>
                <div class="flex flex-col items-center gap-1">
                    <div class="w-8 h-8 rounded-lg bg-purple-500/20 border border-purple-500/40 ssvep-15 cursor-pointer" title="Focus to Record"></div>
                    <span class="text-[8px] opacity-40">REC (15Hz)</span>
                </div>
            </div>
            <button onclick="handleExport()" class="glass px-4 py-2 rounded-xl text-[10px] font-bold uppercase tracking-widest hover:bg-emerald-500/20 text-emerald-400 transition-all border-emerald-500/20">Export CSV</button>
            <button onclick="restartSession()" class="glass px-4 py-2 rounded-xl text-[10px] font-bold uppercase tracking-widest hover:bg-white/5 transition-all">Restart</button>
            <button onclick="handleLogout()" class="glass px-4 py-2 rounded-xl text-[10px] font-bold uppercase tracking-widest hover:bg-rose-500/20 text-rose-400 transition-all border-rose-500/20">Logout</button>
        </div>
    </header>

    <div class="grid grid-cols-12 gap-6 flex-1 min-h-0">
        <!-- LEFT: Neural Spectrum -->
        <div class="col-span-12 lg:col-span-7 glass rounded-3xl p-6 flex flex-col min-h-[300px]">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-xs font-bold text-slate-400 uppercase tracking-widest">Neural Frequency Spectrum</h3>
                <div id="phaseIndicator" class="text-[9px] font-black bg-cyan-500/20 text-cyan-400 px-3 py-1 rounded-full animate-pulse transition-all">INITIALIZING</div>
            </div>
            <div class="flex-1 relative h-64 md:h-auto">
                <canvas id="lineChart"></canvas>
            </div>
        </div>

        <!-- RIGHT: Metrics & Mood -->
        <div class="col-span-12 lg:col-span-5 flex flex-col gap-6">
            
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-6">
                <!-- 2D MOOD GRID -->
                <div class="glass rounded-3xl p-6 flex flex-col items-center">
                    <h3 class="text-[10px] font-bold text-slate-500 uppercase tracking-[0.2em] mb-4">Mood Coordinate System</h3>
                    <div class="w-full aspect-square relative glass rounded-2xl border-white/5 overflow-hidden shadow-inner">
                        <!-- Grid Lines -->
                        <div class="absolute inset-0 flex items-center justify-center opacity-10">
                            <div class="w-px h-full bg-white"></div>
                            <div class="w-full h-px bg-white"></div>
                        </div>
                        <!-- Quadrant Labels -->
                        <div class="absolute top-2 right-2 text-[8px] opacity-20 uppercase">Excited</div>
                        <div class="absolute bottom-2 right-2 text-[8px] opacity-20 uppercase">Calm</div>
                        <div class="absolute top-2 left-2 text-[8px] opacity-20 uppercase">Stressed</div>
                        <div class="absolute bottom-2 left-2 text-[8px] opacity-20 uppercase">Sad</div>
                        <!-- Current Mood Dot -->
                        <div id="moodDot" class="absolute w-4 h-4 rounded-full bg-blue-400 shadow-[0_0_15px_#38bdf8] transition-all duration-300 ease-out" style="left: 50%; top: 50%; transform: translate(-50%, -50%)"></div>
                        <canvas id="scatterChart" class="hidden"></canvas>
                    </div>
                </div>

                <!-- BAND DISTRIBUTION -->
                <div class="glass rounded-3xl p-6 flex flex-col">
                    <h3 class="text-[10px] font-bold text-slate-500 uppercase tracking-[0.2em] mb-4 text-center">Spectral Intensity</h3>
                    <div class="flex-1 relative min-h-[150px]">
                        <canvas id="radarChart"></canvas>
                    </div>
                </div>
            </div>

            <!-- ENGAGEMENT ORB (THE NEURAL MIRROR) -->
            <div class="glass rounded-3xl p-8 flex flex-1 items-center gap-8 relative overflow-hidden group">
                <div class="neural-mirror flex-shrink-0 relative">
                    <div class="orb">
                        <div id="orbFill" class="orb-fill" style="height: 50%"></div>
                        <div class="absolute inset-0 flex items-center justify-center font-black text-xs opacity-20 tracking-widest">NEURAL</div>
                    </div>
                </div>
                <div class="flex flex-col gap-2">
                    <div class="flex flex-col">
                        <span class="text-[10px] font-black text-slate-500 uppercase tracking-[0.3em]">Engagement Score</span>
                        <div id="engagementVal" class="text-5xl font-black tracking-tighter text-blue-400">0.00</div>
                    </div>
                    <div id="moodText" class="text-xl font-bold uppercase tracking-tight text-slate-300">Calibrating...</div>
                    <p id="instruction" class="text-[11px] text-slate-500 italic leading-snug">Neural handshake in progress. Maintain neutral state.</p>
                </div>

                <!-- Flow Badge -->
                <div id="flowBadge" class="absolute top-6 right-8 text-[9px] font-black border border-white/10 px-3 py-1 rounded-full opacity-0 translate-y-2 transition-all duration-500">FLOW DETECTED</div>
                
                <div id="wsDebug" class="absolute bottom-2 right-4 text-[8px] text-slate-600 font-mono hidden md:block"></div>
            </div>
            </div>

        </div>
    </div>

    <!-- NOTIFICATION / HYPERSCAN -->
    <div id="hyperscanPanel" class="fixed top-24 right-6 w-48 glass rounded-2xl p-4 flex flex-col gap-3 opacity-0 pointer-events-none transition-all duration-1000">
        <div class="flex items-center justify-between">
            <span class="text-[8px] font-black text-slate-500 uppercase tracking-widest">Neural Sync</span>
            <div class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></div>
        </div>
        <div class="flex flex-col gap-1">
            <span class="text-[10px] text-slate-400">Partner: <span class="text-white">None</span></span>
            <div class="h-1 bg-white/5 rounded-full overflow-hidden">
                <div id="syncProgress" class="h-full bg-emerald-500" style="width: 0%"></div>
            </div>
        </div>
        <button class="text-[8px] font-bold text-blue-400 border border-blue-400/20 py-1 rounded-md hover:bg-blue-400/10">LINK BRAINS</button>
    </div>

    <!-- NOTIFICATION -->
    <div id="notify" class="fixed bottom-10 right-10 glass px-6 py-4 rounded-2xl shadow-2xl translate-y-32 opacity-0 transition-all duration-700 pointer-events-none border-blue-500/20">
        <div class="flex items-center gap-4">
            <div class="w-2 h-2 rounded-full bg-blue-400 animate-ping"></div>
            <p id="notifyText" class="text-sm font-bold tracking-tight"></p>
        </div>
    </div>

    <script>
        const colors = { 
            Delta: '#94a3b8', 
            Theta: '#c084fc', 
            Alpha: '#4ade80', 
            Beta: '#38bdf8', 
            Gamma: '#fb7185' 
        };

        // --- CHARTS ---
        const lineCtx = document.getElementById('lineChart').getContext('2d');
        const lineChart = new Chart(lineCtx, {
            type: 'line',
            data: {
                labels: Array(60).fill(''),
                datasets: Object.keys(colors).map(k => ({
                    label: k, data: Array(60).fill(0), borderColor: colors[k],
                    borderWidth: 1.5, pointRadius: 0, tension: 0.2, fill: false,
                    spanGaps: true // Optimization
                }))
            },
            options: {
                responsive: true, 
                maintainAspectRatio: false, 
                animation: false,
                scales: { 
                    y: { 
                        grid: { color: 'rgba(255,255,255,0.03)' }, 
                        ticks: { display: false },
                        suggestedMax: 60,
                        suggestedMin: -20
                    },
                    x: { display: false }
                },
                plugins: { 
                    legend: { 
                        position: 'bottom', 
                        labels: { 
                            color: '#64748b', 
                            boxWidth: 12, 
                            font: { size: 10, weight: 'bold' }, 
                            padding: 20 
                        } 
                    } 
                }
            }
        });

        const radarCtx = document.getElementById('radarChart').getContext('2d');
        const radarChart = new Chart(radarCtx, {
            type: 'radar',
            data: {
                labels: Object.keys(colors),
                datasets: [{
                    data: [0,0,0,0,0], backgroundColor: 'rgba(56, 189, 248, 0.2)', 
                    borderColor: '#38bdf8', borderWidth: 2, pointRadius: 0
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: { 
                    r: { 
                        angleLines: { color: 'rgba(255,255,255,0.05)' }, grid: { color: 'rgba(255,255,255,0.05)' },
                        pointLabels: { color: '#64748b', font: { size: 9, weight: 'bold' } },
                        ticks: { display: false }
                    }
                },
                plugins: { legend: { display: false } }
            }
        });

        // --- AUTH & STATE ---
        let ws = null;
        let currentUser = null;

        const showNotify = (text, isError = false) => {
            const el = document.getElementById('notify');
            const textEl = document.getElementById('notifyText');
            textEl.innerText = text;
            textEl.className = `text-sm font-bold tracking-tight ${isError ? 'text-rose-400' : 'text-blue-400'}`;
            el.style.opacity = '1'; el.style.transform = 'translateY(0)';
            setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(32px)'; }, 3000);
        };

        // --- GLOBAL DEBUGGING ---
        window.onerror = (msg, url, line) => {
            console.error(`>>> [GLOBAL ERROR] ${msg} at ${line}`);
            showNotify(`SYSTEM ERROR: ${msg}`, true);
        };

        const handleAuth = async (type) => {
            console.log(`>>> [AUTH] handleAuth called with type: ${type}`);
            const uEl = document.getElementById('username');
            const pEl = document.getElementById('password');
            const errEl = document.getElementById('authError');
            
            if (!uEl || !pEl || !errEl) {
                console.error(">>> [AUTH] Missing DOM elements!");
                return;
            }

            const u = uEl.value.trim();
            const p = pEl.value.trim();
            errEl.innerText = ""; 
            
            if(!u || !p) {
                console.warn(">>> [AUTH] Username or password missing");
                return showNotify("CREDENTIALS REQUIRED", true);
            }

            console.log(`>>> [AUTH] Attempting ${type} for user: ${u}`);
            try {
                const url = `/${type}`;
                console.log(`>>> [AUTH] Fetching ${url}...`);
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: u, password: p })
                });
                
                console.log(`>>> [AUTH] Response status: ${res.status}`);
                const data = await res.json();
                console.log(">>> [AUTH] Response data:", data);
                
                if (res.ok) {
                    if (type === 'register') {
                        showNotify("PROFILE CREATED - LOGIN NOW");
                        pEl.value = ""; 
                        return;
                    }
                    currentUser = data.username;
                    document.getElementById('authScreen').classList.add('hidden-screen');
                    document.getElementById('userBadge').innerText = currentUser;
                    document.getElementById('userBadge').classList.remove('hidden');
                    showNotify(`SYSTEM SYNCED: ${currentUser}`);
                    initNeuralStream(currentUser, data.id); // Pass identity
                } else {
                    errEl.innerText = (data.message || "Error").toUpperCase();
                }
            } catch (e) { 
                console.error(">>> [AUTH] Fetch Exception:", e);
                showNotify("CONNECTION ERROR", true); 
                errEl.innerText = "SERVER UNREACHABLE";
            }
        };

        const handleLogout = async () => {
            await fetch('/logout', { method: 'POST' });
            location.reload();
        };

        const restartSession = async () => {
             if (confirm("Restart neural baseline recalibration?")) {
                await fetch('/restart', { method: 'POST' });
                showNotify("RECALIBRATING...");
            }
        };

        const handleExport = () => {
            showNotify("PREPARING DATASET...");
            window.location.href = '/export';
        };

        const toggleFeature = (feature, value) => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: `TOGGLE_${feature}`, value: value }));
                showNotify(`${feature} ${value ? 'ENABLED' : 'DISABLED'}`);
                
                if (feature === 'SSVEP') {
                    document.getElementById('ssvepControls').style.opacity = value ? '1' : '0.2';
                    document.getElementById('ssvepControls').style.pointerEvents = value ? 'auto' : 'none';
                }
            }
        };

        let frameCount = 0;
        const initNeuralStream = (username, userId) => {
            const wsUrl = `ws://${window.location.hostname}:8765`;
            console.log(`>>> [WS] Connecting to ${wsUrl}`);
            const dbgEl = document.getElementById('wsDebug');
            if (dbgEl) {
                dbgEl.innerText = wsUrl;
                dbgEl.classList.remove('hidden');
            }
            
            ws = new WebSocket(wsUrl);
            
            ws.onopen = () => {
                console.log(">>> [WS] Connection established");
                document.getElementById('neuralStatus').innerText = 'NEURAL STREAM ACTIVE';
                document.getElementById('neuralStatus').className = 'text-blue-400';
                
                // Identify with server
                ws.send(JSON.stringify({
                    type: "IDENTIFY",
                    username: username,
                    user_id: userId
                }));
            };

            ws.onerror = (err) => {
                console.error(">>> [WS] Connection error:", err);
                showNotify("NEURAL BRIDGE OFFLINE", true);
            };

            ws.onmessage = (e) => {
                try {
                const data = JSON.parse(e.data);
                if (!data) return;
                
                // Throttled Chart Updates (Every 2nd frame ~5Hz at 10Hz sampling)
                // Since STEP_SEC is 0.2, it's 5 updates per second.
                if (data.powers) {
                    Object.keys(colors).forEach((k, i) => {
                        if (data.powers[k] !== undefined) {
                            lineChart.data.datasets[i].data.push(data.powers[k]);
                            lineChart.data.datasets[i].data.shift();
                        }
                    });
                    
                    lineChart.update('none'); 
                    if (frameCount % 2 === 0) {
                        radarChart.data.datasets[0].data = Object.keys(colors).map(k => data.powers[k] || 0);
                        radarChart.update('none');
                    }
                }
                frameCount++;

                // Toggle Reflection (Sync with server state if needed, but local for now)
                
                // Phase 3: Cognitive Load (Pro 3.0 ML Port)
                const cogVal = data.cog_load !== undefined ? Math.min(1.0, data.cog_load / 2).toFixed(2) : "0.00";
                if (dbgEl) {
                    dbgEl.innerText = `ML: ${data.ml_enabled ? 'ON' : 'OFF'} | SSVEP: ${data.ssvep_enabled ? 'ON' : 'OFF'} | COG_LOAD=${cogVal}`;
                    dbgEl.classList.remove('hidden');
                }
                
                // Update Mood Dot (Valence/Arousal Grid)
                const clamp = (v) => Math.min(Math.max(v || 0, -3), 3);
                const x = ((clamp(data.valence) + 3) / 6) * 100;
                const y = (1 - (clamp(data.arousal) + 3) / 6) * 100;
                const dot = document.getElementById('moodDot');
                if (dot) {
                    dot.style.left = `${x}%`; dot.style.top = `${y}%`;
                    dot.style.opacity = data.ml_enabled ? '1' : '0.2';
                }

                // Metrics & UI
                const eVal = document.getElementById('engagementVal');
                if (eVal) eVal.innerText = (data.engagement || 0).toFixed(2);
                
                const mt = document.getElementById('moodText');
                if (mt) {
                    mt.innerText = data.mood || "ANALYZING...";
                    mt.style.opacity = data.ml_enabled ? '1' : '0.4';
                }
                
                const ins = document.getElementById('instruction');
                if (ins) ins.innerText = data.instruction || "";
                
                const st = document.getElementById('sessionTimer');
                if (st) st.innerText = new Date((data.session_duration || 0) * 1000).toISOString().substr(11, 8);
                
                // Phase Indicator
                const pi = document.getElementById('phaseIndicator');
                if (pi) {
                    pi.innerText = data.phase === 'LIVE' ? 'LIVE' : `${data.phase || 'BOOT'}: ${data.phase_remaining || 0}S`;
                    if (data.phase === 'ML_DISABLED') pi.innerText = 'ML DISABLED';
                    pi.className = `text-[9px] font-black px-3 py-1 rounded-full transition-all ${data.phase === 'LIVE' ? 'bg-green-500/20 text-green-400' : 'bg-cyan-500/20 text-cyan-400 animate-pulse'}`;
                }

                // Flow Integration
                const flowBadge = document.getElementById('flowBadge');
                if (flowBadge) {
                    if (data.flow) {
                        flowBadge.style.opacity = '1';
                        flowBadge.style.transform = 'translateY(0)';
                        flowBadge.className = 'absolute top-6 right-8 text-[9px] font-black border border-emerald-500/50 bg-emerald-500/20 text-emerald-400 px-3 py-1 rounded-full animate-bounce';
                    } else {
                        flowBadge.style.opacity = '0';
                        flowBadge.style.transform = 'translateY(8px)';
                    }
                }

                // Phase 3: Neural Coherence Status
                const cohEl = document.getElementById('coherenceStatus');
                if (cohEl) {
                    const coh = Math.abs((data.coherence || 0) * 100).toFixed(0);
                    cohEl.innerText = `SYM: ${coh}%`;
                    cohEl.className = coh > 60 && data.ml_enabled ? 'text-emerald-400' : 'text-slate-500';
                }

                // Phase 3: The Neural Mirror (Ultra-Sharp Performance Mode)
                const orb = document.querySelector('.orb');
                const orbFill = document.getElementById('orbFill');
                const nm = document.querySelector('.neural-mirror');
                
                if (orb && orbFill && nm) {
                    const eng = (data.engagement || 0);
                    const scale = 0.8 + (eng * 0.4);
                    const color = data.valence > 0 ? '#4ade80' : '#38bdf8';
                    
                    nm.style.transform = `scale(${scale})`;
                    orb.style.borderColor = data.ml_enabled ? color : '#334155';
                    orbFill.style.height = `${eng * 100}%`;
                    orbFill.style.background = color;
                    orbFill.style.opacity = data.ml_enabled ? '0.5' : '0.1';

                    if (data.cog_load !== undefined && data.ml_enabled) {
                        orb.style.borderWidth = `${2 + (data.cog_load * 6)}px`;
                        if (data.cog_load > 1.2) orb.style.borderColor = '#fb7185';
                    } else {
                        orb.style.borderWidth = '2px';
                    }
                }

                // Phase 3: SSVEP Visual Feedback (Restored)
                const s10 = document.querySelector('.ssvep-10');
                const s15 = document.querySelector('.ssvep-15');
                if (data.ssvep_enabled && data.ssvep) {
                    if (s10) {
                        if (data.ssvep['10hz'] > 50) s10.classList.add('bg-blue-400/50');
                        else s10.classList.remove('bg-blue-400/50');
                    }
                    if (s15) {
                        if (data.ssvep['15hz'] > 50) s15.classList.add('bg-purple-400/50');
                        else s15.classList.remove('bg-purple-400/50');
                    }
                }
                } catch (err) {
                    console.error(">>> [FRONTEND ERROR]", err);
                    const dbgEl = document.getElementById('wsDebug');
                    if (dbgEl) dbgEl.innerText = `UI ERROR: ${err.message}`;
                }
            };

            ws.onclose = (e) => {
                console.warn(">>> [WS] Closed:", e.code, e.reason);
                document.getElementById('neuralStatus').innerText = 'STREAM LOST - RECONNECTING';
                document.getElementById('neuralStatus').className = 'text-rose-500';
                setTimeout(initNeuralStream, 2000);
            };
        };

    </script>
</body>
</html>
"""

@app.route("/")
def index(): 
    return render_template_string(INDEX_HTML)

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    print(f">>> [AUTH] Registering user: {data.get('username')}")
    if User.query.filter_by(username=data['username']).first():
        print(f">>> [AUTH] Registration failed: User {data['username']} already exists")
        return {"status": "error", "message": "User already exists"}, 400
    
    user = User(username=data['username'], password_hash=generate_password_hash(data['password']))
    db.session.add(user)
    db.session.commit()
    print(f">>> [AUTH] User {data['username']} registered successfully")
    return {"status": "success"}

@app.route("/login", methods=["POST"])
def login():
    data = request.json
    print(f">>> [AUTH] Login attempt for user: {data.get('username')}")
    user = User.query.filter_by(username=data['username']).first()
    if user and check_password_hash(user.password_hash, data['password']):
        login_user(user)
        # Start a new MindSession
        new_session = MindSession(user_id=user.id)
        db.session.add(new_session)
        db.session.commit()
        
        ms_instance.current_user_id = user.id
        ms_instance.current_session_id = new_session.id
        ms_instance.reset() # Start calibration for new user
        
        print(f">>> [AUTH] User {user.username} logged in successfully")
        return {"status": "success", "username": user.username}
    print(f">>> [AUTH] Login failed for user: {data.get('username')}")
    return {"status": "error", "message": "Invalid credentials"}, 401

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    if ms_instance.current_session_id:
        sess = MindSession.query.get(ms_instance.current_session_id)
        if sess:
            sess.end_time = datetime.utcnow()
            db.session.commit()
    
    ms_instance.current_user_id = None
    ms_instance.current_session_id = None
    logout_user()
    return {"status": "success"}

@app.route("/restart", methods=["POST"])
@login_required
def restart():
    ms_instance.reset()
    return {"status": "success"}

@app.route("/export")
@login_required
def export():
    logs = EEGLog.query.order_by(EEGLog.timestamp).all()
    
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['id', 'session_id', 'username', 'timestamp', 'valence', 'arousal', 'engagement', 'mood', 'delta', 'theta', 'alpha', 'beta', 'gamma', 'coherence', 'cog_load'])
    
    for log in logs:
        cw.writerow([
            log.id, log.session_id, log.username, log.timestamp.isoformat(),
            log.valence, log.arousal, log.engagement, log.mood,
            log.delta, log.theta, log.alpha, log.beta, log.gamma,
            log.coherence, log.cog_load
        ])
    
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=mind_reader_dataset.csv"}
    )

# ──────────────────────── RUN ─────────────────────────
ms_instance = MindState()

async def run_servers():
    ms_instance.loop = asyncio.get_running_loop()
    
    # OSC Server
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", ms_instance.handle_osc)
    disp.map("/eeg", ms_instance.handle_osc)
    osc_srv = osc_server.ThreadingOSCUDPServer((OSC_IP, OSC_PORT), disp)
    threading.Thread(target=osc_srv.serve_forever, daemon=True).start()
    
    # WebSocket Server
    async with websockets.serve(ms_instance.ws_handler, WS_IP, WS_PORT):
        print(f">>> Mind Reader All-in-One Server Active.")
        print(f">>> UI: http://localhost:{WEB_PORT}")
        print(f">>> WebSocket: {WS_IP}:{WS_PORT}")
        print(f">>> Listening to Muse S on {OSC_IP}:{OSC_PORT}")
        await asyncio.Future() # keep running

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    
    def run_flask():
        app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)

    threading.Thread(target=run_flask, daemon=True).start()
    
    try:
        asyncio.run(run_servers())
    except KeyboardInterrupt:
        pass
    print("\nShutting down.")
