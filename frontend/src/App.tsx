import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const connectionString = API_URL.replace(/^http/, 'ws') + '/ws/live/QD-01';
    const socket = new WebSocket(connectionString);

    socket.onopen = () => {
      setConnected(true);
      setError(null);
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        setData(payload);
      } catch (err) {
        setError('Received malformed live payload');
      }
    };

    socket.onerror = () => {
      setError('WebSocket connection failed. Ensure the backend is running.');
    };

    socket.onclose = () => {
      setConnected(false);
    };

    return () => socket.close();
  }, []);

  const statusTone = useMemo(() => {
    if (!connected) {
      return 'bg-orange-500';
    }
    return 'bg-[#3DF5FF]';
  }, [connected]);

  return (
    <div className="min-h-screen bg-backdrop text-white">
      <motion.div
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="mx-auto max-w-7xl px-6 py-8"
      >
        <header className="mb-8 flex items-center justify-between rounded-lg border border-white/10 bg-panel p-4 shadow-panel">
          <div>
            <div className="text-xs uppercase tracking-[0.35em] text-muted">QuietDrive</div>
            <div className="mt-2 font-display text-2xl">Live Vehicle / QD-01</div>
          </div>
          <div className="flex items-center gap-3">
            <div className={`h-2.5 w-2.5 rounded-full ${statusTone}`} />
            <span className="font-mono text-sm uppercase tracking-[0.2em] text-muted">
              {connected ? 'LIVE' : 'CONNECTING'}
            </span>
          </div>
        </header>

        <main className="grid gap-6 xl:grid-cols-[1.6fr_0.9fr]">
          <section className="rounded-lg border border-white/10 bg-panel p-6 shadow-panel">
            <div className="mb-6 flex items-center justify-between">
              <div>
                <div className="text-xs uppercase tracking-[0.3em] text-muted">Health</div>
                <div className="mt-2 text-7xl font-display font-bold tracking-tight" style={{ lineHeight: 1 }}>
                  {data?.health ?? 82}
                </div>
              </div>
              <div className="flex h-40 w-40 items-center justify-center rounded-full border-8 border-[#3DFFA2] text-2xl font-bold text-[#3DFFA2]">
                {data?.health ?? 82}
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <div className="rounded border border-white/10 bg-[#0A1016] p-4">
                <div className="text-[10px] uppercase tracking-[0.25em] text-muted">RPM</div>
                <div className="mt-4 font-mono text-3xl text-[#3DF5FF]">{data?.rpm ?? 1800}</div>
              </div>
              <div className="rounded border border-white/10 bg-[#0A1016] p-4">
                <div className="text-[10px] uppercase tracking-[0.25em] text-muted">CURRENT</div>
                <div className="mt-4 font-mono text-3xl text-[#3DF5FF]">{data?.current_a ?? 11.2}A</div>
              </div>
              <div className="rounded border border-white/10 bg-[#0A1016] p-4">
                <div className="text-[10px] uppercase tracking-[0.25em] text-muted">TEMP</div>
                <div className="mt-4 font-mono text-3xl text-[#3DF5FF]">{data?.temp_c ?? 72}°C</div>
              </div>
            </div>
          </section>

          <aside className="rounded-lg border border-white/10 bg-panel p-6 shadow-panel">
            <div className="text-[10px] uppercase tracking-[0.25em] text-muted">Fault Indicator</div>
            <div className="mt-4 rounded border border-[#3DF5FF]/30 bg-[#0A1016] p-4">
              <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-[#3DF5FF]">IMBALANCE</div>
              <div className="mt-3 text-sm text-muted">2/3 sensor agreement</div>
            </div>
            <div className="mt-6 space-y-3 text-sm text-muted">
              <div className="flex justify-between"><span>Audio</span><span className="font-mono text-white">0.81</span></div>
              <div className="flex justify-between"><span>Vibration</span><span className="font-mono text-white">0.93</span></div>
              <div className="flex justify-between"><span>Current</span><span className="font-mono text-white">0.68</span></div>
            </div>
          </aside>
        </main>

        <div className="mt-6 rounded-lg border border-white/10 bg-panel p-6 shadow-panel">
          <div className="mb-3 text-[10px] uppercase tracking-[0.3em] text-muted">Live Feed</div>
          {error ? (
            <div className="rounded border border-orange-500/30 bg-orange-500/10 p-4 text-sm text-orange-200">{error}</div>
          ) : (
            <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs text-[#dfeaf5]">
              {JSON.stringify(data ?? { status: 'waiting for data' }, null, 2)}
            </pre>
          )}
        </div>
      </motion.div>
    </div>
  );
}

export default App;
