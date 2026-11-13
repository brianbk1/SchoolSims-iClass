import { useEffect, useState } from 'react';
import { api } from './SimBuilder';

interface Node { id: string; badge: string; quote: string; script: string }
interface Data {
  sim: string; title: string; nodes: Node[]; videos: Record<string, Record<string, string>>;
  languages: Record<string, string>; upload_ready: boolean; max_upload: number;
}
interface SimRow { id: string; title: string; status: string }

const TOKEN_KEY = 'iclass_admin_token';
const token = () => { try { return localStorage.getItem(TOKEN_KEY) ?? ''; } catch { return ''; } };
const card: React.CSSProperties = { background: '#fff', border: '1px solid var(--border)', borderRadius: 14, padding: 20, marginBottom: 16 };
const inp: React.CSSProperties = { border: '1px solid var(--border2)', borderRadius: 8, padding: '7px 10px', fontSize: 13 };
const ghost: React.CSSProperties = { fontSize: 12, border: '1px solid var(--border2)', background: '#fff', borderRadius: 8, padding: '5px 10px', cursor: 'pointer' };

export default function SignLanguageAdmin() {
  const [simList, setSimList] = useState<SimRow[]>([]);
  const [sim, setSim] = useState('default');
  const [signLang, setSignLang] = useState('asl');
  const [data, setData] = useState<Data | null>(null);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null);
  const [links, setLinks] = useState<Record<string, string>>({});

  useEffect(() => {
    api<{ sims: SimRow[] }>('/api/admin/sims').then(r => setSimList(r.sims.filter(s => s.status !== 'superseded'))).catch(() => {});
  }, []);
  useEffect(() => {
    setData(null);
    api<Data>(`/api/admin/sign-videos?sim=${encodeURIComponent(sim)}`).then(setData).catch(e => setMsg({ tone: 'err', text: e.message }));
  }, [sim]);

  const path = (node: string) => `/api/admin/sign-videos/${encodeURIComponent(sim)}/${encodeURIComponent(node)}/${signLang}`;
  const update = (videos: Data['videos']) => setData(d => (d ? { ...d, videos } : d));

  const upload = async (node: string, file: File) => {
    if (!data) return;
    if (file.size > data.max_upload) {
      setMsg({ tone: 'err', text: `${file.name} is ${(file.size / 1e6).toFixed(1)} MB. Keep uploads under 4 MB (export at 480p; it plays in a small corner window) or paste a link to the file.` });
      return;
    }
    setBusy(node); setMsg(null);
    try {
      const r = await fetch(path(node), { method: 'PUT', body: file, headers: { 'Content-Type': file.type || 'video/mp4', 'X-Admin-Token': token() } });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail ?? `Upload failed (${r.status})`);
      update(body.videos); setMsg({ tone: 'ok', text: 'Uploaded.' });
    } catch (e) { setMsg({ tone: 'err', text: (e as Error).message }); }
    setBusy('');
  };
  const saveLink = async (node: string) => {
    setBusy(node); setMsg(null);
    try {
      const r = await api<{ videos: Data['videos'] }>(path(node), { method: 'POST', body: JSON.stringify({ url: links[node] ?? '' }) });
      update(r.videos); setLinks(l => ({ ...l, [node]: '' })); setMsg({ tone: 'ok', text: 'Link saved.' });
    } catch (e) { setMsg({ tone: 'err', text: (e as Error).message }); }
    setBusy('');
  };
  const remove = async (node: string) => {
    setBusy(node);
    try { update((await api<{ videos: Data['videos'] }>(path(node), { method: 'DELETE' })).videos); } catch (e) { setMsg({ tone: 'err', text: (e as Error).message }); }
    setBusy('');
  };

  const done = data ? data.nodes.filter(n => data.videos[n.id]?.[signLang]).length : 0;

  return (
    <div>
      <div style={card}>
        <h2 style={{ margin: '0 0 6px', fontSize: 18 }}>Sign language</h2>
        <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '0 0 14px', maxWidth: 820 }}>
          Add a signer video for each video slide. Learners turn it on with the sign-language button and it plays in a corner window, in step with the character's video.
          Record each clip with a qualified interpreter signing the character's line (the script is shown below each slide).
        </p>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'end' }}>
          <label style={{ fontSize: 12, fontWeight: 600 }}>Sim<br />
            <select style={{ ...inp, minWidth: 320 }} value={sim} onChange={e => setSim(e.target.value)}>
              <option value="default">Built-in: Leadership Decision Simulation</option>
              {simList.map(s => <option key={s.id} value={s.id}>{s.title}{s.status !== 'published' ? ` (${s.status.replace('_', ' ')})` : ''}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, fontWeight: 600 }}>Sign language<br />
            <select style={inp} value={signLang} onChange={e => setSignLang(e.target.value)}>
              {Object.entries(data?.languages ?? { asl: 'American Sign Language (ASL)' }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          {data && <span style={{ fontSize: 13, paddingBottom: 8 }}><b>{done}/{data.nodes.length}</b> video slides have a signer</span>}
        </div>
        {data && !data.upload_ready && <p style={{ fontSize: 12, color: '#92400e', margin: '12px 0 0' }}>Blob storage isn't connected, so uploads are off. You can still paste links.</p>}
        {msg && <p role="status" style={{ fontSize: 13, margin: '12px 0 0', color: msg.tone === 'ok' ? '#166534' : '#b91c1c' }}>{msg.text}</p>}
      </div>

      {data && data.nodes.length === 0 && <div style={card}>This sim has no video slides.</div>}
      {data?.nodes.map(n => {
        const url = data.videos[n.id]?.[signLang];
        return (
          <div key={n.id} style={{ ...card, display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 260px', gap: 20 }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--purple)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{n.badge || n.id}</div>
              <p style={{ fontSize: 15, fontWeight: 600, margin: '6px 0' }}>{n.quote}</p>
              {n.script && <p style={{ fontSize: 13, color: 'var(--text-2)', margin: '0 0 12px', lineHeight: 1.5 }}><b>Script:</b> {n.script}</p>}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <label style={{ ...ghost, opacity: data.upload_ready && busy !== n.id ? 1 : 0.5 }}>
                  {busy === n.id ? 'Working…' : url ? 'Replace video' : 'Upload video'}
                  <input type="file" accept="video/mp4,video/webm" hidden disabled={!data.upload_ready || !!busy}
                    onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) upload(n.id, f); }} />
                </label>
                <input style={{ ...inp, flex: 1, minWidth: 200 }} placeholder="or paste a https:// link to an MP4" value={links[n.id] ?? ''}
                  onChange={e => setLinks(l => ({ ...l, [n.id]: e.target.value }))} aria-label={`Link for ${n.badge || n.id}`} />
                <button style={ghost} disabled={!links[n.id] || !!busy} onClick={() => saveLink(n.id)}>Save link</button>
                {url && <button style={{ ...ghost, color: '#b91c1c' }} disabled={!!busy} onClick={() => remove(n.id)}>Remove</button>}
              </div>
            </div>
            <div style={{ background: '#0f172a', borderRadius: 10, aspectRatio: '4 / 3', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
              {url ? <video src={url} controls muted style={{ width: '100%', height: '100%', objectFit: 'contain' }} /> : <span style={{ color: '#94a3b8', fontSize: 12 }}>No signer yet</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
