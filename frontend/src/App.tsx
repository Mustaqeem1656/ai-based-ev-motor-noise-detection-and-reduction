import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const VID = 'QD-01';

type Num = number | null | undefined;
type Tel = { rpm?: Num; current_a?: Num; temp_c?: Num; vib_rms_g?: Num; spl_ref_db?: Num; spl_err_db?: Num; anc_on?: boolean };
type Inf = {
  fault?: string; conf?: Record<string, Num>; severity_raw?: Num; protected_hz?: number[];
  points?: Num; health?: Num; tier?: string; action?: string; advice?: string; reason?: string;
  sensors_agree?: boolean; fix_db?: Num;
};
type Spec = { mic: string; fmax: number; bins: number[] };

// Merges the per-type live frames (tel, inf, spec ref/err) into one state.
function useLive(vid: string) {
  const [tel, setTel] = useState<Tel>({});
  const [inf, setInf] = useState<Inf>({});
  const [connected, setConnected] = useState(false);
  const [lastTs, setLastTs] = useState(0);
  const specListeners = useRef<((s: Spec) => void)[]>([]);

  useEffect(() => {
    let socket: WebSocket;
    let retry: number;
    const connect = () => {
      socket = new WebSocket(API_URL.replace(/^http/, 'ws') + '/ws/live/' + vid);
      socket.onopen = () => setConnected(true);
      socket.onclose = () => { setConnected(false); retry = window.setTimeout(connect, 1500); };
      socket.onmessage = (ev) => {
        let m: any;
        try { m = JSON.parse(ev.data); } catch { return; }
        setLastTs(Date.now());
        if (m.t === 'tel') setTel(m);
        else if (m.t === 'inf') setInf(m);
        else if (m.t === 'spec' && Array.isArray(m.bins)) specListeners.current.forEach((f) => f(m));
      };
    };
    connect();
    return () => { window.clearTimeout(retry); socket.onclose = null; socket.close(); };
  }, [vid]);

  return { tel, inf, connected, lastTs, specListeners };
}

const fmt = (v: Num, d = 0, unit = '') => (v === null || v === undefined || !isFinite(v) ? '—' : v.toFixed(d) + unit);

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded border border-white/10 bg-[#0A1016] p-4">
      <div className="text-[10px] uppercase tracking-[0.25em] text-muted">{label}</div>
      <div className="mt-3 font-mono text-2xl text-signal">{value}</div>
      {sub && <div className="mt-1 text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

export const TIER_COLOR: Record<string, string> = {
  HEALTHY: '#3DFFA2', MILD: '#FFC53D', MODERATE: '#FF7A2F', SEVERE: '#FF3D5A',
};

function Gauge({ health, color }: { health: Num; color: string }) {
  const h = health ?? 0;
  const r = 70, c = 2 * Math.PI * r;
  return (
    <svg width="180" height="180" viewBox="0 0 180 180">
      <circle cx="90" cy="90" r={r} stroke="#1b2430" strokeWidth="14" fill="none" />
      <circle cx="90" cy="90" r={r} stroke={color} strokeWidth="14" fill="none" strokeLinecap="round"
        strokeDasharray={`${(c * h) / 100} ${c}`} transform="rotate(-90 90 90)" style={{ transition: 'stroke-dasharray .5s' }} />
      <text x="90" y="98" textAnchor="middle" fill="white" fontSize="40" fontFamily="Space Grotesk">{health == null ? '—' : Math.round(h)}</text>
      <text x="90" y="122" textAnchor="middle" fill="#7D8794" fontSize="11" letterSpacing="3">HEALTH</text>
    </svg>
  );
}

// Scrolling spectrogram for one mic ("ref" raw, "err" cleaned).
function Waterfall({ mic, listeners, title }: { mic: string; listeners: React.MutableRefObject<((s: Spec) => void)[]>; title: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [level, setLevel] = useState<number | null>(null);
  const [fmax, setFmax] = useState(4000);
  useEffect(() => {
    const onSpec = (s: Spec) => {
      if (s.mic !== mic || !canvas.current) return;
      const cv = canvas.current, ctx = cv.getContext('2d')!;
      ctx.drawImage(cv, 0, 1);                                // scroll down one row
      const row = ctx.createImageData(s.bins.length, 1);
      s.bins.forEach((db, i) => {
        const v = Math.max(0, Math.min(1, (db - 20) / 70));
        row.data.set([255 * Math.min(1, v * 2), 255 * Math.max(0, v * 2 - 0.6), 120 + 135 * (1 - v), 255], i * 4);
      });
      ctx.putImageData(row, 0, 0);
      const p = s.bins.reduce((a, db) => a + Math.pow(10, db / 10), 0);
      setLevel(10 * Math.log10(p / s.bins.length));
      setFmax(s.fmax);
    };
    listeners.current.push(onSpec);
    return () => { listeners.current = listeners.current.filter((f) => f !== onSpec); };
  }, [mic, listeners]);
  return (
    <div className="rounded border border-white/10 bg-[#0A1016] p-3">
      <div className="mb-2 flex justify-between text-[10px] uppercase tracking-[0.25em] text-muted">
        <span>{title}</span><span className="font-mono text-white">{level == null ? 'no data' : level.toFixed(1) + ' dB'}</span>
      </div>
      <canvas ref={canvas} width={128} height={120} className="h-40 w-full" style={{ imageRendering: 'pixelated' }} />
      <div className="mt-1 flex justify-between font-mono text-[10px] text-muted"><span>0 Hz</span><span>{fmax / 2} Hz</span><span>{fmax} Hz</span></div>
    </div>
  );
}

function ConfBar({ label, v }: { label: string; v: Num }) {
  const ok = v != null && v >= 0.5;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm"><span className="text-muted">{label}</span>
        <span className="font-mono">{v == null ? 'n/a' : v.toFixed(2)}</span></div>
      <div className="h-1.5 rounded bg-white/10">
        {v != null && <div className="h-1.5 rounded" style={{ width: `${v * 100}%`, background: ok ? '#3DFFA2' : '#FFC53D' }} />}
      </div>
    </div>
  );
}

export default function App() {
  const { tel, inf, connected, lastTs, specListeners } = useLive(VID);
  const [, tick] = useState(0);
  useEffect(() => { const i = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(i); }, []);
  const stale = Date.now() - lastTs > 3000;
  const health = inf.health ?? (inf.severity_raw != null ? 100 - inf.severity_raw * 100 : null);
  const tier = inf.tier ?? (health == null ? undefined : health > 79 ? 'HEALTHY' : health > 54 ? 'MILD' : health > 24 ? 'MODERATE' : 'SEVERE');
  const color = TIER_COLOR[tier ?? 'HEALTHY'] ?? '#3DF5FF';
  const fault = inf.fault ?? 'normal';

  return (
    <div className="min-h-screen bg-backdrop text-white">
      <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }} className="mx-auto max-w-7xl px-6 py-8">
        <header className="mb-6 flex items-center justify-between rounded-lg border border-white/10 bg-panel p-4 shadow-panel">
          <div>
            <div className="text-xs uppercase tracking-[0.35em] text-muted">QuietDrive</div>
            <div className="mt-2 font-display text-2xl">Live Vehicle / {VID}</div>
          </div>
          <div className="flex items-center gap-3">
            <div className={`h-2.5 w-2.5 rounded-full ${connected && !stale ? 'bg-signal' : 'bg-orange-500'}`} />
            <span className="font-mono text-sm uppercase tracking-[0.2em] text-muted">
              {!connected ? 'CONNECTING' : stale ? 'NO DATA' : 'LIVE'}
            </span>
          </div>
        </header>

        <main className="grid gap-6 xl:grid-cols-[1.6fr_0.9fr]">
          <section className="rounded-lg border border-white/10 bg-panel p-6 shadow-panel">
            <div className="flex flex-wrap items-center gap-6">
              <Gauge health={health} color={color} />
              <div className="grid flex-1 grid-cols-2 gap-3 md:grid-cols-3">
                <Tile label="RPM" value={fmt(tel.rpm)} />
                <Tile label="Sound" value={fmt(tel.spl_ref_db, 1, ' dB')} sub={tel.spl_err_db != null ? `cleaned ${fmt(tel.spl_err_db, 1, ' dB')}` : undefined} />
                <Tile label="Vibration" value={fmt(tel.vib_rms_g, 3, ' g')} />
                <Tile label="Current" value={fmt(tel.current_a, 1, ' A')} />
                <Tile label="Temp" value={fmt(tel.temp_c, 1, ' °C')} />
                <Tile label="Severity" value={fmt(inf.severity_raw != null ? inf.severity_raw * 3 : null, 1, ' / 3')} />
              </div>
            </div>
          </section>

          <aside className="rounded-lg border border-white/10 bg-panel p-6 shadow-panel">
            <div className="text-[10px] uppercase tracking-[0.25em] text-muted">Fault indicator</div>
            <div className="mt-3 rounded border bg-[#0A1016] p-4" style={{ borderColor: color + '55' }}>
              <div className="font-mono text-lg uppercase tracking-[0.2em]" style={{ color }}>{fault.replace('_', ' ')}</div>
              {inf.protected_hz && inf.protected_hz.length > 0 && (
                <div className="mt-2 text-xs text-muted">Protected (not cancelled): {inf.protected_hz.map((f) => f.toFixed(0) + ' Hz').join(', ')}</div>
              )}
            </div>
            <div className="mt-5 space-y-3">
              <ConfBar label="Sound" v={inf.conf?.audio} />
              <ConfBar label="Vibration" v={inf.conf?.vib} />
              <ConfBar label="Current" v={inf.conf?.current} />
            </div>
          </aside>
        </main>

        <section className="mt-6 grid gap-4 md:grid-cols-2">
          <Waterfall mic="ref" listeners={specListeners} title="Cabin mic (raw)" />
          <Waterfall mic="err" listeners={specListeners} title="After noise reduction" />
        </section>
      </motion.div>
    </div>
  );
}
