import React, { useEffect, useState, useRef } from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { Activity, Brain, Zap, Heart, Disc } from 'lucide-react';
import './index.css';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

interface EEGData {
  powers: { [key: string]: number };
  valence: number;
  arousal: number;
  engagement: number;
  flow: boolean;
  mood: string;
  timestamp: string;
}

const HISTORY_SIZE = 50;
const BANDS = ["Delta", "Theta", "Alpha", "Beta", "Gamma"];
const BAND_COLORS = {
  Delta: '#00f3ff',
  Theta: '#ff00ff',
  Alpha: '#f3ff00',
  Beta: '#00ff41',
  Gamma: '#ffffff'
};

const App: React.FC = () => {
  const [data, setData] = useState<EEGData | null>(null);
  const [history, setHistory] = useState<{ [key: string]: number[] }>({
    Delta: new Array(HISTORY_SIZE).fill(0),
    Theta: new Array(HISTORY_SIZE).fill(0),
    Alpha: new Array(HISTORY_SIZE).fill(0),
    Beta: new Array(HISTORY_SIZE).fill(0),
    Gamma: new Array(HISTORY_SIZE).fill(0),
  });
  const [connected, setConnected] = useState(false);
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    const connect = () => {
      ws.current = new WebSocket('ws://localhost:8765');
      
      ws.current.onopen = () => {
        setConnected(true);
        console.log('Connected to Mind Reader Bridge');
        // Simple identity for React app (Can be expanded if auth is added)
        ws.current?.send(JSON.stringify({
          type: 'IDENTIFY',
          username: 'ReactUser',
          user_id: 999
        }));
      };

      ws.current.onmessage = (event) => {
        const newData: EEGData = JSON.parse(event.data);
        setData(newData);
        
        setHistory(prev => {
          const next = { ...prev };
          BANDS.forEach(band => {
            next[band] = [...prev[band].slice(1), newData.powers[band]];
          });
          return next;
        });
      };

      ws.current.onclose = () => {
        setConnected(false);
        console.log('Disconnected. Retrying in 2s...');
        setTimeout(connect, 2000);
      };
    };

    connect();
    return () => ws.current?.close();
  }, []);

  const chartData = {
    labels: new Array(HISTORY_SIZE).fill(''),
    datasets: BANDS.map(band => ({
      label: band,
      data: history[band],
      borderColor: BAND_COLORS[band as keyof typeof BAND_COLORS],
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.4,
    })),
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 0 },
    scales: {
      y: {
        grid: { color: 'rgba(255, 255, 255, 0.05)' },
        ticks: { color: 'rgba(255, 255, 255, 0.3)' },
        suggestedMin: -20,
        suggestedMax: 60
      },
      x: { display: false }
    },
    plugins: {
      legend: {
        labels: { color: 'white', boxWidth: 12, padding: 20 }
      }
    },
  };

  const getOrbStyle = () => {
    if (!data) return {};
    const scale = 1 + (data.engagement * 0.2);
    let color = 'var(--neon-cyan)';
    if (data.mood.includes("Happy")) color = 'var(--neon-lime)';
    if (data.mood.includes("Stressed")) color = 'var(--neon-magenta)';
    if (data.mood.includes("Sad")) color = '#1a2a4a';
    
    return {
      background: `radial-gradient(circle at 30% 30%, ${color}, transparent)`,
      boxShadow: `0 0 ${30 + data.engagement * 20}px ${color}`,
      transform: `scale(${scale})`,
    };
  };

  return (
    <div className="dashboard">
      <header>
        <div className="logo">MIND READER</div>
        <div className={`status-badge ${connected ? '' : 'disconnected'}`}>
          {connected ? 'BRIDGE CONNECTED' : 'RECONNECTING...'}
        </div>
      </header>

      <div className="glass-panel main-chart">
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Activity size={20} color="var(--neon-cyan)" /> BRAINWAVE SPECTRUM
          </h2>
          <div style={{ color: 'rgba(255,255,255,0.4)', fontSize: '0.8rem' }}>256Hz Real-time Sampling</div>
        </div>
        <div style={{ height: 'calc(100% - 60px)' }}>
          <Line data={chartData} options={chartOptions} />
        </div>
      </div>

      <div className="sidebar">
        <div className="glass-panel metric-card">
          <div className="metric-label">Mind State</div>
          <div className="metric-value" style={{ fontSize: '1.8rem', color: 'var(--neon-yellow)' }}>
            {data?.mood || "DISCONNECTED"}
          </div>
          <p style={{ marginTop: 10, opacity: 0.6, fontSize: '0.9rem' }}>
            {data?.flow ? "🔥 IN THE FLOW STATE 🔥" : "Standard Cognition"}
          </p>
        </div>

        <div className="glass-panel orb-container">
          <div className="energy-orb" style={getOrbStyle()} />
          <div style={{ position: 'absolute', bottom: 20, textAlign: 'center' }}>
            <div className="metric-label">Neural Engagement</div>
            <div className="metric-value">{data?.engagement.toFixed(2) || "0.00"}</div>
          </div>
        </div>

        <div className="glass-panel" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 15 }}>
          <div className="metric-card">
            <div className="metric-label">Valence</div>
            <div style={{ color: data && data.valence > 0 ? 'var(--neon-lime)' : 'var(--neon-magenta)', fontWeight: 'bold' }}>
              {data?.valence.toFixed(2) || "0.00"}
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Arousal</div>
            <div style={{ color: 'var(--neon-cyan)', fontWeight: 'bold' }}>
              {data?.arousal.toFixed(1) || "0.0"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default App;
