import { SKIN_TONES } from '../characterFilters';
import { useCallback, useEffect, useState } from 'react';
import { api, LangPicker, type LangOpt } from './SimBuilder';

interface Look {
  id: string; label: string; custom: boolean; gender: string; role?: string; skin_tone?: string; langs: string[];
  names: Record<string, string>; labels: Record<string, string>; photo: string; videos: string[];
}

const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 14, padding: 20 };
const lbl: React.CSSProperties = { display: 'block', fontSize: 12, fontWeight: 700, color: '#1e2a5a', margin: '0 0 5px' };
const inp: React.CSSProperties = { width: '100%', boxSizing: 'border-box', border: '1px solid var(--border2)', borderRadius: 8, padding: '9px 11px', fontSize: 14, fontFamily: 'inherit', background: 'var(--surface)', color: 'var(--text-1)' };
const ghost: React.CSSProperties = { fontSize: 13, background: 'var(--surface)', border: '1px solid var(--border2)', borderRadius: 8, padding: '7px 12px', cursor: 'pointer', color: 'var(--text-2)' };
const navy: React.CSSProperties = { fontSize: 14, fontWeight: 600, background: '#1e2a5a', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 18px', cursor: 'pointer' };

/** Shrink a photo in the browser (keeps uploads small) and return a JPEG data URL. */
function shrink(file: File, maxH = 1400): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, maxH / img.height);
      const c = document.createElement('canvas');
      c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
      c.getContext('2d')!.drawImage(img, 0, 0, c.width, c.height);
      resolve(c.toDataURL('image/jpeg', 0.9));
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => reject(new Error('That file isn\'t an image.'));
    img.src = URL.createObjectURL(file);
  });
}

export const ROLES: Record<string, [string, string]> = {
  leader: ['School leader', 'school leader in their 40s, business-casual clothing'],
  teacher: ['Teacher', 'teacher in their 30s, smart-casual clothing'],
  staff: ['School staff', 'school staff member in their 30s or 40s, smart-casual clothing'],
  parent: ['Parent', 'parent in their late 30s, everyday casual clothing'],
  student_elem: ['Student (elementary)', 'elementary school student about 9 years old, casual school clothes'],
  student_middle: ['Student (middle school)', 'middle school student about 12 years old, casual school clothes'],
  student_high: ['Student (high school)', 'high school student about 16 years old, casual school clothes'],
  student_college: ['Student (college)', 'college student about 20 years old, casual clothes'],
};
const chatgptPrompt = (region: string, gender: string, desc = '', role = 'leader') =>
  `Photorealistic head-and-shoulders portrait of a ${gender || 'female'} ${(ROLES[role] ?? ROLES.leader)[1]}, from ${region || 'the region'}${desc ? ` (${desc})` : ''}, ` +
  'looking at the camera with a warm, friendly expression, soft even light, ' +
  'plain light-grey studio background, centred, portrait orientation (2:3), no text, no logos. Not a real or famous person.';

interface Suggestion { label: string; gender: string; langs: string[]; names: Record<string, string>; portrait: string; why: string; added?: boolean; error?: string; updating?: boolean }

const EMPTY = { label: '', gender: 'female', role: 'leader', skin_tone: '', langs: [] as string[], names: {} as Record<string, string>, photo: '', generate_prompt: '' };

export default function LooksAdmin({ languages }: { languages: LangOpt[] }) {
  const [looks, setLooks] = useState<Look[]>([]);
  const [ready, setReady] = useState({ images_ready: false, blob_ready: false });
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);

  const suggestLooks = async () => {
    setBusy('suggest'); setMsg(null);
    try { setSuggestions((await api<{ looks: Suggestion[] }>('/api/admin/looks/suggest', { method: 'POST' })).looks); }
    catch (e: any) { setMsg({ tone: 'bad', text: e.message }); }
    finally { setBusy(''); }
  };

  const addSuggestion = async (i: number) => {
    const sg = suggestions[i];
    setSuggestions(list => list.map((x, j) => j === i ? { ...x, error: '' } : x));
    try {
      await api('/api/admin/looks', { method: 'POST', body: JSON.stringify({
        label: sg.label, gender: sg.gender, langs: sg.langs, names: sg.names, generate_prompt: sg.portrait }) });
      setSuggestions(list => list.map((x, j) => j === i ? { ...x, added: true } : x));
    } catch (e: any) {
      setSuggestions(list => list.map((x, j) => j === i ? { ...x, error: e.message } : x));
    }
  };

  const addAll = async () => {
    const todo = suggestions.map((x, i) => (x.added ? -1 : i)).filter(i => i >= 0);
    for (const [n, i] of todo.entries()) {
      setBusy(`all ${n + 1}/${todo.length}`);
      await addSuggestion(i);
    }
    setBusy(''); await load();
  };

  // Switch a suggestion's gender: AI rewrites its names and portrait description to match.
  const changeGender = async (i: number, gender: string) => {
    const sg = suggestions[i];
    if (sg.gender === gender) return;
    setSuggestions(list => list.map((x, j) => j === i ? { ...x, gender, updating: true, error: '' } : x));
    try {
      const r = await api<Suggestion>('/api/admin/looks/adapt', { method: 'POST', body: JSON.stringify({ ...sg, gender }) });
      setSuggestions(list => list.map((x, j) => j === i ? { ...x, ...r, why: `Switched to ${gender}. ${sg.why}`, updating: false } : x));
    } catch (e: any) {
      setSuggestions(list => list.map((x, j) => j === i ? { ...x, gender: sg.gender, updating: false, error: e.message } : x));
    }
  };

  const useInForm = (sg: Suggestion) => {
    setEditing(null);
    setForm({ label: sg.label, gender: sg.gender, role: 'leader', skin_tone: '', langs: sg.langs, names: sg.names, photo: '', generate_prompt: ready.images_ready ? sg.portrait : '' });
    setMsg({ tone: 'ok', text: ready.images_ready ? 'Filled in below. Check it, then Add look.' : 'Filled in below. Make the portrait in ChatGPT with the prompt shown, upload it, then Add look.' });
    setPortraitHint(sg.portrait);
  };
  const [portraitHint, setPortraitHint] = useState('');

  const load = useCallback(async () => {
    const r = await api<{ looks: Look[]; images_ready: boolean; blob_ready: boolean }>('/api/admin/looks');
    setLooks(r.looks); setReady({ images_ready: r.images_ready, blob_ready: r.blob_ready });
  }, []);
  useEffect(() => { load().catch(e => setMsg({ tone: 'bad', text: e.message })); }, [load]);

  const set = (p: Partial<typeof EMPTY>) => setForm(f => ({ ...f, ...p }));
  const nameLangs = ['default', ...form.langs];

  const suggest = async () => {
    setBusy('names'); setMsg(null);
    try {
      const r = await api<{ names: Record<string, string> }>('/api/admin/looks/suggest-names', { method: 'POST', body: JSON.stringify({ label: form.label, gender: form.gender, langs: form.langs }) });
      set({ names: { ...form.names, ...r.names } });
    } catch (e: any) { setMsg({ tone: 'bad', text: e.message }); }
    finally { setBusy(''); }
  };

  const save = async () => {
    setBusy('save'); setMsg(null);
    try {
      const body = JSON.stringify({ ...form, names: form.names });
      if (editing) await api(`/api/admin/looks/${editing}`, { method: 'PUT', body });
      else await api('/api/admin/looks', { method: 'POST', body });
      setMsg({ tone: 'ok', text: editing ? 'Look updated.' : 'Look added. It now appears in the learner menu for AI-built sims and in the video table.' });
      setForm(EMPTY); setEditing(null); await load();
    } catch (e: any) { setMsg({ tone: 'bad', text: e.message }); }
    finally { setBusy(''); }
  };

  const edit = (l: Look) => {
    setEditing(l.id);
    setForm({ label: l.label, gender: l.gender || 'female', role: l.role || 'leader', skin_tone: l.skin_tone || '', langs: l.langs, names: l.names, photo: '', generate_prompt: '' });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const remove = async (l: Look) => {
    if (!confirm(`Remove the look "${l.label}"? Videos already made with it stay in the sims but can't be remade.`)) return;
    try { await api(`/api/admin/looks/${l.id}`, { method: 'DELETE' }); await load(); }
    catch (e: any) { setMsg({ tone: 'bad', text: e.message }); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 1100 }}>
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <h2 style={{ fontSize: 20, fontWeight: 700, color: '#1e2a5a', margin: 0, flex: 1 }}>Suggested looks</h2>
          {suggestions.some(x => !x.added) && ready.images_ready && (
            <button style={ghost} disabled={!!busy} onClick={addAll}>{busy.startsWith('all') ? `Adding ${busy.slice(4)}…` : 'Add all (generate portraits)'}</button>
          )}
          <button style={navy} disabled={!!busy} onClick={suggestLooks}>{busy === 'suggest' ? 'Thinking…' : '✦ Suggest looks'}</button>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '6px 0 0' }}>
          AI checks which languages have no fitting character (and which regions lack a woman or a man) and proposes looks to fill the gaps.
          {ready.images_ready ? ' Adding one generates its portrait (about 30 seconds each).' : ' Add OPENAI_API_KEY to generate portraits here; otherwise use a suggestion in the form and upload a photo.'}
        </p>
        {suggestions.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12, marginTop: 14 }}>
            {suggestions.map((sg, i) => (
              <div key={i} style={{ border: `1px solid ${sg.added ? '#bbf7d0' : 'var(--border)'}`, background: sg.added ? '#f0fdf4' : 'var(--surface)', borderRadius: 12, padding: 14 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <b>{sg.label}</b>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-3)' }}>
                    <select value={sg.gender} disabled={sg.added || sg.updating} aria-label={`Gender for ${sg.label}`}
                      onChange={e => changeGender(i, e.target.value)}
                      style={{ ...inp, width: 'auto', padding: '3px 6px', fontSize: 12 }}>
                      <option value="female">Female</option><option value="male">Male</option>
                    </select>
                    {sg.langs.join(', ')}
                  </span>
                </div>
                {sg.updating && <p style={{ fontSize: 12, color: '#3730a3', margin: '4px 0' }}>Updating names and description…</p>}
                <p style={{ fontSize: 12, color: '#8b1e3f', margin: '4px 0' }}>{sg.why}</p>
                <p dir="auto" style={{ fontSize: 13, margin: '4px 0' }}>{Object.entries(sg.names).map(([l, n]) => `${n} (${l})`).join(' · ')}</p>
                <textarea rows={5} disabled={sg.updating} style={{ ...inp, fontSize: 12, padding: 6, resize: 'vertical' }} value={sg.portrait} aria-label={`Portrait description for ${sg.label}`}
                  onChange={e => setSuggestions(list => list.map((x, j) => j === i ? { ...x, portrait: e.target.value } : x))} />
                {sg.error && <p style={{ fontSize: 12, color: '#b91c1c', margin: '4px 0' }}>{sg.error}</p>}
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  {sg.added ? <span style={{ fontSize: 13, color: '#166534', fontWeight: 600 }}>✓ Added</span> : (
                    <>
                      {ready.images_ready && <button style={{ ...navy, padding: '6px 12px', fontSize: 13 }} disabled={!!busy || sg.updating}
                        onClick={async () => { setBusy(`one ${i}`); await addSuggestion(i); setBusy(''); await load(); }}>
                        {busy === `one ${i}` ? 'Generating…' : 'Add'}</button>}
                      <button style={{ ...ghost, padding: '6px 12px' }} onClick={() => useInForm(sg)}>Edit in form</button>
                      <button style={{ ...ghost, padding: '6px 12px' }} onClick={() => setSuggestions(list => list.filter((_, j) => j !== i))}>Dismiss</button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={card}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: '#1e2a5a', margin: '0 0 6px' }}>{editing ? 'Edit look' : 'Add a character look'}</h2>
        <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '0 0 16px' }}>
          A look is the face of the featured character: a portrait, a name for each language, and the languages it suits
          (it's suggested first for those). Looks you add are used by AI-built sims and their videos; the built-in sim keeps its own five.
        </p>
        {msg && <div style={{ borderRadius: 10, padding: '10px 14px', fontSize: 13, marginBottom: 12, background: msg.tone === 'ok' ? '#f0fdf4' : '#fef2f2', color: msg.tone === 'ok' ? '#166534' : '#991b1b', border: `1px solid ${msg.tone === 'ok' ? '#bbf7d0' : '#fecaca'}` }}>{msg.text}</div>}
        <div className="sb-grid3">
          <div><label style={lbl} htmlFor="lk-label">Region / name of the look</label>
            <input id="lk-label" style={inp} value={form.label} onChange={e => set({ label: e.target.value })} placeholder="Middle East" /></div>
          <div><label style={lbl} htmlFor="lk-gender">Gender (sets the video voice)</label>
            <select id="lk-gender" style={inp} value={form.gender} onChange={e => set({ gender: e.target.value })}>
              <option value="female">Female</option><option value="male">Male</option>
            </select></div>
          <div><label style={lbl} htmlFor="lk-role">Who they are</label>
            <select id="lk-role" style={inp} value={form.role} onChange={e => set({ role: e.target.value })}>
              {Object.entries(ROLES).map(([k, [label]]) => <option key={k} value={k}>{label}</option>)}
            </select></div>
          <div><label style={lbl} id="lk-tone">Skin tone (learners can filter by it)</label>
            <div role="radiogroup" aria-labelledby="lk-tone" style={{ display: 'flex', gap: 8, alignItems: 'center', paddingTop: 4 }}>
              <button type="button" role="radio" aria-checked={!form.skin_tone} onClick={() => set({ skin_tone: '' })}
                style={{ ...ghost, padding: '5px 10px', fontSize: 12, background: !form.skin_tone ? '#ede9fe' : '#fff' }}>Not set</button>
              {SKIN_TONES.map(t => (
                <button key={t.id} type="button" role="radio" aria-checked={form.skin_tone === t.id} aria-label={t.id} title={t.id}
                  onClick={() => set({ skin_tone: t.id })}
                  style={{ width: 28, height: 28, borderRadius: '50%', background: t.color, border: '2px solid #fff', cursor: 'pointer', padding: 0,
                    boxShadow: form.skin_tone === t.id ? '0 0 0 3px var(--purple)' : '0 0 0 1px var(--border2)' }} />
              ))}
            </div></div>
        </div>

        <div style={{ marginTop: 14 }}>
          <label style={lbl}>Languages this look suits</label>
          <LangPicker options={[{ code: 'en', label: 'English', native: 'English (International)', place: '' }, ...languages]}
            chosen={form.langs} setChosen={langs => set({ langs })} />
        </div>

        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <label style={{ ...lbl, margin: 0 }}>Name in each language</label>
            <button style={{ ...ghost, padding: '4px 10px', fontSize: 12 }} disabled={!form.label || busy === 'names'} onClick={suggest}>
              {busy === 'names' ? 'Suggesting…' : '✦ Suggest names'}
            </button>
          </div>
          <div className="sb-langs">
            {nameLangs.map(code => (
              <label key={code} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                <span style={{ width: 52, color: 'var(--text-3)' }}>{code}</span>
                <input dir="auto" style={{ ...inp, padding: '6px 8px' }} value={form.names[code] ?? ''}
                  onChange={e => set({ names: { ...form.names, [code]: e.target.value } })} aria-label={`Name in ${code}`} />
              </label>
            ))}
          </div>
        </div>

        <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '160px 1fr', gap: 16, alignItems: 'start' }}>
          <div style={{ width: 160, height: 200, borderRadius: 12, background: '#f1f5f9', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, color: 'var(--text-3)' }}>
            {form.photo ? <img src={form.photo} alt="New portrait" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              : editing ? <img src={looks.find(l => l.id === editing)?.photo} alt="Current portrait" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              : 'No photo yet'}
          </div>
          <div>
            <label style={lbl}>Portrait</label>
            <input type="file" accept="image/*" aria-label="Upload a portrait"
              onChange={async e => { const f = e.target.files?.[0]; if (f) { try { set({ photo: await shrink(f), generate_prompt: '' }); } catch (err: any) { setMsg({ tone: 'bad', text: err.message }); } } }} />
            <p style={{ fontSize: 12, color: 'var(--text-3)', margin: '6px 0 10px' }}>
              Head and shoulders, looking at the camera, plain background (it's replaced by the region's background in videos).
              To make one in ChatGPT, use this prompt:
            </p>
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <code style={{ fontSize: 12, background: '#f8fafc', border: '1px solid var(--border)', borderRadius: 8, padding: 8, flex: 1, whiteSpace: 'normal' }}>{chatgptPrompt(form.label, form.gender, portraitHint, form.role)}</code>
              <button style={{ ...ghost, padding: '4px 10px', fontSize: 12 }} onClick={() => navigator.clipboard?.writeText(chatgptPrompt(form.label, form.gender, portraitHint, form.role))}>Copy</button>
            </div>
            {ready.images_ready && (
              <div style={{ marginTop: 10 }}>
                <label style={{ ...lbl, fontWeight: 600 }}>…or describe the person and generate the portrait here</label>
                <textarea style={{ ...inp, minHeight: 50 }} value={form.generate_prompt}
                  onChange={e => set({ generate_prompt: e.target.value, photo: '' })}
                  placeholder={`A ${form.gender} ${(ROLES[form.role] ?? ROLES.leader)[0].toLowerCase()} from ${form.label || 'the region'}, warm expression`} />
              </div>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 16 }}>
          {editing && <button style={ghost} onClick={() => { setEditing(null); setForm(EMPTY); }}>Cancel</button>}
          <button style={navy} disabled={!!busy || !form.label || (!editing && !form.photo && !form.generate_prompt)} onClick={save}>
            {busy === 'save' ? (form.generate_prompt ? 'Generating portrait…' : 'Saving…') : editing ? 'Save changes' : 'Add look'}
          </button>
        </div>
      </div>

      <div style={card}>
        <p style={{ ...lbl, marginBottom: 12 }}>All looks ({looks.length})</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 14 }}>
          {looks.map(l => (
            <div key={l.id} style={{ border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
              <img src={l.photo} alt={`${l.label} portrait`} style={{ width: '100%', height: 200, objectFit: 'cover', objectPosition: 'top', background: '#f1f5f9' }} />
              <div style={{ padding: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <b style={{ fontSize: 14 }}>{l.label}</b>
                  <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{l.skin_tone && <span title={l.skin_tone} style={{ display: 'inline-block', width: 9, height: 9, borderRadius: '50%', marginInlineEnd: 4, background: SKIN_TONES.find(t => t.id === l.skin_tone)?.color }} />}{l.custom ? 'Added' : 'Built-in'} · {l.gender}{l.role && l.role !== 'leader' ? ` · ${(ROLES[l.role] ?? [l.role])[0]}` : ''}</span>
                </div>
                <p dir="auto" style={{ fontSize: 13, color: 'var(--text-2)', margin: '4px 0' }}>{l.names.default ?? Object.values(l.names)[0]}</p>
                <p style={{ fontSize: 12, color: 'var(--text-3)', margin: 0 }}>Suggested for: {l.langs.join(', ') || '—'}</p>
                {l.custom && (
                  <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                    <button style={{ ...ghost, padding: '4px 10px', fontSize: 12 }} onClick={() => edit(l)}>Edit</button>
                    <button style={{ ...ghost, padding: '4px 10px', fontSize: 12, color: '#b91c1c' }} onClick={() => remove(l)}>Remove</button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
