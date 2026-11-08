import { useEffect, useState } from 'react';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

type Tone = { name: string; hz: number; before_db: number; after_db: number; reduction_db: number; protected: boolean; present?: boolean };
type Result = { whine_hz: number; shaft_hz: number; rpm: number; overall_db: number; tones: Tone[]; note: string };
type Session = {
  source: string; session: string; synthetic: boolean; label?: string; level?: number; speed?: number;
  seconds?: number; result?: Result | null;
};

const file = (s: Session, name: string, v: number) => `${API_URL}/api/anc/file/${s.source}/${s.session}/${name}?v=${v}`;

// Fault-aware ANC (FxLMS) on recorded sessions, run by the backend.
export default function NoiseReduction() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sel, setSel] = useState<Session | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [version, setVersion] = useState(Date.now());

  const load = async () => {
    try {
      const r = await fetch(`${API_URL}/api/anc/sessions`);
      const j = await r.json();
      setSessions(j.sessions);
      setSel((cur) => (cur ? j.sessions.find((s: Session) => s.session === cur.session && s.source === cur.source) ?? null : null));
    } catch {
      setErr('Backend not reachable.');
    }
  };
  useEffect(() => { load(); }, []);

  const run = async (s: Session) => {
    setSel(s); setBusy(true); setErr(null);
    try {
      const r = await fetch(`${API_URL}/api/anc/run/${s.source}/${s.session}`, { method: 'POST' });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || 'ANC failed');
      setSel({ ...s, result: j.result });
      setVersion(Date.now());
      load();
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid grid-cols-12 gap-x-[clamp(24px,2.6vw,56px)]">
      <aside className="col-span-12 lg:col-span-4 xl:col-span-3">
        <div className="mb-3 flex items-baseline justify-between border-t-[1.5px] border-ink pt-2">
          <span className="lbl !text-ink"><span className="mr-3 text-mute">01</span>Recorded sessions</span>
          <span className="lbl">{sessions.length}</span>
        </div>
        {sessions.length === 0 && <p className="text-[15px] text-mute">No sessions yet. Record with bridge/record.py.</p>}
        <div className="max-h-[72vh] overflow-y-auto">
          {sessions.map((s) => {
            const active = sel?.session === s.session && sel?.source === s.source;
            return (
              <button key={s.source + s.session} onClick={() => setSel(s)}
                className={`block w-full border-b hair border-l-[3px] py-2.5 pl-3 pr-2 text-left transition-colors ${active ? 'border-l-ink bg-paper-2' : 'border-l-transparent hover:bg-paper-2/60'}`}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="num text-[14px] text-ink">{(s.label ?? '?').replace('_', ' ')}{s.level ? ` L${s.level}` : ''} <span className="font-medium text-mute">· speed {s.speed ?? '?'}</span></span>
                  {s.synthetic && <span className="lbl border border-ink px-1.5 !text-[10px] !text-ink">Synthetic</span>}
                </div>
                <div className="num mt-0.5 text-[11px] font-medium text-mute">
                  {s.session}{s.result ? ` · whine −${Math.max(...s.result.tones.filter((t) => !t.protected).map((t) => t.reduction_db)).toFixed(0)} dB` : ''}
                </div>
              </button>
            );
          })}
        </div>
      </aside>

      <section className="col-span-12 lg:col-span-8 xl:col-span-9">
        <div className="mb-5 flex items-baseline justify-between border-t-[1.5px] border-ink pt-2">
          <span className="lbl !text-ink"><span className="mr-3 text-mute">02</span>Fault-aware active noise cancellation</span>
          <span className="lbl">{sel?.synthetic ? 'Synthetic test motor' : 'Real recorded motor noise'} · simulated speaker path</span>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div>
            <h1 className="font-display text-[clamp(34px,2.6vw,52px)] font-black uppercase leading-[0.95] tracking-[-0.01em]">
              {sel ? <>{(sel.label ?? 'Session').replace('_', ' ')}{sel.level ? ` · L${sel.level}` : ''}</> : 'Pick a session'}
            </h1>
            <p className="mt-3 max-w-[72ch] text-[16px] leading-snug text-mute">
              Narrowband FxLMS cancels the motor whine (x1 and x2) using the whine tracked from the recording as reference.
              The speaker-to-ear path is simulated and learned by LMS first. The 1x shaft tone is protected, so a fault stays audible.
            </p>
          </div>
          {sel && (
            <button disabled={busy} onClick={() => run(sel)}
              className="num border-2 border-ink px-5 py-2 text-[14px] tracking-[0.12em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:opacity-40">
              {busy ? 'RUNNING ANC… ~10 S' : sel.result ? 'RUN AGAIN' : 'RUN ANC'}
            </button>
          )}
        </div>

        {err && <p className="num mt-5 border-l-[3px] border-severe pl-3 text-[14px] text-severe">{err}</p>}
        {!sel && <p className="mt-10 text-[16px] text-mute">Choose a recording from the list on the left.</p>}

        {sel?.result && !busy && (
          <>
            <table className="mt-7 w-full border-collapse">
              <thead>
                <tr className="border-b-[1.5px] border-ink">
                  {['Tone', 'Frequency', 'Before', 'After', 'Change', ''].map((h, i) => (
                    <th key={i} className={`lbl pb-2 font-medium ${i > 0 && i < 5 ? 'text-right' : 'text-left'} ${i === 5 ? 'pl-6' : ''}`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sel.result.tones.map((t) => {
                  const absent = t.protected && t.present === false;
                  return (
                    <tr key={t.name} className="border-b hair">
                      <td className="py-3 text-[16px] text-ink">{t.name}</td>
                      <td className="num py-3 text-right text-[16px]">{t.hz.toFixed(0)}<span className="ml-1 text-[12px] font-medium text-mute">Hz</span></td>
                      <td className="num py-3 text-right text-[16px]">{absent ? '—' : t.before_db.toFixed(0)}</td>
                      <td className="num py-3 text-right text-[16px]">{absent ? '—' : t.after_db.toFixed(0)}</td>
                      <td className={`num py-3 text-right text-[26px] ${t.protected ? 'text-ink' : 'text-teal'}`}>
                        {absent ? '—' : t.protected ? `${-t.reduction_db >= 0 ? '+' : ''}${(-t.reduction_db).toFixed(1)}` : `−${t.reduction_db.toFixed(1)}`}
                        {!absent && <span className="ml-1 text-[12px] font-medium text-mute">dB</span>}
                      </td>
                      <td className="py-3 pl-6 text-[14px] text-mute">
                        {absent ? "No 1x tone in this recording's sound (nothing to protect). Never targeted."
                          : t.protected ? <span className="num border-b-[3px] border-accent text-[13px] tracking-[0.06em] text-ink">PROTECTED — kept on purpose</span>
                            : 'Cancelled'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="mt-7 grid grid-cols-2 gap-x-10">
              {(['anc_before.wav', 'anc_after.wav'] as const).map((n) => (
                <div key={n} className="border-t hair pt-2">
                  <div className="lbl mb-2 !text-ink">{n === 'anc_before.wav' ? 'Before — recording' : 'After ANC'}</div>
                  <audio controls className="w-full" src={file(sel, n, version)} />
                </div>
              ))}
            </div>
            <figure className="mt-7 border-2 border-ink bg-white">
              <img className="block w-full" src={file(sel, 'anc_result.png', version)} alt="ANC result plot" />
            </figure>
          </>
        )}
      </section>
    </div>
  );
}
