import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, MotionConfig, motion } from 'framer-motion';
import NoiseReduction from './NoiseReduction';
import { Head, Label, N, Scale, SEV_COLOR, isNum } from './ui';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const VID = 'QD-01';

type Num = number | null | undefined;
type Tel = { rpm?: Num; current_a?: Num; temp_c?: Num; vib_rms_g?: Num; spl_ref_db?: Num; spl_err_db?: Num; anc_on?: boolean; vib_xyz?: Num[] };
type Inf = {
  fault?: string; conf?: Record<string, Num>; severity_raw?: Num; protected_hz?: number[];
  points?: Num; health?: Num; tier?: string; action?: string; advice?: string; reason?: string;
  sensors_agree?: boolean; fix_db?: Num; fix_vib_pct?: Num; ts?: number;
  anc_connected?: boolean; anc_on?: boolean; anc_freq?: Num; anc_level_db?: Num; anc_reduction_db?: Num;
  anc_out?: Num; anc_at_limit?: boolean; anc_tracking?: boolean; anc_msg?: string;
};
type NR = { ts: number; ref?: number; err?: number; reduction?: Num; whine?: Num; notches?: number[] };
type Spec = { mic: string; fmax: number; bins: number[] };

// Merges the per-type live frames (tel, inf, spec ref/err) into one state.
function useLive(vid: string) {
  const [tel, setTel] = useState<Tel>({});
  const [inf, setInf] = useState<Inf>({});
  const [connected, setConnected] = useState(false);
  const [lastTs, setLastTs] = useState(0);
  const [nr, setNr] = useState<NR>({ ts: 0 });
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
        else if (m.t === 'spec' && Array.isArray(m.bins)) {
          specListeners.current.forEach((f) => f(m));
          const lvl = 10 * Math.log10(m.bins.reduce((a: number, db: number) => a + Math.pow(10, db / 10), 0) / m.bins.length);
          setNr((p) => ({ ...p, ts: Date.now(), [m.mic === 'err' ? 'err' : 'ref']: lvl,
            ...(m.mic === 'err' ? { reduction: m.reduction_db, whine: m.whine_hz, notches: m.notch_hz } : {}) }));
        }
      };
    };
    connect();
    return () => { window.clearTimeout(retry); socket.onclose = null; socket.close(); };
  }, [vid]);

  return { tel, inf, connected, lastTs, specListeners, nr };
}


// ---------------------------------------------------------------- helpers
const fmt = (v: Num, d = 0) => (isNum(v) ? v.toFixed(d) : '—');

// The backend simulator always sends vib_xyz as fixed 0.35 / 0.52 / 0.75 multiples of vib_rms_g;
// real sensors never do. Read from the existing tel fields only (no protocol change).
function looksSimulated(tel: Tel): boolean {
  const r = tel.vib_rms_g, xyz = tel.vib_xyz;
  if (!isNum(r) || r <= 0 || !xyz || xyz.length !== 3 || !xyz.every(isNum)) return false;
  return [0.35, 0.52, 0.75].every((k, i) => Math.abs((xyz[i] as number) - r * k) < 0.0015 + r * 0.002);
}

const ACTION: Record<string, [string, string]> = {
  ok: ['NO ACTION', 'Keep driving'],
  diy: ['DIY', 'Owner can fix it'],
  service_soon: ['SERVICE', 'Within a week'],
  service_now: ['SERVICE', 'Now'],
};

function Clock() {
  const [t, setT] = useState(() => new Date());
  useEffect(() => { const i = setInterval(() => setT(new Date()), 1000); return () => clearInterval(i); }, []);
  return <span className="num text-ink">{t.toLocaleTimeString('en-GB', { hour12: false })}</span>;
}

// ---------------------------------------------------------------- hero
function HealthNumber({ health }: { health: Num }) {
  return (
    <div className="flex items-start">
      <span className="font-display font-black leading-[0.82] tracking-[-0.045em] text-ink"
        style={{ fontSize: 'clamp(128px, 10.4vw, 200px)' }}>
        <N v={isNum(health) ? Math.round(health) : health} className="font-display font-black" />
      </span>
      <span className="num ml-2 mt-2 text-[22px] text-mute">/100</span>
    </div>
  );
}

function Advice({ inf, tier, health }: { inf: Inf; tier?: string; health: Num }) {
  const hasInf = inf.fault != null;
  const points = inf.points ?? (isNum(health) ? Math.round(100 - health) : null);
  const advice = inf.advice ?? (hasInf ? (inf.fault === 'normal' ? 'No action needed.' : `Detected: ${inf.fault?.replace('_', ' ')}`) : null);
  const [act, when] = ACTION[inf.action ?? 'ok'] ?? ACTION.ok;
  const fixLine = isNum(inf.fix_db) && inf.fix_db >= 0.5
    ? <>Fixing this should make the motor about <b className="num">{inf.fix_db.toFixed(1)} dB</b> quieter</>
    : isNum(inf.fix_vib_pct) && inf.fix_vib_pct >= 5
      ? <>Fixing this should cut motor vibration by about <b className="num">{Math.round(inf.fix_vib_pct)}%</b></>
      : null;
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3 border-b hair pb-3">
        <dl className="flex gap-10">
          <div><Label>Tier</Label><dd className="num mt-1 text-[18px] text-ink">{tier ?? '—'}</dd></div>
          <div><Label>Problem points</Label><dd className="mt-1 text-[18px] text-ink"><N v={points} /><span className="num text-mute"> / 100</span></dd></div>
        </dl>
        <div className="flex items-stretch border-2 border-ink">
          <span className="num bg-paper px-3 py-1.5 text-[15px] tracking-[0.12em] text-ink">{act}</span>
          <span className="lbl flex items-center border-l-2 border-ink px-3 !text-ink">{when}</span>
        </div>
      </div>
      <Label className="mt-5">Advice</Label>
      <p className="mt-1 max-w-[40ch] font-sans font-medium leading-[1.18] tracking-[-0.01em] text-ink"
        style={{ fontSize: 'clamp(22px, 1.68vw, 32px)' }}>
        {advice ?? 'Waiting for the first diagnosis…'}
      </p>
      {inf.reason && <p className="mt-3 max-w-[68ch] text-[16px] leading-snug text-mute">{inf.reason}</p>}
      {fixLine && (
        <p className="mt-auto pt-4 font-sans text-[16px] text-teal">
          <span className="lbl mr-3 !text-teal">After fix</span>{fixLine}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- fault block
function Fault({ inf }: { inf: Inf }) {
  const stopped = inf.fault === 'stopped';
  const fault = stopped ? 'Motor stopped' : (inf.fault ?? '—').replace('_', ' ');
  const gauge = (name: string, v: Num) => {
    const ok = isNum(v) && v >= 0.5;
    return (
      <div>
        <div className="flex items-baseline justify-between">
          <span className="num text-[14px] text-ink">{name} <span className={ok ? 'text-teal' : 'text-mute'}>{!isNum(v) ? '' : ok ? '✓' : '?'}</span></span>
          <span>
            {isNum(v) ? <><N v={v} d={2} className="text-[14px] text-ink" /><span className="lbl ml-1.5 !text-[11px]">{ok ? 'confirms' : 'unsure'}</span></>
              : <span className="text-[13px] text-mute">not fitted</span>}
          </span>
        </div>
        <Scale v={v} />
      </div>
    );
  };
  return (
    <div>
      <Label>Fault</Label>
      <div className={`mt-1 break-words font-display text-[clamp(30px,2.55vw,50px)] font-black uppercase leading-[0.92] tracking-[-0.01em] ${stopped ? 'text-mute' : 'text-ink'}`}>
        {fault}
      </div>
      <div className="mt-5 space-y-1.5">
        {gauge('Sound', inf.conf?.audio)}
        {gauge('Vibration', inf.conf?.vib)}
        {gauge('Current', inf.conf?.current)}
      </div>
      <div className="num flex justify-between text-[10px] font-medium text-mute"><span>0</span><span>confidence 0.5</span><span>1</span></div>
      <p className="mt-2 text-[14px] text-mute">
        {inf.sensors_agree == null ? ' ' : inf.sensors_agree ? 'Sound and vibration agree.' : 'Sensors disagree: points reduced by 30%.'}
      </p>
      {inf.protected_hz && inf.protected_hz.length > 0 && (
        <div className="mt-4">
          <span className="num inline-block whitespace-nowrap border-b-[3px] border-accent pb-0.5 text-[15px] tracking-[0.06em] text-ink">
            PROTECTED {inf.protected_hz.map((f) => f.toFixed(0)).join(' / ')} Hz
          </span>
          <span className="num ml-2 whitespace-nowrap text-[13px] font-medium text-mute">— not cancelled</span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- datasheet
function Datasheet({ tel, inf }: { tel: Tel; inf: Inf }) {
  const rows: [string, Num, number, string, React.ReactNode?][] = [
    ['Shaft speed', tel.rpm, 0, 'rpm'],
    ['Sound level', tel.spl_ref_db, 1, 'dB', isNum(tel.spl_err_db) ? <>cleaned <span className="num text-ink">{fmt(tel.spl_err_db, 1)}</span> dB</> : undefined],
    ['Vibration', tel.vib_rms_g, 3, 'g rms'],
    ['Current', tel.current_a, 1, 'A'],
    ['Temperature', tel.temp_c, 1, '°C'],
    ['Severity', isNum(inf.severity_raw) ? inf.severity_raw * 3 : null, 1, '/ 3'],
  ];
  return (
    <table className="w-full border-collapse">
      <tbody>
        {rows.map(([label, v, d, unit, sub]) => (
          <tr key={label} className="border-b hair">
            <th className="lbl py-[10px] pr-3 text-left align-baseline font-medium">{label}</th>
            <td className="py-[10px] text-right align-baseline">
              {isNum(v)
                ? <N v={v} d={d} className="text-[clamp(22px,1.55vw,30px)] leading-none text-ink" />
                : <span className="num text-[clamp(22px,1.55vw,30px)] leading-none text-line">—</span>}
              {sub && <div className="lbl mt-1 whitespace-nowrap !normal-case !tracking-normal">{sub}</div>}
            </td>
            <td className="w-[64px] py-[10px] pl-2 align-baseline">
              {isNum(v) ? <span className="num text-[13px] font-medium text-mute">{unit}</span>
                : <span className="whitespace-nowrap text-[12px] text-mute">{label === 'Severity' ? 'none' : 'not fitted'}</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------- spectrum
const sharedPeak = { v: -1e9 };
const COLS = 360;                              // one column per spectrum frame (~70 s at 5 frames/s)

// paper -> ink -> orange
const PAPER = [242, 238, 228], INK = [21, 23, 28], ORANGE = [255, 90, 31];
function cmap(v: number): [number, number, number] {
  const mix = (a: number[], b: number[], k: number) => a.map((x, i) => x + (b[i] - x) * k) as [number, number, number];
  return v < 0.62 ? mix(PAPER, INK, Math.pow(v / 0.62, 0.9)) : mix(INK, ORANGE, (v - 0.62) / 0.38);
}

// One strip of the waterfall: newest column on the right, history moving left across the full width.
function Strip({ mic, listeners, title, protectedHz, height, onRate }: {
  mic: string; listeners: React.MutableRefObject<((s: Spec) => void)[]>; title: string;
  protectedHz?: number[]; height: string; onRate?: (sPerCol: number) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [level, setLevel] = useState<number | null>(null);
  const [fmax, setFmax] = useState(4000);
  const last = useRef(0), dt = useRef(0);
  useEffect(() => {
    const onSpec = (s: Spec) => {
      if (s.mic !== mic || !canvas.current) return;
      const now = performance.now();
      if (last.current) { const d = (now - last.current) / 1000; dt.current = dt.current ? dt.current * 0.9 + d * 0.1 : d; onRate?.(dt.current); }
      last.current = now;
      const cv = canvas.current, ctx = cv.getContext('2d')!;
      if (cv.height !== s.bins.length) { cv.height = s.bins.length; ctx.fillStyle = '#F2EEE4'; ctx.fillRect(0, 0, cv.width, cv.height); }
      ctx.drawImage(cv, -1, 0);
      const col = ctx.createImageData(1, s.bins.length);
      sharedPeak.v = Math.max(Math.max(...s.bins), sharedPeak.v - 0.1);   // one scale for raw and cleaned
      s.bins.forEach((db, i) => {
        const v = Math.max(0, Math.min(1, (db - (sharedPeak.v - 50)) / 50));
        const [r, g, b] = cmap(v);
        col.data.set([r, g, b, 255], (s.bins.length - 1 - i) * 4);
      });
      ctx.putImageData(col, cv.width - 1, 0);
      setLevel(10 * Math.log10(s.bins.reduce((a, db) => a + Math.pow(10, db / 10), 0) / s.bins.length));
      setFmax(s.fmax);
    };
    listeners.current.push(onSpec);
    return () => { listeners.current = listeners.current.filter((f) => f !== onSpec); };
  }, [mic, listeners, onRate]);

  const ticks = [0, 1000, 2000, 3000, 4000].filter((f) => f <= fmax);
  return (
    <div className="grid grid-cols-[56px_1fr]">
      <div />
      <div className="mb-1 flex items-baseline justify-between">
        <span className="lbl !text-ink">{title}</span>
        <span className="lbl">{level == null ? 'no signal' : <><span className="num text-[14px] text-ink">{level.toFixed(1)}</span> dB</>}</span>
      </div>
      <div className="relative" style={{ height }}>
        <span className="num absolute -top-[19px] right-3 text-[10px] font-medium text-mute">Hz</span>
        {ticks.map((f) => (
          <div key={f} className="absolute right-0 flex -translate-y-1/2 items-center gap-1.5" style={{ top: `${100 - (f / fmax) * 100}%` }}>
            <span className="num text-[11px] font-medium text-mute">{f === 0 ? '0' : `${f / 1000}k`}</span>
            <span className="h-px w-2 bg-ink" />
          </div>
        ))}
      </div>
      <div className="relative border-2 border-ink bg-paper" style={{ height }}>
        <canvas ref={canvas} width={COLS} height={128} className="block h-full w-full" style={{ imageRendering: 'pixelated' }} />
        {protectedHz && protectedHz.length > 0 && (() => {
          // one hatched band spanning all protected tones (they sit close together on a 0-4 kHz axis)
          const lo = Math.min(...protectedHz), hi = Math.max(...protectedHz);
          const top = 100 - (hi / fmax) * 100, bottom = 100 - (lo / fmax) * 100;
          return (
            <div className="pointer-events-none absolute inset-x-0"
              style={{ top: `calc(${top}% - 5px)`, height: `calc(${bottom - top}% + 10px)` }}>
              <div className="h-full w-full border-y border-accent"
                style={{ backgroundImage: 'repeating-linear-gradient(-45deg, var(--accent) 0 1.5px, transparent 1.5px 6px)', opacity: 0.85 }} />
              <span className="num absolute right-2 -top-[18px] bg-paper px-1 text-[11px] text-accent">
                PROTECTED {protectedHz.map((f) => f.toFixed(0)).join(' / ')} Hz
              </span>
            </div>
          );
        })()}
      </div>
    </div>
  );
}

function Spectrum({ listeners, protectedHz }: { listeners: React.MutableRefObject<((s: Spec) => void)[]>; protectedHz?: number[] }) {
  const [sPerCol, setSPerCol] = useState(0.2);
  const onRate = useRef((d: number) => setSPerCol(d)).current;
  const span = sPerCol * COLS;
  const step = span > 240 ? 60 : span > 100 ? 20 : span > 40 ? 10 : 5;
  const ticks = Array.from({ length: Math.floor(span / step) + 1 }, (_, i) => i * step);
  return (
    <div>
      <Strip mic="ref" listeners={listeners} title="Cabin mic — raw" protectedHz={protectedHz} height="clamp(140px, 16.5vh, 200px)" onRate={onRate} />
      <div className="h-3" />
      <Strip mic="err" listeners={listeners} title="After noise reduction" height="clamp(76px, 8.5vh, 104px)" />
      <div className="grid grid-cols-[56px_1fr]">
        <span />
        <div className="relative h-6">
          {ticks.map((t) => (
            <div key={t} className={`absolute top-0 flex flex-col ${t === 0 ? '-translate-x-full items-end' : '-translate-x-1/2 items-center'}`} style={{ left: `${100 - (t / span) * 100}%` }}>
              <span className="h-1.5 w-px bg-ink" />
              <span className="num text-[11px] font-medium text-mute">{t === 0 ? 'now' : `−${t} s`}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- noise reduction
function NoiseMeter({ nr }: { nr: NR }) {
  const active = nr.err != null && Date.now() - nr.ts < 3000;
  const lo = 20, hi = 100;
  const pos = (v: Num) => (isNum(v) ? Math.max(0, Math.min(1, (v - lo) / (hi - lo))) * 100 : 0);
  const bar = (label: string, v: Num, cls: string) => (
    <div className="grid grid-cols-[96px_1fr_72px] items-center gap-3">
      <span className="lbl !text-ink">{label}</span>
      <div className="relative h-[10px] border-b border-ink">
        {isNum(v) && <div className={`absolute bottom-0 left-0 h-[8px] ${cls} transition-[width] duration-500`} style={{ width: `${pos(v)}%` }} />}
      </div>
      <span className="text-right"><N v={v} d={1} className="text-[16px] text-ink" /><span className="lbl ml-1 !text-[11px]">dB</span></span>
    </div>
  );
  return (
    <div className="grid grid-cols-12 items-end gap-x-8">
      <div className="col-span-8 space-y-3">
        {bar('Raw', nr.ref, 'bg-ink')}
        {bar('Cleaned', active ? nr.err : undefined, 'bg-teal')}
        <div className="grid grid-cols-[96px_1fr_72px] gap-3">
          <span />
          <div className="relative h-5">
            {Array.from({ length: 9 }, (_, i) => lo + i * 10).map((d) => (
              <div key={d} className="absolute top-0 flex -translate-x-1/2 flex-col items-center" style={{ left: `${pos(d)}%` }}>
                <span className="h-1.5 w-px bg-ink" /><span className="num text-[10px] font-medium text-mute">{d}</span>
              </div>
            ))}
          </div>
          <span />
        </div>
        <p className="lbl !normal-case !tracking-normal !text-[13px]">
          {active
            ? <>Whine <span className="num text-ink">{fmt(nr.whine, 0)} Hz</span> · notches <span className="num text-ink">{(nr.notches ?? []).map((f) => f.toFixed(0)).join(' / ') || 'off'}</span></>
            : 'Run bridge/clean_voice.py to see live noise removal.'}
        </p>
      </div>
      <div className="col-span-4 border-l-[1.5px] border-ink pl-6">
        <Label>Whine removed</Label>
        <div className="num mt-1 text-[clamp(40px,3.4vw,64px)] leading-none tracking-[-0.03em] text-ink">
          {active && isNum(nr.reduction) ? <><N v={-Math.abs(nr.reduction)} d={1} /><span className="ml-2 text-[0.42em] text-mute">dB</span></>
            : <span className="font-sans text-[18px] font-normal tracking-normal text-mute">no measurement yet</span>}
        </div>
      </div>
    </div>
  );
}

// On-board ANC (ESP32) status, merged into inf by bridge/live.py --esp32.
function EspAnc({ inf }: { inf: Inf }) {
  if (!inf.anc_connected) {
    return (
      <div className="mt-6 border-t hair pt-3">
        <Label>On-board ANC (ESP32)</Label>
        <p className="mt-2 text-[13px] text-mute">ESP32 not connected</p>
      </div>
    );
  }
  const out = isNum(inf.anc_out) ? Math.max(0, Math.min(1, inf.anc_out)) : null;
  return (
    <div className="mt-6 border-t hair pt-3">
      <div className="flex items-baseline justify-between gap-4">
        <Label>On-board ANC (ESP32)</Label>
        <span className={`num border-[1.5px] px-2 py-[1px] text-[12px] font-medium tracking-[0.1em] ${inf.anc_on ? 'border-ink bg-ink text-paper' : 'border-ink text-ink'}`}>
          {inf.anc_on ? 'ON' : 'OFF'}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-12 items-end gap-x-8">
        <div className="col-span-8 space-y-3">
          <div className="flex items-baseline gap-2">
            <span className="lbl !text-ink w-[96px]">Target</span>
            <N v={inf.anc_freq} d={1} className="text-[16px] text-ink" /><span className="lbl !text-[11px]">Hz</span>
            <span className="lbl ml-auto">speed tracking <span className="num text-ink">{inf.anc_tracking ? 'ON' : 'OFF'}</span></span>
          </div>
          <div className="grid grid-cols-[96px_1fr_72px] items-center gap-3">
            <span className="lbl !text-ink">Speaker out</span>
            <div className="relative h-[10px] border-b border-ink">
              {out != null && <div className={`absolute bottom-0 left-0 h-[8px] ${inf.anc_at_limit ? 'bg-severe' : 'bg-ink'} transition-[width] duration-500`} style={{ width: `${out * 100}%` }} />}
            </div>
            <span className="text-right"><N v={inf.anc_out} d={2} className="text-[16px] text-ink" /></span>
          </div>
          {inf.anc_at_limit && <p className="num text-[12px] font-medium tracking-[0.1em]" style={{ color: 'var(--sev-severe)' }}>AT LIMIT</p>}
          {inf.anc_msg && <p className="truncate text-[12px] text-mute">{inf.anc_msg}</p>}
        </div>
        <div className="col-span-4 border-l-[1.5px] border-ink pl-6">
          <Label>Reduction</Label>
          <div className="num mt-1 text-[clamp(40px,3.4vw,64px)] leading-none tracking-[-0.03em] text-ink">
            {isNum(inf.anc_reduction_db) ? <><N v={inf.anc_reduction_db} d={1} /><span className="ml-2 text-[0.42em] text-mute">dB</span></>
              : <span className="font-sans text-[18px] font-normal tracking-normal text-mute">waiting for status</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- page
export default function App() {
  const { tel, inf, connected, lastTs, specListeners, nr } = useLive(VID);
  const [page, setPage] = useState<'live' | 'anc'>('live');
  const [, tick] = useState(0);
  useEffect(() => { const i = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(i); }, []);
  const stale = Date.now() - lastTs > 3000;
  const health = inf.health ?? (inf.severity_raw != null ? 100 - inf.severity_raw * 100 : null);
  const tier = inf.tier ?? (health == null ? undefined : health >= 80 ? 'HEALTHY' : health >= 55 ? 'MILD' : health >= 25 ? 'MODERATE' : 'SEVERE');
  const color = SEV_COLOR[tier ?? ''] ?? 'var(--mute)';
  const [status, dot] = !connected ? ['DISCONNECTED', 'var(--sev-severe)'] : stale ? ['NO DATA', 'var(--sev-mild)']
    : looksSimulated(tel) ? ['SIMULATED', 'var(--sev-mild)'] : ['LIVE', 'var(--teal)'];

  return (
    <MotionConfig reducedMotion="user">
      <div className="mx-auto min-h-screen max-w-[1920px] px-[clamp(20px,2.5vw,48px)] pb-6 text-ink">
        <header className="flex items-center justify-between gap-6 border-b-[3px] border-ink py-3.5">
          <div className="flex items-baseline gap-10">
            <span className="num text-[15px] tracking-[0.14em]">PRAYOG ’26 <span className="mx-2 text-mute">|</span> QUIETDRIVE</span>
            <nav className="flex gap-6">
              {([['live', 'Live'], ['anc', 'Noise reduction']] as const).map(([k, l]) => (
                <button key={k} onClick={() => setPage(k)}
                  className={`lbl border-b-2 pb-0.5 transition-colors ${page === k ? 'border-ink !text-ink' : 'border-transparent hover:!text-ink'}`}>{l}</button>
              ))}
            </nav>
          </div>
          <div className="flex items-baseline gap-8">
            <span className="lbl">Vehicle <span className="num ml-1 text-[14px] text-ink">{VID}</span></span>
            <span className="lbl flex items-center gap-2 !text-ink">
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: dot }} />{status}
            </span>
            <Clock />
          </div>
        </header>

        {page === 'anc' ? <div className="pt-8"><NoiseReduction /></div> : (
          <main className="grid grid-cols-12 gap-x-[clamp(24px,2.6vw,56px)]">
            {/* hero */}
            <section className="col-span-12 grid grid-cols-12 gap-x-[clamp(24px,2.6vw,56px)] pb-7 pt-6">
              <div className="col-span-3">
                <Label>Motor health</Label>
                <div className="mt-2"><HealthNumber health={health} /></div>
                <div className="mt-4 h-[38px]">
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.div key={tier ?? 'none'} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.35 }}
                      className="font-display text-[clamp(26px,1.9vw,36px)] font-black uppercase leading-none tracking-[0.03em]" style={{ color }}>
                      {tier ?? 'No data'}
                    </motion.div>
                  </AnimatePresence>
                </div>
              </div>
              <div className="col-span-6 border-l-[1.5px] border-ink pl-[clamp(20px,2vw,40px)]">
                <Advice inf={inf} tier={tier} health={health} />
              </div>
              <div className="col-span-3 border-l-[1.5px] border-ink pl-[clamp(20px,2vw,40px)]">
                <Fault inf={inf} />
              </div>
            </section>

            {/* readings + spectrum */}
            <section className="col-span-3">
              <Head n="01">Readings</Head>
              <Datasheet tel={tel} inf={inf} />
            </section>
            <section className="col-span-9">
              <Head n="02" right="paper → ink → orange · shared scale">Spectrum waterfall, 0–4 kHz</Head>
              <Spectrum listeners={specListeners} protectedHz={inf.fault && inf.fault !== 'normal' ? inf.protected_hz : undefined} />
              <div className="mt-6">
                <Head n="03" right="cabin mic, live">Noise reduction</Head>
                <NoiseMeter nr={nr} />
                <EspAnc inf={inf} />
              </div>
            </section>
          </main>
        )}
      </div>
    </MotionConfig>
  );
}
