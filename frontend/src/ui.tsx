// Shared datasheet-style primitives: eased numbers, labels, instrument scales.
import { useEffect, useRef, useState } from 'react';

export type Num = number | null | undefined;

export const SEV_COLOR: Record<string, string> = {
  HEALTHY: 'var(--sev-healthy)', MILD: 'var(--sev-mild)', MODERATE: 'var(--sev-moderate)', SEVERE: 'var(--sev-severe)',
};

export const isNum = (v: Num): v is number => v !== null && v !== undefined && isFinite(v);

const reduced = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** Eases a number toward its new value (~400 ms); jumps when reduced motion is requested. */
export function useEased(target: Num, ms = 400): Num {
  const [v, setV] = useState<Num>(target);
  const from = useRef<Num>(target);
  useEffect(() => {
    if (!isNum(target) || !isNum(from.current) || reduced()) { from.current = target; setV(target); return; }
    const a = from.current, b = target, t0 = performance.now();
    let raf = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3);
      const x = a + (b - a) * e;
      from.current = x; setV(x);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

/** A number in mono that eases; "—" when missing. */
export function N({ v, d = 0, className = '' }: { v: Num; d?: number; className?: string }) {
  const e = useEased(v);
  return <span className={`num ${className}`}>{isNum(e) ? e.toFixed(d) : '—'}</span>;
}

export function Label({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`lbl ${className}`}>{children}</div>;
}

/** Section heading: index number + title, ink rule above. */
export function Head({ n, children, right }: { n: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-4 border-t-[1.5px] border-ink pt-2">
      <div className="lbl text-ink"><span className="mr-3 text-mute">{n}</span>{children}</div>
      {right && <div className="lbl text-right">{right}</div>}
    </div>
  );
}

/** Printed instrument scale 0..1: minor ticks every .1, major at 0 / .5 / 1, a needle-like fill. */
export function Scale({ v, threshold = 0.5 }: { v: Num; threshold?: number }) {
  const e = useEased(v);
  const pct = isNum(e) ? Math.max(0, Math.min(1, e)) * 100 : 0;
  return (
    <div className="relative h-[22px] w-full">
      <div className="absolute inset-x-0 top-[7px] h-[6px] border border-ink bg-paper" />
      {isNum(e) && <div className="absolute left-0 top-[7px] h-[6px] bg-ink" style={{ width: `${pct}%` }} />}
      {Array.from({ length: 11 }, (_, i) => (
        <div key={i} className="absolute top-[13px] w-px bg-ink" style={{ left: `${i * 10}%`, height: i % 5 === 0 ? 8 : 4 }} />
      ))}
      <div className="absolute top-[1px] h-[6px] w-[2px] bg-ink" style={{ left: `calc(${threshold * 100}% - 1px)` }} />
    </div>
  );
}
