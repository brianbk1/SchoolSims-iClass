import { useCallback, useEffect, useMemo, useState } from 'react';
import ExtendSimPanel from '../components/ExtendSimPanel';

// ---- types -----------------------------------------------------------------
type Status = 'draft' | 'pending_review' | 'published' | 'expired' | 'superseded';
interface SimRow {
  id: string; title: string; status: Status; builtin: boolean; source: string; parent_id: string | null;
  created_by: string; created_at: string; submitted_at: string; expires_at: string;
  reviewed_by: string; reviewed_at: string; review_note: string; context_summary: string;
  languages: string[]; decisions: number; slides: number; problems: number;
}
type AnyNode = Record<string, any>;
interface Block { nodes: Record<string, AnyNode>; ui: Record<string, any> }
interface SimDetail extends SimRow {
  inputs: Record<string, any>;
  scenario: { start_node_id: string } & Record<string, Block | string>;
  images: Record<string, string>;
  review_notes: string[]; problems_list?: string[];
  translation_notes: Record<string, string[]>;
  ada: { passed: boolean; issues: string[]; notes: string[] };
  default_look: string; accent: string; pending_langs: string[];
  media: Media | null;
}
interface Who {
  role: 'reviewer' | 'builder'; reviewer_email: string; generator_ready: boolean; images_ready: boolean;
  email_ready: boolean; storage: string; persistent: boolean; review_days: number;
  heygen_ready?: boolean; blob_ready?: boolean;
  personas: { id: string; label: string; photo: string }[]; app_languages: string[];
  language_options: LangOpt[];
}
interface Cell { ready: number; rendering: number; failed: number; stale: number; total: number; errors: string[] }
interface Media {
  video_nodes: string[]; grid: Record<string, Record<string, Cell>>; seconds_per_set: number;
  suggested: Record<string, string>; images: { total: number; done: number; missing: string[] };
  heygen_ready: boolean; blob_ready: boolean; images_ready: boolean; looks: { id: string; label: string }[];
}
export interface LangOpt { code: string; label: string; native: string; place: string }
interface ReportRow { id: string; title: string; context_summary: string; created_by: string; created_at: string; status: Status; languages: string[]; decisions: number; source: string }
interface Note { to: string; subject: string; text: string; at: string; sent: boolean; error: string }

// ---- helpers -----------------------------------------------------------------
const TOKEN_KEY = 'iclass_admin_token';
const token = () => { try { return localStorage.getItem(TOKEN_KEY) ?? ''; } catch { return ''; } };
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', 'X-Admin-Token': token(), ...(init.headers ?? {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(body.detail ?? `HTTP ${r.status}`), { status: r.status });
  return body as T;
}
const remember = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };
const recall = (k: string) => { try { return localStorage.getItem(k) ?? ''; } catch { return ''; } };
const day = (s: string) => (s ? new Date(s).toLocaleDateString() : '');

const STATUS_LABEL: Record<Status, string> = {
  draft: 'Draft', pending_review: 'Pending review', published: 'Published', expired: 'Expired', superseded: 'Replaced by extension',
};
const STATUS_TONE: Record<Status, [string, string]> = {
  draft: ['#f1f5f9', '#334155'], pending_review: ['#e0e7ff', '#3730a3'], published: ['#dcfce7', '#166534'],
  expired: ['#fee2e2', '#991b1b'], superseded: ['#f1f5f9', '#64748b'],
};
const TONES = ['Realistic / challenging', 'Supportive / coaching', 'High-pressure / urgent', 'Reflective / exploratory'];
const SKIP_KEYS = new Set(['type', 'next_node_id', 'id', 'target_node_id', 'key', 'direction', 'image_url', 'character_name', 'speech_locale']);
const TYPE_LABEL: Record<string, string> = { context: 'Scene', text_image: 'Character', decision: 'Decision', outcome: 'Outcome', summary: 'Summary', video: 'Video' };

/** Editable text fields of a slide: path ("nodes.d1.prompt") -> text. */
function nodeFields(id: string, node: AnyNode): [string, string][] {
  const out: [string, string][] = [];
  const walk = (v: any, path: string) => {
    if (typeof v === 'string') out.push([path, v]);
    else if (Array.isArray(v)) v.forEach((x, i) => walk(x, `${path}.${i}`));
    else if (v && typeof v === 'object') for (const [k, x] of Object.entries(v)) if (!SKIP_KEYS.has(k)) walk(x, `${path}.${k}`);
  };
  for (const [k, v] of Object.entries(node)) if (!SKIP_KEYS.has(k)) walk(v, `nodes.${id}.${k}`);
  return out;
}
const fieldLabel = (path: string) => path.split('.').slice(2).join(' › ').replace(/_/g, ' ').replace(/\b(\d+)\b/g, n => `#${+n + 1}`);

/** Slides in reading order (breadth-first from the start). */
function slideOrder(nodes: Record<string, AnyNode>, start: string): string[] {
  const seen: string[] = []; const q = [start];
  while (q.length) {
    const id = q.shift()!;
    if (seen.includes(id) || !nodes[id]) continue;
    seen.push(id);
    const n = nodes[id];
    if (n.type === 'decision') q.push(...n.choices.map((c: any) => c.target_node_id));
    else if (n.next_node_id) q.push(n.next_node_id);
  }
  return [...seen, ...Object.keys(nodes).filter(k => !seen.includes(k))];
}

/** Track (list of slide ids) starting at `first`, up to the next decision/summary. */
function trackSlides(nodes: Record<string, AnyNode>, first: string): string[] {
  const out: string[] = []; let id: string | null = first;
  while (id && nodes[id] && !['decision', 'summary'].includes(nodes[id].type) && !out.includes(id)) { out.push(id); id = nodes[id].next_node_id; }
  return out;
}

/** Translate a sim into several languages, a few at a time (each is one request). */
async function translateMany(id: string, codes: string[], onProgress: (done: number, current: string, errors: string[]) => void): Promise<{ last: any; errors: string[] }> {
  const queue = [...codes]; const errors: string[] = []; let done = 0; let last: any = null;
  const worker = async () => {
    while (queue.length) {
      const code = queue.shift()!;
      onProgress(done, code, errors);
      try {
        last = await api(`/api/admin/sims/${encodeURIComponent(id)}/languages`, { method: 'POST', body: JSON.stringify({ code }) });
      } catch (e: any) { errors.push(`${code}: ${e.message}`); }
      done += 1; onProgress(done, '', errors);
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  return { last, errors };
}

export function LangPicker({ options, chosen, setChosen, exclude = [] }: { options: LangOpt[]; chosen: string[]; setChosen: (c: string[]) => void; exclude?: string[] }) {
  const list = options.filter(o => o.code !== 'en' && !exclude.includes(o.code));
  const all = list.length > 0 && list.every(o => chosen.includes(o.code));
  return (
    <div>
      <label className="sb-check" style={{ fontWeight: 600, marginBottom: 6 }}>
        <input type="checkbox" checked={all} onChange={e => setChosen(e.target.checked ? list.map(o => o.code) : [])} /> All languages ({list.length})
      </label>
      <div className="sb-langs">
        {list.map(o => (
          <label key={o.code} className="sb-check" title={o.place ? `Set in ${o.place}` : ''}>
            <input type="checkbox" checked={chosen.includes(o.code)}
              onChange={e => setChosen(e.target.checked ? [...chosen, o.code] : chosen.filter(c => c !== o.code))} />
            <bdi>{o.native}</bdi> <span style={{ color: 'var(--text-3)', fontSize: 12 }}>{o.code}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

// ---- styles -----------------------------------------------------------------
const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, padding: 20 };
const lbl: React.CSSProperties = { display: 'block', fontSize: 12, fontWeight: 700, color: '#1e2a5a', margin: '0 0 5px' };
const inp: React.CSSProperties = { width: '100%', boxSizing: 'border-box', border: '1px solid var(--border2)', borderRadius: 8, padding: '9px 11px', fontSize: 14, fontFamily: 'inherit', background: 'var(--surface)', color: 'var(--text-1)' };
const ghost: React.CSSProperties = { fontSize: 13, background: 'var(--surface)', border: '1px solid var(--border2)', borderRadius: 8, padding: '7px 12px', cursor: 'pointer', color: 'var(--text-2)' };
const primary: React.CSSProperties = { fontSize: 14, fontWeight: 600, background: '#8b1e3f', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 18px', cursor: 'pointer' };
const navy: React.CSSProperties = { ...primary, background: '#1e2a5a' };
const banner = (tone: 'warn' | 'bad' | 'ok' | 'info'): React.CSSProperties => ({
  borderRadius: 10, padding: '10px 14px', fontSize: 13, lineHeight: 1.5, margin: '0 0 12px',
  background: { bad: '#fef2f2', warn: '#fffbeb', ok: '#f0fdf4', info: '#eef2ff' }[tone],
  border: `1px solid ${{ bad: '#fecaca', warn: '#fde68a', ok: '#bbf7d0', info: '#c7d2fe' }[tone]}`,
  color: { bad: '#991b1b', warn: '#92400e', ok: '#166534', info: '#3730a3' }[tone],
});
const h2: React.CSSProperties = { fontSize: 20, fontWeight: 700, color: '#1e2a5a', margin: '0 0 16px' };

function Chip({ status, extra }: { status: Status; extra?: string }) {
  const [bg, fg] = STATUS_TONE[status];
  return <span style={{ fontSize: 11, fontWeight: 700, background: bg, color: fg, borderRadius: 999, padding: '3px 10px', whiteSpace: 'nowrap' }}>{STATUS_LABEL[status]}{extra ? ` · ${extra}` : ''}</span>;
}

// ---- main ---------------------------------------------------------------------
type View = { kind: 'new' } | { kind: 'sim'; id: string } | { kind: 'report' } | { kind: 'notifications' } | { kind: 'review' };

/** The admin's address bar is its menu: #new, #review, #report, #notifications, #sim=<id>. */
export function viewFromHash(hash = window.location.hash): View {
  const m = hash.match(/sim=([^&]+)/);
  if (m) return { kind: 'sim', id: decodeURIComponent(m[1]) };
  const h = hash.replace('#', '');
  if (h === 'report' || h === 'notifications' || h === 'review') return { kind: h };
  return { kind: 'new' };
}
const hashFor = (v: View) => (v.kind === 'sim' ? `sim=${encodeURIComponent(v.id)}` : v.kind === 'new' ? 'new' : v.kind);

export default function SimBuilder({ onNeedToken }: { onNeedToken: (msg: string) => void }) {
  const [who, setWho] = useState<Who | null>(null);
  const [list, setList] = useState<SimRow[]>([]);
  const [view, setView] = useState<View>(() => viewFromHash());
  // Header links and the browser's back button change the address; follow them.
  useEffect(() => {
    const onHash = () => { setView(viewFromHash()); setError(null); setNotice(null); };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [w, l] = await Promise.all([api<Who>('/api/admin/whoami'), api<{ sims: SimRow[] }>('/api/admin/sims')]);
      setWho(w); setList(l.sims);
    } catch (e: any) {
      if (e.status === 401 || e.status === 403) onNeedToken(e.message); else setError(e.message);
    }
  }, [onNeedToken]);
  useEffect(() => { load(); }, [load]);

  const go = (v: View) => {
    setError(null); setNotice(null); setView(v);
    window.location.hash = hashFor(v);
  };

  const groups: [string, SimRow[]][] = [
    ['Waiting for review', list.filter(s => s.status === 'pending_review')],
    ['Drafts', list.filter(s => s.status === 'draft')],
    ['Published', list.filter(s => s.status === 'published')],
    ['Expired / replaced', list.filter(s => s.status === 'expired' || s.status === 'superseded')],
  ];

  return (
    <div className="sb-layout">
      <aside style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <button style={{ ...primary, width: '100%' }} onClick={() => go({ kind: 'new' })}>✦ Create Sim from AI</button>
        {who && (
          <p style={{ fontSize: 12, color: 'var(--text-3)', margin: 0 }}>
            Signed in as <b>{who.role === 'reviewer' ? 'SchoolSims reviewer' : 'sim builder'}</b>
          </p>
        )}
        {groups.map(([title, rows]) => rows.length > 0 && (
          <div key={title} style={{ ...card, padding: 12 }}>
            <p style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 4px 6px' }}>{title} ({rows.length})</p>
            {rows.map(s => (
              <button key={s.id} onClick={() => go({ kind: 'sim', id: s.id })} className="sb-row"
                style={{ background: view.kind === 'sim' && view.id === s.id ? 'rgba(30,42,90,0.07)' : 'transparent' }}>
                <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-1)' }}>{s.title}</span>
                <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
                  {s.builtin ? 'Built-in' : `${s.created_by || 'Unknown'} · ${day(s.created_at)}`}
                  {s.status === 'pending_review' && s.expires_at ? ` · expires ${day(s.expires_at)}` : ''}
                  {s.problems ? ` · ${s.problems} issue(s)` : ''}
                </span>
              </button>
            ))}
          </div>
        ))}
      </aside>

      <section style={{ minWidth: 0 }}>
        {who && !who.persistent && (
          <div style={banner('warn')}>
            Sims are saved in temporary storage on this server and can disappear. On Vercel, add an Upstash Redis store
            (Storage → Marketplace) so sims and reviews are kept.
          </div>
        )}
        {who && !who.generator_ready && <div style={banner('warn')}>Set <code>ANTHROPIC_API_KEY</code> to generate sims.</div>}
        {error && <div style={banner('bad')}>{error}</div>}
        {notice && <div style={banner('ok')}>{notice}</div>}

        {view.kind === 'new' && who && (
          <NewSimForm who={who} onCreated={async id => { await load(); go({ kind: 'sim', id }); setNotice('Sim shell ready. Read it through, fix anything, then submit for review.'); }} onError={setError} />
        )}
        {view.kind === 'sim' && who && (
          <SimView key={view.id} id={view.id} who={who} onChanged={load} onOpen={id => go({ kind: 'sim', id })}
            onDeleted={async () => { await load(); go({ kind: 'new' }); }} setError={setError} setNotice={setNotice} />
        )}
        {view.kind === 'report' && <Report onError={setError} />}
        {view.kind === 'review' && <ReviewQueue rows={list} who={who} onOpen={id => go({ kind: 'sim', id })} />}
        {view.kind === 'notifications' && <Notifications who={who} onError={setError} />}
      </section>
    </div>
  );
}

// ---- 1 · Describe your simulation -------------------------------------------------
function NewSimForm({ who, onCreated, onError }: { who: Who; onCreated: (id: string) => void; onError: (m: string | null) => void }) {
  const [f, setF] = useState({
    title: '', audience: '', context: '', objective: '', tone: TONES[0], setting: '',
    decisions: 3, options_per_decision: 3, total_tracks: 6, slides_per_track: 2,
    pros_cons: true, follow_up_tracks: true, summary: true, video_slides: 'tracks',
    featured: { role: '', name: '', ethnicity: '' },
    characters: [{ name: '', role: '' }],
    language: 'en', language_name: '', accent: '', notes: '',
    created_by: recall('iclass_builder_name'), creator_email: recall('iclass_builder_email'),
  });
  const [busy, setBusy] = useState(false);
  const [extraLangs, setExtraLangs] = useState<string[]>([]);
  const [makeImages, setMakeImages] = useState(true);
  const [progress, setProgress] = useState('');
  const set = (patch: Partial<typeof f>) => setF(x => ({ ...x, ...patch }));

  const maxTracks = f.decisions * f.options_per_decision;
  const tracksOk = f.total_tracks >= f.decisions && f.total_tracks <= maxTracks;
  const split = useMemo(() => {
    if (!tracksOk) return [];
    const base = Math.floor(f.total_tracks / f.decisions), extra = f.total_tracks % f.decisions;
    return Array.from({ length: f.decisions }, (_, i) => base + (i < extra ? 1 : 0));
  }, [f.total_tracks, f.decisions, tracksOk]);
  const slides = 3 + f.decisions + f.total_tracks * (f.follow_up_tracks ? f.slides_per_track : 1);

  const submit = async () => {
    onError(null);
    if (!f.title.trim() || !f.context.trim()) { onError('Add a title and the scenario background.'); return; }
    if (!tracksOk) { onError(`Tracks must be between ${f.decisions} and ${maxTracks}.`); return; }
    remember('iclass_builder_name', f.created_by); remember('iclass_builder_email', f.creator_email);
    setBusy(true);
    try {
      const sim = await api<{ id: string }>('/api/admin/sims', { method: 'POST', body: JSON.stringify({
        ...f, slides_per_track: f.follow_up_tracks ? f.slides_per_track : 1,
        characters: f.characters.filter(c => c.name.trim()),
      }) });
      if (makeImages && who.images_ready) {
        const d = await api<{ media: Media | null }>(`/api/admin/sims/${encodeURIComponent(sim.id)}`);
        const missing = d.media?.images.missing ?? [];
        let done = 0;
        await Promise.all([0, 1, 2].map(async w => {
          for (let i = w; i < missing.length; i += 3) {
            setProgress(`Sim written. Creating images ${done}/${missing.length}…`);
            try { await api(`/api/admin/sims/${encodeURIComponent(sim.id)}/images/${missing[i]}`, { method: 'POST' }); } catch { /* listed in the preview */ }
            done += 1;
          }
        }));
      }
      const extra = extraLangs.filter(c => c !== f.language);
      if (extra.length) {
        const { errors } = await translateMany(sim.id, extra, (done, cur) =>
          setProgress(`Sim written. Translating ${done}/${extra.length}${cur ? ` (${cur})` : ''}…`));
        if (errors.length) onError(`Some languages didn't finish; add them again from the preview: ${errors.join('; ')}`);
      }
      onCreated(sim.id);
    } catch (e: any) { onError(e.message); }
    finally { setBusy(false); setProgress(''); }
  };

  const num = (key: 'decisions' | 'options_per_decision' | 'total_tracks' | 'slides_per_track', min: number, max: number) => (
    <input style={inp} type="number" min={min} max={max} value={f[key]}
      onChange={e => set({ [key]: Math.max(min, Math.min(max, +e.target.value || min)) } as any)} />
  );

  return (
    <div style={card}>
      <h2 style={h2}>1 · Describe your simulation</h2>
      <div className="sb-grid2">
        <div><label style={lbl} htmlFor="f-title">Simulation title</label>
          <input id="f-title" style={inp} value={f.title} onChange={e => set({ title: e.target.value })} placeholder="Navigating a Parent Complaint" /></div>
        <div><label style={lbl} htmlFor="f-aud">Target audience</label>
          <input id="f-aud" style={inp} value={f.audience} onChange={e => set({ audience: e.target.value })} placeholder="Building principals" /></div>
      </div>

      <div className="sb-grid4" style={{ marginTop: 14 }}>
        <div><label style={lbl}>Decision points</label>{num('decisions', 1, 6)}</div>
        <div><label style={lbl}>Options per decision</label>{num('options_per_decision', 2, 4)}</div>
        <div><label style={lbl}>Total tracks ({f.decisions}–{maxTracks})</label>{num('total_tracks', 1, 24)}</div>
        <div><label style={lbl} htmlFor="f-tone">Tone</label>
          <select id="f-tone" style={inp} value={f.tone} onChange={e => set({ tone: e.target.value })}>
            {TONES.map(t => <option key={t}>{t}</option>)}
          </select></div>
      </div>
      <p style={{ fontSize: 12, color: tracksOk ? 'var(--text-3)' : '#b91c1c', margin: '6px 2px 0' }}>
        {tracksOk
          ? `Tracks per decision: ${split.join(' · ')}. Options share a track when there are fewer tracks than options. About ${slides} slides in total.`
          : `With ${f.decisions} decisions and ${f.options_per_decision} options, tracks must be between ${f.decisions} and ${maxTracks}.`}
      </p>

      <div style={{ marginTop: 14 }}>
        <label style={lbl} htmlFor="f-ctx">Context / scenario background</label>
        <textarea id="f-ctx" style={{ ...inp, minHeight: 110 }} value={f.context} onChange={e => set({ context: e.target.value })}
          placeholder="A parent contacts the principal after their 7th-grade child reports feeling singled out during a class discussion…" />
      </div>
      <div className="sb-grid2" style={{ marginTop: 14 }}>
        <div><label style={lbl} htmlFor="f-obj">Learning objective (optional)</label>
          <input id="f-obj" style={inp} value={f.objective} onChange={e => set({ objective: e.target.value })} /></div>
        <div><label style={lbl} htmlFor="f-set">Setting / region (optional)</label>
          <input id="f-set" style={inp} value={f.setting} onChange={e => set({ setting: e.target.value })} placeholder="Suburban public middle school, US" /></div>
      </div>

      <fieldset className="sb-fieldset">
        <legend>Track content</legend>
        <label className="sb-check"><input type="checkbox" checked={f.pros_cons} onChange={e => set({ pros_cons: e.target.checked })} /> Pros / cons for each option</label>
        <label className="sb-check"><input type="checkbox" checked={f.follow_up_tracks} onChange={e => set({ follow_up_tracks: e.target.checked })} /> Follow-up slides in each track</label>
        {f.follow_up_tracks && <span className="sb-check">Slides per track <span style={{ width: 70, display: 'inline-block' }}>{num('slides_per_track', 1, 3)}</span></span>}
        <label className="sb-check"><input type="checkbox" checked={f.summary} onChange={e => set({ summary: e.target.checked })} /> Summary with debrief and takeaways</label>
        <span className="sb-check" style={{ width: '100%' }}>
          Character video
          <select style={{ ...inp, width: 'auto' }} value={f.video_slides} onChange={e => set({ video_slides: e.target.value })} aria-label="Character video">
            <option value="tracks">In the intro and after every choice (like our current sims)</option>
            <option value="intro">Intro only</option>
            <option value="none">No video</option>
          </select>
        </span>
        <label className="sb-check" style={{ width: '100%' }}>
          <input type="checkbox" checked={makeImages} disabled={!who.images_ready} onChange={e => setMakeImages(e.target.checked)} />
          Create the scene images after writing {who.images_ready ? '' : '(needs OPENAI_API_KEY)'}
        </label>
      </fieldset>

      <fieldset className="sb-fieldset">
        <legend>Characters, language and accent</legend>
        <div className="sb-grid3">
          <div><label style={lbl} htmlFor="f-frole">Featured character's role</label>
            <input id="f-frole" style={inp} value={f.featured.role} onChange={e => set({ featured: { ...f.featured, role: e.target.value } })} placeholder="Parent" /></div>
          <div><label style={lbl} htmlFor="f-fname">Featured character's name</label>
            <input id="f-fname" style={inp} value={f.featured.name} onChange={e => set({ featured: { ...f.featured, name: e.target.value } })} placeholder="Auto: matches language & look" /></div>
          <div><label style={lbl} htmlFor="f-look">Default look (ethnicity)</label>
            <select id="f-look" style={inp} value={f.featured.ethnicity} onChange={e => set({ featured: { ...f.featured, ethnicity: e.target.value } })}>
              <option value="">Suggested by language</option>
              {who.personas.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select></div>
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-3)', margin: '6px 2px 10px' }}>
          Leave the name blank to let it change with the learner's language and look (e.g. Carlos Hernández in Mexican Spanish). Learners can still pick a different look.
        </p>
        <label style={lbl}>Other characters</label>
        {f.characters.map((c, i) => (
          <div key={i} className="sb-grid-char">
            <input style={inp} value={c.name} placeholder="Name (e.g. Ms. Okafor)" aria-label={`Character ${i + 1} name`}
              onChange={e => set({ characters: f.characters.map((x, j) => j === i ? { ...x, name: e.target.value } : x) })} />
            <input style={inp} value={c.role} placeholder="Role (e.g. 7th-grade teacher)" aria-label={`Character ${i + 1} role`}
              onChange={e => set({ characters: f.characters.map((x, j) => j === i ? { ...x, role: e.target.value } : x) })} />
            <button style={ghost} aria-label="Remove character" onClick={() => set({ characters: f.characters.filter((_, j) => j !== i) })}>✕</button>
          </div>
        ))}
        <button style={{ ...ghost, marginTop: 6 }} onClick={() => set({ characters: [...f.characters, { name: '', role: '' }] })}>+ Add character</button>
        <div className="sb-grid3" style={{ marginTop: 14 }}>
          <div><label style={lbl} htmlFor="f-lang">Main language</label>
            <input id="f-lang" style={inp} list="app-langs" value={f.language} onChange={e => set({ language: e.target.value.trim() })} />
            <datalist id="app-langs">{who.language_options.map(l => <option key={l.code} value={l.code}>{l.native}</option>)}</datalist></div>
          <div><label style={lbl} htmlFor="f-lname">Language name</label>
            <input id="f-lname" style={inp} value={f.language_name} onChange={e => set({ language_name: e.target.value })} placeholder={f.language === 'en' ? 'English' : 'e.g. Español (México)'} /></div>
          <div><label style={lbl} htmlFor="f-acc">Accent / variety</label>
            <input id="f-acc" style={inp} value={f.accent} onChange={e => set({ accent: e.target.value })} placeholder="e.g. Mexican Spanish" /></div>
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-3)', margin: '6px 2px 0' }}>
          English is always written first (it's the fallback). The accent is saved for narration and video voices.
        </p>
        <div style={{ width: '100%', marginTop: 12 }}>
          <label style={lbl}>Also make it available in (learners pick these in the language menu)</label>
          <LangPicker options={who.language_options} chosen={extraLangs} setChosen={setExtraLangs} exclude={[f.language]} />
          <p style={{ fontSize: 12, color: 'var(--text-3)', margin: '6px 2px 0' }}>
            Each language is localized for its region (names, places, school terms), using the same regions as the built-in sim. You can add more later.
          </p>
        </div>
      </fieldset>

      <div style={{ marginTop: 14 }}>
        <label style={lbl} htmlFor="f-notes">Anything else the AI should know? (optional)</label>
        <textarea id="f-notes" style={{ ...inp, minHeight: 60 }} value={f.notes} onChange={e => set({ notes: e.target.value })} placeholder="Policies to respect, things to avoid, how hard the decisions should be…" />
      </div>
      <div className="sb-grid2" style={{ marginTop: 14 }}>
        <div><label style={lbl} htmlFor="f-by">Your name</label>
          <input id="f-by" style={inp} value={f.created_by} onChange={e => set({ created_by: e.target.value })} placeholder="Dana Flores, PD Director" /></div>
        <div><label style={lbl} htmlFor="f-em">Your email (for review updates)</label>
          <input id="f-em" style={inp} type="email" value={f.creator_email} onChange={e => set({ creator_email: e.target.value })} /></div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, marginTop: 20 }}>
        {busy && <span style={{ fontSize: 13, color: 'var(--text-3)' }}>{progress || 'Writing the sim… this takes 1–3 minutes.'}</span>}
        <button style={{ ...primary, opacity: busy || !who.generator_ready ? 0.6 : 1 }} disabled={busy || !who.generator_ready} onClick={submit}>
          {busy ? 'Generating…' : '✦ Generate Sim Shell'}
        </button>
      </div>
    </div>
  );
}

// ---- 2 · Preview, edit, review -------------------------------------------------------
function SimView({ id, who, onChanged, onOpen, onDeleted, setError, setNotice }: {
  id: string; who: Who; onChanged: () => void; onOpen: (id: string) => void; onDeleted: () => void;
  setError: (m: string | null) => void; setNotice: (m: string | null) => void;
}) {
  const [sim, setSim] = useState<SimDetail | null>(null);
  const [lang, setLang] = useState('en');
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');
  const [note, setNote] = useState('');
  const [reviewer, setReviewer] = useState(recall('iclass_reviewer_name'));
  const [extendOpen, setExtendOpen] = useState(false);
  const [addLangs, setAddLangs] = useState<string[]>([]);
  const [langProgress, setLangProgress] = useState('');
  const [problems, setProblems] = useState<string[]>([]);

  const fetchSim = useCallback(async () => {
    try {
      const s = await api<SimDetail & { problems: any }>(`/api/admin/sims/${encodeURIComponent(id)}`);
      setSim(s); setProblems(Array.isArray(s.problems) ? s.problems : []);
    } catch (e: any) { setError(e.message); }
  }, [id, setError]);
  useEffect(() => { fetchSim(); }, [fetchSim]);

  if (!sim) return <div style={card}>Loading…</div>;
  const block = sim.scenario[lang] as Block | undefined;
  const en = sim.scenario.en as Block;
  const start = sim.scenario.start_node_id;
  const order = slideOrder(en.nodes, start);
  const editable = !sim.builtin && (sim.status === 'draft' || sim.status === 'expired' || sim.status === 'pending_review');
  const isReviewer = who.role === 'reviewer';
  const pending = sim.pending_langs ?? [];
  // Published sims stay live; a language added to them waits for review and can be edited meanwhile.
  const canEdit = editable || (sim.status === 'published' && pending.includes(lang));
  const canAddLangs = !sim.builtin && sim.status !== 'superseded';
  const dirty = Object.keys(edits).length > 0;

  const run = async (label: string, fn: () => Promise<any>, ok?: string) => {
    setError(null); setNotice(null); setBusy(label);
    try {
      const r = await fn();
      if (r && r.id) { setSim(r); setProblems(Array.isArray(r.problems) ? r.problems : []); }
      if (ok) setNotice(ok);
      onChanged();
    } catch (e: any) { setError(e.message); }
    finally { setBusy(''); }
  };
  const save = () => run('save', async () => {
    const r = await api(`/api/admin/sims/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ lang, edits }) });
    setEdits({}); return r;
  }, 'Saved.');
  const post = (path: string, body: any = {}) => api(`/api/admin/sims/${encodeURIComponent(id)}${path}`, { method: 'POST', body: JSON.stringify(body) });

  const decisions = order.filter(k => en.nodes[k].type === 'decision');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
          <h2 style={{ ...h2, margin: 0 }}>2 · AI-generated preview</h2>
          <Chip status={sim.status} extra={sim.status === 'pending_review' ? who.reviewer_email : undefined} />
          {sim.source === 'extend' && sim.parent_id && (
            <button style={{ ...ghost, padding: '3px 10px', fontSize: 12 }} onClick={() => onOpen(sim.parent_id!)}>Extends: original ↗</button>
          )}
        </div>
        <p style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-1)', margin: '8px 0 4px' }}>{sim.title}</p>
        <p style={{ fontSize: 13, color: 'var(--text-3)', margin: 0 }}>
          {sim.builtin ? 'Built-in sim' : `Created by ${sim.created_by || 'unknown'} on ${day(sim.created_at)}`}
          {sim.submitted_at && ` · submitted ${day(sim.submitted_at)}`}
          {sim.status === 'pending_review' && sim.expires_at && ` · expires ${day(sim.expires_at)} if not reviewed`}
          {sim.reviewed_by && ` · reviewed by ${sim.reviewed_by} ${day(sim.reviewed_at)}`}
          {` · ${decisions.length} decisions · ${order.length} slides`}
        </p>

        {sim.review_note && sim.status === 'draft' && <div style={{ ...banner('warn'), marginTop: 12 }}><b>Reviewer:</b> {sim.review_note}</div>}

        <div className="sb-scenario" style={{ marginTop: 14 }}>
          <b>Scenario:</b> {sim.context_summary || en.ui.brief_scenario_body}
        </div>

        {!sim.builtin && JSON.stringify(en.nodes).includes('Jordan Chen') && (
          <p style={{ fontSize: 12, color: 'var(--text-3)', margin: '8px 0 0' }}>
            “Jordan Chen” stands in for the featured character ({sim.inputs?.featured?.role || 'the main character'}). Learners see a name that fits the language and look they choose. Keep it as is when editing.
          </p>
        )}

        {/* Branch map */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14 }}>
          {decisions.map(did => {
            const d = en.nodes[did];
            const firsts: string[] = Array.from(new Set(d.choices.map((c: any) => c.target_node_id)));
            return (
              <div key={did} className="sb-dp">
                <p style={{ margin: '0 0 6px', fontSize: 14 }}><b style={{ color: '#1e2a5a' }}>{d.step_badge}:</b> {d.prompt}</p>
                <div className="sb-branch">
                  {d.choices.map((c: any) => (
                    <div key={c.id} className="sb-opt">
                      <span className="sb-letter">{c.id}</span>
                      <span style={{ flex: 1 }}>{c.label}</span>
                      <span className="sb-arrow">→ Track {firsts.indexOf(c.target_node_id) + 1}</span>
                    </div>
                  ))}
                </div>
                <div className="sb-tracks">
                  {firsts.map((f, i) => (
                    <div key={f} className="sb-track">
                      <b>Track {i + 1}</b>
                      {trackSlides(en.nodes, f).map(s => <span key={s}>· {en.nodes[s].title || TYPE_LABEL[en.nodes[s].type]}</span>)}
                      <span style={{ color: 'var(--text-3)' }}>→ {en.nodes[trackSlides(en.nodes, f).slice(-1)[0]]?.next_node_id === 'summary' ? 'Summary' : 'next decision'}</span>
                    </div>
                  ))}
                </div>
                {d.choices.some((c: any) => c.pros?.length) && <p style={{ margin: '6px 0 0', fontSize: 12, color: '#8b1e3f', fontStyle: 'italic' }}>Pros/cons and follow-up tracks generated for each option.</p>}
              </div>
            );
          })}
        </div>

        <p style={{ fontSize: 12, color: sim.ada.passed ? 'var(--text-3)' : '#b91c1c', margin: '14px 0 0' }}>
          ⚠ Narration and transcripts generated for all slides. ADA check: <b>{sim.ada.passed ? 'passed' : `${sim.ada.issues.length} issue(s)`}</b>.
          {sim.status !== 'published' && ' Awaiting SchoolSims editorial review before library publication.'}
        </p>
        {!sim.ada.passed && <ul style={{ fontSize: 12, color: '#b91c1c', margin: '4px 0 0' }}>{sim.ada.issues.slice(0, 8).map(i => <li key={i}>{i}</li>)}</ul>}

        {/* Actions */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 16, alignItems: 'center' }}>
          {!sim.builtin && (sim.status === 'draft' || sim.status === 'expired') && (
            <button style={navy} disabled={!!busy} onClick={() => run('submit', () => post('/submit'), `Submitted. ${who.reviewer_email} has been notified; it expires in ${who.review_days} days if not reviewed.`)}>
              {busy === 'submit' ? 'Submitting…' : 'Submit for Review'}
            </button>
          )}
          <a style={{ ...ghost, textDecoration: 'none' }} href={`/?sim=${encodeURIComponent(sim.id)}`} target="_blank" rel="noreferrer">Play preview ↗</a>
          {(sim.status === 'published' || sim.builtin) && <button style={ghost} onClick={() => setExtendOpen(true)}>Extend Sim</button>}
          {!sim.builtin && sim.status !== 'published' && (
            <button style={{ ...ghost, color: '#b91c1c', marginInlineStart: 'auto' }} disabled={!!busy}
              onClick={() => { if (confirm('Delete this sim?')) run('delete', async () => { await api(`/api/admin/sims/${encodeURIComponent(id)}`, { method: 'DELETE' }); onDeleted(); }); }}>
              Delete
            </button>
          )}
          {!sim.builtin && (sim.status === 'published' || sim.status === 'superseded') && isReviewer && (
            <button style={{ ...ghost, marginInlineStart: 'auto' }} disabled={!!busy} onClick={() => run('unpub', () => post('/unpublish'), 'Unpublished; it is a draft again.')}>Unpublish</button>
          )}
        </div>

        {/* Reviewer panel */}
        {sim.status === 'pending_review' && (
          <div className="sb-review">
            <p style={{ margin: '0 0 8px', fontWeight: 700, color: '#1e2a5a' }}>SchoolSims editorial review</p>
            {!isReviewer ? (
              <p style={{ margin: 0, fontSize: 13, color: 'var(--text-3)' }}>Waiting for {who.reviewer_email}. Only reviewers can publish or send back.</p>
            ) : (
              <>
                <div className="sb-grid2">
                  <input style={inp} value={reviewer} onChange={e => setReviewer(e.target.value)} placeholder="Reviewer name" aria-label="Reviewer name" />
                  <input style={inp} value={note} onChange={e => setNote(e.target.value)} placeholder="What needs to change? (to send back)" aria-label="Note to the creator" />
                </div>
                <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
                  <button style={{ ...primary, background: '#166534' }} disabled={!!busy || problems.length > 0 || dirty}
                    title={problems.length ? 'Resolve the open problems first' : dirty ? 'Save your edits first' : ''}
                    onClick={() => { remember('iclass_reviewer_name', reviewer); run('approve', () => post('/approve', { reviewer }), 'Published to the library.'); }}>
                    Approve &amp; publish
                  </button>
                  <button style={ghost} disabled={!!busy || !note.trim()}
                    onClick={() => { remember('iclass_reviewer_name', reviewer); run('back', () => post('/send-back', { reviewer, note }), 'Sent back to the creator.'); }}>
                    Send back with note
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {(problems.length > 0 || sim.review_notes.length > 0) && (
        <div style={card}>
          {problems.length > 0 && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                <p style={{ ...lbl, color: '#991b1b', margin: 0 }}>Open problems ({problems.length})</p>
                {editable && (
                  <>
                    <button style={{ ...ghost, padding: '3px 10px', fontSize: 12 }} disabled={!!busy}
                      onClick={() => { if (confirm(`Accept all ${problems.length} problems as they are?`)) run('clear', () => post('/problems/clear', { all: true }), 'All problems accepted.'); }}>
                      Accept all
                    </button>
                    {Array.from(new Set(problems.map(p => p.match(/^\[([^\]]+)\]/)?.[1]).filter(Boolean))).map(l => (
                      <button key={l} style={{ ...ghost, padding: '3px 10px', fontSize: 12 }} disabled={!!busy}
                        onClick={() => run('clear', () => post('/problems/clear', { lang: l }))}>
                        Accept all {l}
                      </button>
                    ))}
                  </>
                )}
              </div>
              <ul className="sb-list">
                {problems.map(p => (
                  <li key={p}>{p}
                    {editable && <button style={{ ...ghost, padding: '1px 8px', fontSize: 11, marginInlineStart: 8 }}
                      onClick={() => run('clear', () => post('/problems/clear', { problem: p }))}>Accept as is</button>}
                  </li>
                ))}
              </ul>
            </>
          )}
          {sim.review_notes.length > 0 && (
            <>
              <p style={lbl}>Notes from the AI for the reviewer</p>
              <ul className="sb-list">{sim.review_notes.map(n => <li key={n}>{n}</li>)}</ul>
            </>
          )}
        </div>
      )}

      {/* Languages */}
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ ...lbl, margin: 0 }}>Languages ({sim.languages.length})</span>
          {sim.languages.map(l => {
            const pend = pending.includes(l);
            return (
              <button key={l} onClick={() => { setLang(l); setEdits({}); }} title={pend ? 'Waiting for review: learners don\'t see it yet' : ''}
                style={{ ...ghost, padding: '4px 12px', background: l === lang ? '#1e2a5a' : pend ? '#fffbeb' : 'var(--surface)', color: l === lang ? '#fff' : 'var(--text-2)', borderColor: pend ? '#fde68a' : undefined }}>
                {l}{pend ? ' · pending' : ''}
              </button>
            );
          })}
          {canEdit && lang !== 'en' && (
            <button style={{ ...ghost, padding: '4px 10px', fontSize: 12, color: '#b91c1c' }}
              onClick={() => { if (confirm(`Remove ${lang}?`)) run('rmlang', async () => { const r = await api(`/api/admin/sims/${encodeURIComponent(id)}/languages/${lang}`, { method: 'DELETE' }); setLang('en'); return r; }); }}>
              Remove {lang}
            </button>
          )}
          {pending.includes(lang) && isReviewer && (
            <button style={{ ...primary, background: '#166534', padding: '5px 12px', fontSize: 13 }} disabled={!!busy || dirty}
              onClick={() => run('applang', () => post(`/languages/${lang}/approve`), `${lang} is now available to learners.`)}>
              Approve {lang} for learners
            </button>
          )}
        </div>
        {pending.length > 0 && (
          <p style={{ fontSize: 12, color: '#92400e', margin: '8px 0 0' }}>
            Pending languages were added after publishing. Learners see them once a reviewer approves each one.
          </p>
        )}
        {(sim.translation_notes[lang] ?? []).length > 0 && (
          <ul className="sb-list" style={{ marginTop: 8 }}>{sim.translation_notes[lang].map(n => <li key={n}>{n}</li>)}</ul>
        )}
        {canAddLangs && (
          <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <LangPicker options={who.language_options} chosen={addLangs} setChosen={setAddLangs} exclude={sim.languages} />
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
              <button style={navy} disabled={!!busy || addLangs.length === 0}
                onClick={() => run('tr', async () => {
                  const { last, errors } = await translateMany(id, addLangs, (done, cur) =>
                    setLangProgress(`Translating ${done}/${addLangs.length}${cur ? ` (${cur})` : ''}…`));
                  setLangProgress(''); setAddLangs([]);
                  if (errors.length) setError(`Some languages didn't finish: ${errors.join('; ')}`);
                  return last;
                }, sim.status === 'published' ? 'Languages added. A reviewer approves each one before learners see it.' : 'Languages added. Read them through before submitting.')}>
                {busy === 'tr' ? (langProgress || 'Translating…') : `Translate into ${addLangs.length || ''} language${addLangs.length === 1 ? '' : 's'}`}
              </button>
              <span style={{ fontSize: 12, color: 'var(--text-3)' }}>Each language takes about a minute; three run at a time.</span>
            </div>
          </div>
        )}
      </div>

      {sim.media && !sim.builtin && (
        <MediaPanel sim={sim} onUpdate={d => { setSim(d); }} setError={setError} setNotice={setNotice} />
      )}

      {/* Slides */}
      {block && (
        <div style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <p style={{ ...lbl, margin: 0, flex: 1 }}>Slides ({lang}) {canEdit ? '— click any text to edit' : ''}</p>
            {canEdit && <button style={{ ...navy, opacity: dirty ? 1 : 0.5 }} disabled={!dirty || !!busy} onClick={save}>{busy === 'save' ? 'Saving…' : `Save ${Object.keys(edits).length || ''} edit(s)`}</button>}
          </div>
          {order.map(nid => {
            const node = block.nodes[nid];
            if (!node) return null;
            const img = sim.images[nid];
            return (
              <div key={nid} className="sb-slide">
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                  <span className="sb-type">{TYPE_LABEL[node.type] ?? node.type}</span>
                  <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{nid}</span>
                </div>
                {node.image_prompt && (
                  <div className="sb-img">
                    {img ? <img src={img} alt={node.image_alt ?? ''} /> : <div className="sb-img-empty">No image yet</div>}
                    <div style={{ fontSize: 12, color: 'var(--text-3)', flex: 1 }}>
                      <div><b>Image prompt:</b> {node.image_prompt}</div>
                      {canEdit && who.images_ready && (
                        <button style={{ ...ghost, marginTop: 6, fontSize: 12 }} disabled={!!busy}
                          onClick={() => run(`img-${nid}`, () => post(`/images/${nid}`), 'Image created.')}>
                          {busy === `img-${nid}` ? 'Creating…' : img ? 'Recreate image' : 'Create image'}
                        </button>
                      )}
                      {!who.images_ready && <div style={{ marginTop: 4 }}>Set <code>OPENAI_API_KEY</code> to create images; until then the slide shows text only.</div>}
                    </div>
                  </div>
                )}
                {nodeFields(nid, node).filter(([p]) => !p.endsWith('.image_prompt')).map(([path, text]) => (
                  <div key={path} className="sb-field">
                    <span className="sb-fl">{fieldLabel(path)}</span>
                    {canEdit ? (
                      <textarea dir="auto" className="sb-ta" rows={Math.min(5, Math.max(1, Math.ceil((edits[path] ?? text).length / 90)))}
                        value={edits[path] ?? text}
                        onChange={e => setEdits(x => {
                          const n = { ...x };
                          if (e.target.value === text) delete n[path]; else n[path] = e.target.value;
                          return n;
                        })}
                        style={{ borderColor: path in edits ? '#8b1e3f' : undefined }} />
                    ) : <span dir="auto" style={{ fontSize: 14, color: 'var(--text-1)' }}>{text}</span>}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}

      {extendOpen && (
        <div className="modal-backdrop-sb" onClick={e => { if (e.target === e.currentTarget) setExtendOpen(false); }}>
          <ExtendSimPanel simId={sim.id} simTitle={sim.title} onClose={() => { setExtendOpen(false); onChanged(); }} />
        </div>
      )}
    </div>
  );
}

// ---- Images and videos -------------------------------------------------------------
function MediaPanel({ sim, onUpdate, setError, setNotice }: {
  sim: SimDetail; onUpdate: (d: SimDetail) => void;
  setError: (m: string | null) => void; setNotice: (m: string | null) => void;
}) {
  const media = sim.media!;
  const [busy, setBusy] = useState('');
  const [pick, setPick] = useState<Record<string, string>>(() => ({ ...media.suggested }));
  const [chosen, setChosen] = useState<string[]>(() => sim.languages);
  const enc = encodeURIComponent(sim.id);
  const reload = async () => onUpdate(await api<SimDetail>(`/api/admin/sims/${enc}`));
  const rendering = Object.values(media.grid).some(row => Object.values(row).some(c => c.rendering > 0));

  // While HeyGen renders, check every 20 s and copy finished clips.
  useEffect(() => {
    if (!rendering) return;
    const t = setInterval(async () => {
      try { await api(`/api/admin/sims/${enc}/videos/refresh`, { method: 'POST' }); await reload(); } catch { /* next tick */ }
    }, 20000);
    return () => clearInterval(t);
  }, [rendering, enc]);

  const images = async () => {
    setBusy('img'); setError(null);
    let done = 0; const missing = [...media.images.missing]; const errs: string[] = [];
    await Promise.all([0, 1, 2].map(async w => {
      for (let i = w; i < missing.length; i += 3) {
        try { await api(`/api/admin/sims/${enc}/images/${missing[i]}`, { method: 'POST' }); }
        catch (e: any) { errs.push(`${missing[i]}: ${e.message}`); }
        done += 1; setBusy(`img ${done}/${missing.length}`);
      }
    }));
    await reload(); setBusy('');
    if (errs.length) setError(errs.slice(0, 3).join('; ')); else setNotice('Images created.');
  };

  const videos = async () => {
    setBusy('vid'); setError(null);
    const errs: string[] = [];
    for (const [i, lang] of chosen.entries()) {
      setBusy(`vid ${i + 1}/${chosen.length}`);
      try {
        const r = await api<{ errors: string[] }>(`/api/admin/sims/${enc}/videos`, { method: 'POST', body: JSON.stringify({ persona: pick[lang], lang }) });
        errs.push(...r.errors.map(e => `${lang}: ${e}`));
        if (r.errors.some(e => /credit/i.test(e))) break;
      } catch (e: any) { errs.push(`${lang}: ${e.message}`); if (e.status === 503) break; }
    }
    await reload(); setBusy('');
    if (errs.length) setError(errs.slice(0, 4).join('; '));
    else setNotice('Videos started. HeyGen takes a few minutes per clip; this page checks every 20 seconds (or come back later).');
  };

  const clips = chosen.length * media.video_nodes.length;
  const lookLabel = (id: string) => media.looks.find(l => l.id === id)?.label ?? id;
  return (
    <div style={card}>
      <p style={{ ...lbl, marginBottom: 10 }}>Images and character videos</p>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
        <span style={{ fontSize: 14 }}>Scene images: <b>{media.images.done}/{media.images.total}</b></span>
        {media.images.missing.length > 0 && (
          <button style={ghost} disabled={!!busy || !media.images_ready} onClick={images}>
            {busy.startsWith('img') ? `Creating ${busy.slice(4)}…` : `Create ${media.images.missing.length} missing image(s)`}
          </button>
        )}
        {!media.images_ready && <span style={{ fontSize: 12, color: 'var(--text-3)' }}>Needs OPENAI_API_KEY in Vercel.</span>}
      </div>

      {media.video_nodes.length === 0 ? (
        <p style={{ fontSize: 13, color: 'var(--text-3)' }}>This sim has no video slides.</p>
      ) : (
        <>
          {(!media.heygen_ready || !media.blob_ready) && (
            <div style={banner('warn')}>
              To make videos here, add in Vercel: {!media.heygen_ready && <><code>HEYGEN_API_KEY</code> (Settings → Environment Variables) </>}
              {!media.blob_ready && <>Blob storage (Storage → Create → Blob → connect to this project) </>}then redeploy.
              Until then, video slides show the character's portrait with what they say.
            </div>
          )}
          <p style={{ fontSize: 13, color: 'var(--text-2)', margin: '0 0 10px' }}>
            {media.video_nodes.length} video slides, about {media.seconds_per_set}s of speech per language.
            Each language gets one look (the learner's chosen look falls back to the portrait if it has no clip).
          </p>
          <table className="sb-table">
            <thead><tr><th style={{ width: 30 }}></th><th>Language</th><th>Character look</th><th>Status</th></tr></thead>
            <tbody>
              {sim.languages.map(lang => {
                const all = media.grid[lang] ?? {};
                const look = pick[lang] ?? media.suggested[lang];
                const c = all[look];
                const others = Object.entries(all).filter(([id, x]) => id !== look && x.ready > 0).map(([id, x]) => `${lookLabel(id)} ${x.ready}/${x.total}`);
                return (
                  <tr key={lang}>
                    <td><input type="checkbox" aria-label={`Videos for ${lang}`} checked={chosen.includes(lang)}
                      onChange={e => setChosen(e.target.checked ? [...chosen, lang] : chosen.filter(x => x !== lang))} /></td>
                    <td>{lang}</td>
                    <td>
                      <select style={{ ...inp, padding: '4px 8px' }} value={look} onChange={e => setPick({ ...pick, [lang]: e.target.value })} aria-label={`Look for ${lang}`}>
                        {media.looks.map(l => <option key={l.id} value={l.id}>{l.label}</option>)}
                      </select>
                    </td>
                    <td style={{ fontSize: 13 }}>
                      {c ? <>
                        <b style={{ color: c.ready === c.total ? '#166534' : 'var(--text-1)' }}>{c.ready}/{c.total} ready</b>
                        {c.rendering > 0 && <span> · {c.rendering} rendering…</span>}
                        {c.stale > 0 && <span style={{ color: '#92400e' }}> · {c.stale} out of date (script edited)</span>}
                        {c.failed > 0 && <span style={{ color: '#b91c1c' }} title={c.errors.join('\n')}> · {c.failed} failed</span>}
                      </> : '—'}
                      {others.length > 0 && <div style={{ color: 'var(--text-3)', fontSize: 12 }}>Also: {others.join(', ')}</div>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
            <button style={navy} disabled={!!busy || clips === 0 || !media.heygen_ready || !media.blob_ready} onClick={videos}>
              {busy.startsWith('vid') ? `Starting ${busy.slice(4)}…` : `Make ${clips} video clip(s)`}
            </button>
            <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
              Uses HeyGen API credit (about {Math.round(chosen.length * media.seconds_per_set / 60 * 10) / 10} min of video). Clips already made are skipped.
            </span>
            {rendering && <span style={{ fontSize: 12, color: '#3730a3' }}>Rendering… checking every 20 seconds.</span>}
          </div>
        </>
      )}
    </div>
  );
}

// ---- Review queue ---------------------------------------------------------------------
function ReviewQueue({ rows, who, onOpen }: { rows: SimRow[]; who: Who | null; onOpen: (id: string) => void }) {
  const pending = rows.filter(s => s.status === 'pending_review');
  const langs = rows.filter(s => s.status === 'published' && (s as any).pending_langs?.length);
  return (
    <div style={card}>
      <h2 style={h2}>Waiting for review</h2>
      <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '-8px 0 14px' }}>
        {who?.role === 'reviewer' ? 'Open a sim to approve and publish it, or send it back with a note.' : `Reviewed by ${who?.reviewer_email ?? 'SchoolSims'}.`}
      </p>
      <table className="sb-table">
        <thead><tr><th>Sim</th><th>Creator</th><th>Submitted</th><th>Expires</th><th>Languages</th><th></th></tr></thead>
        <tbody>
          {pending.map(s => (
            <tr key={s.id}>
              <td><b>{s.title}</b><div style={{ color: 'var(--text-3)' }}>{s.context_summary}</div></td>
              <td>{s.created_by}</td><td>{day(s.submitted_at)}</td><td>{day(s.expires_at)}</td>
              <td>{s.languages.join(', ')}</td>
              <td><button style={ghost} onClick={() => onOpen(s.id)}>Open</button></td>
            </tr>
          ))}
          {langs.map(s => (
            <tr key={s.id + '-langs'}>
              <td><b>{s.title}</b><div style={{ color: 'var(--text-3)' }}>New languages on a published sim</div></td>
              <td>{s.created_by}</td><td>—</td><td>—</td><td>{(s as any).pending_langs.join(', ')}</td>
              <td><button style={ghost} onClick={() => onOpen(s.id)}>Open</button></td>
            </tr>
          ))}
          {pending.length + langs.length === 0 && <tr><td colSpan={6} style={{ color: 'var(--text-3)' }}>Nothing is waiting for review.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

// ---- Report ---------------------------------------------------------------------
function Report({ onError }: { onError: (m: string | null) => void }) {
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const [start, setStart] = useState(iso(new Date(Date.now() - 30 * 864e5)));
  const [end, setEnd] = useState(iso(new Date()));
  const [rows, setRows] = useState<ReportRow[] | null>(null);
  const load = useCallback(async () => {
    try { setRows((await api<{ rows: ReportRow[] }>(`/api/admin/sim-report?start=${start}&end=${end}`)).rows); }
    catch (e: any) { onError(e.message); }
  }, [start, end, onError]);
  useEffect(() => { load(); }, [load]);
  const download = async () => {
    const r = await fetch(`/api/admin/sim-report?start=${start}&end=${end}&format=csv`, { headers: { 'X-Admin-Token': token() } });
    if (!r.ok) { onError(`Download failed (${r.status})`); return; }
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a'); a.href = url; a.download = `sim-report-${start}-${end}.csv`; a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div style={card}>
      <h2 style={h2}>AI-created sims report</h2>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 16 }}>
        <div><label style={lbl} htmlFor="r-s">From</label><input id="r-s" style={inp} type="date" value={start} onChange={e => setStart(e.target.value)} /></div>
        <div><label style={lbl} htmlFor="r-e">To</label><input id="r-e" style={inp} type="date" value={end} onChange={e => setEnd(e.target.value)} /></div>
        <button style={ghost} onClick={download}>Download CSV</button>
        <span style={{ fontSize: 13, color: 'var(--text-3)' }}>{rows ? `${rows.length} sim(s)` : ''}</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="sb-table">
          <thead><tr><th>Sim name</th><th>Context summary</th><th>Creator</th><th>Created</th><th>Status</th><th>Languages</th></tr></thead>
          <tbody>
            {(rows ?? []).map(r => (
              <tr key={r.id}>
                <td><a href={`#sim=${encodeURIComponent(r.id)}`}>{r.title}</a></td>
                <td>{r.context_summary}</td><td>{r.created_by}</td><td>{day(r.created_at)}</td>
                <td><Chip status={r.status} /></td><td>{r.languages.join(', ')}</td>
              </tr>
            ))}
            {rows && rows.length === 0 && <tr><td colSpan={6} style={{ color: 'var(--text-3)' }}>No sims created in this period.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---- Notifications ----------------------------------------------------------------
function Notifications({ who, onError }: { who: Who | null; onError: (m: string | null) => void }) {
  const [items, setItems] = useState<Note[] | null>(null);
  useEffect(() => { api<{ items: Note[] }>('/api/admin/notifications').then(r => setItems(r.items)).catch(e => onError(e.message)); }, [onError]);
  return (
    <div style={card}>
      <h2 style={h2}>Notifications</h2>
      {who && !who.email_ready && (
        <div style={banner('info')}>
          Email isn't set up, so notifications are kept here only. Set <code>RESEND_API_KEY</code> and <code>SIM_MAIL_FROM</code> to email {who.reviewer_email} and creators.
        </div>
      )}
      {(items ?? []).map((n, i) => (
        <div key={i} className="sb-slide">
          <div style={{ fontSize: 13, color: 'var(--text-3)' }}>{new Date(n.at).toLocaleString()} · to {n.to} · {n.sent ? 'emailed' : n.error ? `not sent: ${n.error}` : 'not emailed'}</div>
          <div style={{ fontWeight: 600, margin: '4px 0' }}>{n.subject}</div>
          <div style={{ fontSize: 13, color: 'var(--text-2)', whiteSpace: 'pre-wrap' }}>{n.text}</div>
        </div>
      ))}
      {items && items.length === 0 && <p style={{ color: 'var(--text-3)', fontSize: 14 }}>Nothing yet.</p>}
    </div>
  );
}
