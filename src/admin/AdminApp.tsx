import { useCallback, useEffect, useMemo, useState } from 'react';

// ---- types -----------------------------------------------------------------
interface Approved { code: string; label: string; native_label: string; setting: string; approved_at: string }
interface DraftSummary { code: string; language_name: string; setting: string; status: string; created_at: string; is_new_language: boolean; problems: number }
interface Locales { approved: Approved[]; drafts: DraftSummary[]; writable: boolean; generator_ready: boolean }
interface FitComment { fit: 'partly' | 'no'; character: string; text: string; timestamp: string }
interface FitStats { yes: number; partly: number; no: number; comments: FitComment[] }
interface Feedback { by_lang: Record<string, FitStats>; persistent: boolean }
interface Row { path: string; english: string; current: string; text: string; back_translation: string }
interface Draft {
  code: string; language_name: string; setting: string; notes: string; is_new_language: boolean;
  created_at: string; model: string; review_notes: string[]; problems: string[]; rows: Row[];
}

// ---- helpers ---------------------------------------------------------------
const TOKEN_KEY = 'iclass_admin_token';
const readToken = () => { try { return localStorage.getItem(TOKEN_KEY) ?? ''; } catch { return ''; } };
const writeToken = (t: string) => { try { localStorage.setItem(TOKEN_KEY, t); } catch { /* private mode */ } };

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', 'X-Admin-Token': readToken(), ...(init.headers ?? {}) },
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(body.detail ?? `HTTP ${r.status}`), { status: r.status });
  return body as T;
}

const SLIDES: Record<string, string> = {
  context: '1 · Context', text_image: '2 · Customer request', video: '3 · Video', decision: '4 · Decision',
  outcome_a: '5A · Outcome A', outcome_b: '5B · Outcome B', summary: '6 · Summary',
};
function friendly(path: string): { group: string; field: string } {
  const [root, id, ...rest] = path.split('.');
  if (root === 'nodes') return { group: SLIDES[id] ?? id, field: rest.join(' › ').replace(/_/g, ' ') };
  return { group: 'Interface', field: [id, ...rest].join(' › ').replace(/_/g, ' ') };
}
const isStory = (p: string) => p.startsWith('nodes.') || p === 'ui.brief_scenario_body' || p === 'ui.brief_objective_body';

// ---- styles ----------------------------------------------------------------
const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, padding: 20 };
const label: React.CSSProperties = { display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--text-3)', letterSpacing: '0.06em', textTransform: 'uppercase', margin: '0 0 6px' };
const input: React.CSSProperties = { width: '100%', border: '1px solid var(--border2)', borderRadius: 8, padding: '9px 11px', fontSize: 14, fontFamily: 'inherit', background: 'var(--surface)', color: 'var(--text-1)' };
const ghost: React.CSSProperties = { fontSize: 13, background: 'none', border: '1px solid var(--border2)', borderRadius: 8, padding: '7px 12px', cursor: 'pointer', color: 'var(--text-2)' };
const banner = (tone: 'warn' | 'bad' | 'ok'): React.CSSProperties => ({
  borderRadius: 10, padding: '10px 14px', fontSize: 13, lineHeight: 1.5, margin: '0 0 12px',
  background: tone === 'bad' ? '#fef2f2' : tone === 'warn' ? '#fffbeb' : '#f0fdf4',
  border: `1px solid ${tone === 'bad' ? '#fecaca' : tone === 'warn' ? '#fde68a' : '#bbf7d0'}`,
  color: tone === 'bad' ? '#991b1b' : tone === 'warn' ? '#92400e' : '#166534',
});

// ---- app -------------------------------------------------------------------
export default function AdminApp() {
  const [locales, setLocales] = useState<Locales | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [openFeedback, setOpenFeedback] = useState<string | null>(null);
  const [needToken, setNeedToken] = useState(false);
  const [tokenInput, setTokenInput] = useState(readToken());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [form, setForm] = useState({ code: '', language_name: '', setting: '', notes: '' });
  const [generating, setGenerating] = useState(false);

  const [draft, setDraft] = useState<Draft | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [storyOnly, setStoryOnly] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setLocales(await api<Locales>('/api/admin/locales'));
      setNeedToken(false);
      api<Feedback>('/api/admin/feedback').then(setFeedback).catch(() => setFeedback(null));
    } catch (e: any) {
      if (e.status === 401 || e.status === 403) setNeedToken(true);
      setError(e.message);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const openDraft = async (code: string) => {
    setError(null); setNotice(null);
    try {
      const d = await api<Draft>(`/api/admin/drafts/${encodeURIComponent(code)}`);
      setDraft(d); setEdits({}); setStoryOnly(!d.is_new_language);
    } catch (e: any) { setError(e.message); }
  };

  const generate = async (override?: typeof form) => {
    const f = override ?? form;
    setError(null); setNotice(null); setGenerating(true);
    try {
      const d = await api<{ code: string }>('/api/admin/drafts', { method: 'POST', body: JSON.stringify(f) });
      await load(); await openDraft(d.code);
      setNotice('Draft ready. Review each field, fix anything that reads wrong, then approve.');
    } catch (e: any) { setError(e.message); }
    finally { setGenerating(false); }
  };

  const save = async () => {
    if (!draft || Object.keys(edits).length === 0) return draft;
    setBusy(true);
    try {
      const d = await api<Draft>(`/api/admin/drafts/${encodeURIComponent(draft.code)}`, { method: 'PUT', body: JSON.stringify({ edits }) });
      setDraft(d); setEdits({}); return d;
    } catch (e: any) { setError(e.message); return null; }
    finally { setBusy(false); }
  };

  const approve = async () => {
    if (!draft) return;
    const d = await save();
    if (!d) return;
    if (d.problems.length) { setError('Fix the problems listed above before approving.'); return; }
    setBusy(true);
    try {
      await api(`/api/admin/drafts/${encodeURIComponent(draft.code)}/approve`, { method: 'POST' });
      setNotice(`${draft.language_name || draft.code} is now live for learners.`);
      setDraft(null); await load();
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };

  const discard = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      await api(`/api/admin/drafts/${encodeURIComponent(draft.code)}`, { method: 'DELETE' });
      setDraft(null); await load();
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };

  const rows = useMemo(() => (draft?.rows ?? []).filter(r => !storyOnly || isStory(r.path)), [draft, storyOnly]);
  const dirty = Object.keys(edits).length > 0;

  // ---- token gate ----
  if (needToken) {
    return (
      <Shell>
        <div style={{ ...card, maxWidth: 420, margin: '60px auto' }}>
          <h2 style={{ margin: '0 0 8px', fontSize: 18 }}>Admin token</h2>
          <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '0 0 14px' }}>{error}</p>
          <input style={input} type="password" value={tokenInput} onChange={e => setTokenInput(e.target.value)} placeholder="ADMIN_TOKEN" />
          <button className="btn-primary" style={{ marginTop: 12 }} onClick={() => { writeToken(tokenInput); setError(null); load(); }}>Continue</button>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      {locales && !locales.writable && (
        <div style={banner('warn')}>This is the hosted site, so drafts can't be saved here. Run the app in your own copy or a Codespace to generate and approve locales, then commit <code>backend/scenario.json</code> and redeploy.</div>
      )}
      {locales && !locales.generator_ready && (
        <div style={banner('warn')}>To generate drafts, set <code>ANTHROPIC_API_KEY</code> (in a <code>.env.local</code> file at the repo root, or as an environment variable) and restart the server.</div>
      )}
      {error && <div style={banner('bad')}>{error}</div>}
      {notice && <div style={banner('ok')}>{notice}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: draft ? '300px 1fr' : '1fr 1fr', gap: 20, alignItems: 'start' }}>
        {/* ---------- left: locales + new draft ---------- */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          <div style={card}>
            <p style={label}>Live for learners</p>
            {(locales?.approved ?? []).map(a => {
              const fb = feedback?.by_lang[a.code];
              const total = fb ? fb.yes + fb.partly + fb.no : 0;
              const open = openFeedback === a.code;
              return (
                <div key={a.code} style={{ padding: '8px 0', borderTop: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600 }}>{a.native_label} <span style={{ color: 'var(--text-3)', fontWeight: 400 }}>· {a.code}</span></div>
                      <div style={{ fontSize: 12, color: 'var(--text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.setting || (a.code === 'en' ? 'Master version' : 'Translated (setting not localized yet)')}</div>
                      {fb && total > 0 && (
                        <button onClick={() => setOpenFeedback(open ? null : a.code)} title="Learners: did the character and setting feel accurate for your region?"
                          style={{ marginTop: 4, padding: 0, border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: fb.no > 0 ? '#b91c1c' : fb.partly > 0 ? '#92400e' : '#166534' }}>
                          Regional fit: {fb.yes} yes · {fb.partly} partly · {fb.no} no{fb.comments.length ? ` · ${fb.comments.length} comment(s) ${open ? '▴' : '▾'}` : ''}
                        </button>
                      )}
                    </div>
                    {a.code !== 'en' && (
                      <button style={ghost} onClick={() => setForm({ code: a.code, language_name: a.native_label, setting: a.setting, notes: '' })}>Re-localize</button>
                    )}
                  </div>
                  {open && fb && fb.comments.length > 0 && (
                    <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {fb.comments.map((c, i) => (
                        <li key={i} style={{ fontSize: 13, background: 'var(--surface2)', borderRadius: 8, padding: '8px 10px' }}>
                          <div dir="auto" style={{ color: 'var(--text-1)' }}>{c.text}</div>
                          <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>{c.fit === 'no' ? 'Felt off' : 'Partly'} · character: {c.character} · {c.timestamp ? new Date(c.timestamp + 'Z').toLocaleDateString() : ''}</div>
                        </li>
                      ))}
                      <li>
                        <button style={{ ...ghost, fontSize: 12 }} onClick={() => setForm({ code: a.code, language_name: a.native_label, setting: a.setting,
                          notes: 'Learner feedback to address:\n' + fb.comments.slice(0, 10).map(c => '- ' + c.text).join('\n') })}>
                          Re-localize using this feedback
                        </button>
                      </li>
                    </ul>
                  )}
                </div>
              );
            })}
            {feedback && !feedback.persistent && (
              <p style={{ fontSize: 12, color: 'var(--text-3)', margin: '8px 0 0' }}>On the hosted site, feedback is only kept in Vercel's logs (search for REVIEW).</p>
            )}
          </div>

          {(locales?.drafts.length ?? 0) > 0 && (
            <div style={card}>
              <p style={label}>Waiting for review</p>
              {locales!.drafts.map(d => (
                <button key={d.code} onClick={() => openDraft(d.code)}
                  style={{ display: 'block', width: '100%', textAlign: 'left', padding: '8px 10px', margin: '6px 0 0', borderRadius: 8, cursor: 'pointer',
                    border: `1px solid ${draft?.code === d.code ? 'var(--purple)' : 'var(--border)'}`, background: draft?.code === d.code ? 'rgba(107,70,193,0.06)' : 'none' }}>
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-1)' }}>{d.language_name || d.code} <span style={{ color: 'var(--text-3)', fontWeight: 400 }}>· {d.code}{d.is_new_language ? ' · new' : ''}</span></div>
                  <div style={{ fontSize: 12, color: d.problems ? '#b91c1c' : 'var(--text-3)' }}>{d.problems ? `${d.problems} problem(s) to fix` : d.setting}</div>
                </button>
              ))}
            </div>
          )}

          <div style={card}>
            <p style={label}>Draft a locale</p>
            <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '0 0 14px', lineHeight: 1.5 }}>
              Claude rewrites the English story for the language and place (institution, money, calendar), keeping the same decision and the video's line. Nothing reaches learners until you approve it.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 10, marginBottom: 10 }}>
              <div><label style={label}>Code</label><input style={input} value={form.code} placeholder="pt-BR" onChange={e => setForm({ ...form, code: e.target.value })} /></div>
              <div><label style={label}>Language name</label><input style={input} value={form.language_name} placeholder="Português (Brasil)" onChange={e => setForm({ ...form, language_name: e.target.value })} /></div>
            </div>
            <label style={label}>Setting</label>
            <input style={{ ...input, marginBottom: 10 }} value={form.setting} placeholder="São Paulo, Brazil — private university" onChange={e => setForm({ ...form, setting: e.target.value })} />
            <label style={label}>Notes for the writer (optional)</label>
            <textarea style={{ ...input, minHeight: 60, marginBottom: 12 }} value={form.notes} placeholder="e.g. use the January intake as the deadline" onChange={e => setForm({ ...form, notes: e.target.value })} />
            <button className="btn-primary" disabled={generating || !form.code || !form.setting || !locales?.writable || !locales?.generator_ready} onClick={() => generate()}
              style={{ opacity: generating || !form.code || !form.setting || !locales?.writable || !locales?.generator_ready ? 0.5 : 1 }}>
              {generating ? 'Drafting… (about a minute)' : 'Generate draft'}
            </button>
          </div>
        </div>

        {/* ---------- right: review ---------- */}
        {draft ? (
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 14 }}>
              <div style={{ flex: 1 }}>
                <h2 style={{ margin: 0, fontSize: 20 }}>{draft.language_name || draft.code} <span style={{ color: 'var(--text-3)', fontWeight: 400, fontSize: 15 }}>· {draft.code}{draft.is_new_language ? ' · new language' : ''}</span></h2>
                <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-3)' }}>{draft.setting} · drafted {new Date(draft.created_at).toLocaleString()} by {draft.model}</p>
              </div>
              <button style={ghost} disabled={busy || generating} onClick={() => generate({ code: draft.code, language_name: draft.language_name, setting: draft.setting, notes: draft.notes })}>Regenerate</button>
              <button style={ghost} disabled={busy} onClick={discard}>Discard</button>
            </div>

            {draft.problems.length > 0 && (
              <div style={banner('bad')}><b>Must fix before approving:</b><ul style={{ margin: '4px 0 0', paddingLeft: 18, listStyle: 'disc' }}>{draft.problems.map(p => <li key={p}>{p}</li>)}</ul></div>
            )}
            {draft.review_notes.length > 0 && (
              <div style={banner('warn')}><b>Check with a native speaker:</b><ul style={{ margin: '4px 0 0', paddingLeft: 18, listStyle: 'disc' }}>{draft.review_notes.map(n => <li key={n}>{n}</li>)}</ul></div>
            )}
            {draft.is_new_language && (
              <div style={banner('warn')}>New language: the videos will play in English with this language's on-screen text until videos are recorded for it.</div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 14, margin: '6px 0 10px' }}>
              <label style={{ fontSize: 13, color: 'var(--text-2)', display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
                <input type="checkbox" checked={storyOnly} onChange={e => setStoryOnly(e.target.checked)} /> Story fields only
              </label>
              <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{rows.length} fields</span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '150px 1fr 1.2fr 1fr', gap: 0, fontSize: 13, border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
              {['Field', 'English master', 'Draft (editable)', 'What the draft says (back-translation)'].map(h => (
                <div key={h} style={{ padding: '8px 10px', background: 'var(--surface2)', fontWeight: 700, fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</div>
              ))}
              {rows.map(r => {
                const f = friendly(r.path);
                const value = edits[r.path] ?? r.text;
                const changed = r.path in edits;
                return (
                  <div key={r.path} style={{ display: 'contents' }}>
                    <div style={{ padding: 10, borderTop: '1px solid var(--border)' }}>
                      <div style={{ fontWeight: 600, color: 'var(--text-1)' }}>{f.group}</div>
                      <div style={{ color: 'var(--text-3)', fontSize: 12 }}>{f.field}</div>
                    </div>
                    <div style={{ padding: 10, borderTop: '1px solid var(--border)', color: 'var(--text-2)', lineHeight: 1.5 }}>{r.english}</div>
                    <div style={{ padding: 6, borderTop: '1px solid var(--border)' }}>
                      <textarea dir="auto" value={value} onChange={e => setEdits({ ...edits, [r.path]: e.target.value })}
                        style={{ ...input, minHeight: Math.min(160, 40 + Math.ceil(value.length / 45) * 20), resize: 'vertical', borderColor: changed ? 'var(--purple)' : 'var(--border2)' }} />
                      {r.current && r.current !== value && (
                        <div style={{ fontSize: 11, color: 'var(--text-3)', margin: '4px 2px 0' }} dir="auto">Was: {r.current}</div>
                      )}
                    </div>
                    <div style={{ padding: 10, borderTop: '1px solid var(--border)', color: 'var(--text-3)', lineHeight: 1.5, fontStyle: 'italic' }}>
                      {changed ? <span style={{ fontStyle: 'normal' }}>(edited by you)</span> : r.back_translation}
                    </div>
                  </div>
                );
              })}
            </div>

            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 16, position: 'sticky', bottom: 0, background: 'var(--surface)', padding: '10px 0' }}>
              <button style={ghost} disabled={!dirty || busy} onClick={save}>{dirty ? `Save ${Object.keys(edits).length} edit(s)` : 'No unsaved edits'}</button>
              <button className="btn-primary" disabled={busy || !locales?.writable} onClick={approve}>Approve & publish</button>
            </div>
          </div>
        ) : (
          <div style={{ ...card, color: 'var(--text-3)', fontSize: 14, lineHeight: 1.6 }}>
            <p style={{ ...label, marginBottom: 10 }}>How this works</p>
            <ol style={{ margin: 0, paddingLeft: 18, listStyle: 'decimal' }}>
              <li>Pick a language and describe the place. For an existing language, click <b>Re-localize</b>.</li>
              <li>Claude drafts it in about a minute, with an English back-translation of every line.</li>
              <li>Review side by side, ideally with a native speaker, and edit anything.</li>
              <li><b>Approve & publish</b> adds it to <code>scenario.json</code>. Commit and redeploy to put it on the hosted site.</li>
            </ol>
          </div>
        )}
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg, #f4f2fa)' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 28px', borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
        <img src="/logo.png" alt="" style={{ height: 26 }} onError={e => (e.currentTarget.style.display = 'none')} />
        <span style={{ fontWeight: 700, fontSize: 17 }}>iClass</span>
        <span style={{ color: 'var(--text-3)' }}>| Locale studio</span>
        <a href="/" style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--purple)' }}>Open the simulation ↗</a>
      </header>
      <main style={{ padding: 24, maxWidth: 1500, margin: '0 auto' }}>{children}</main>
    </div>
  );
}
