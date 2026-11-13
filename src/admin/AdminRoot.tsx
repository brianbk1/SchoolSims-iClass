import { useEffect, useState } from 'react';
import AdminApp from './AdminApp';
import SimBuilder, { api, type LangOpt } from './SimBuilder';
import LooksAdmin from './LooksAdmin';

const TOKEN_KEY = 'iclass_admin_token';
const readToken = () => { try { return localStorage.getItem(TOKEN_KEY) ?? ''; } catch { return ''; } };

type Tab = 'builder' | 'looks' | 'languages';

export default function AdminRoot() {
  const [tab, setTab] = useState<Tab>(() => (window.location.hash.includes('languages') ? 'languages' : window.location.hash.includes('looks') ? 'looks' : 'builder'));
  const [langs, setLangs] = useState<LangOpt[]>([]);
  useEffect(() => { api<{ language_options: LangOpt[] }>('/api/admin/whoami').then(w => setLangs(w.language_options ?? [])).catch(() => {}); }, []);
  const [tokenMsg, setTokenMsg] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState(readToken());
  const [key, setKey] = useState(0);

  const tabBtn = (t: Tab, text: string) => (
    <button onClick={() => { setTab(t); window.location.hash = t === 'builder' ? '' : t; }}
      className={`adm-tab${tab === t ? ' on' : ''}`}>{text}</button>
  );

  return (
    <div style={{ minHeight: '100vh', background: '#f4f5f9' }}>
      <header className="adm-header">
        <span style={{ fontWeight: 800, fontSize: 17, color: '#fff' }}>SchoolSims</span>
        {tabBtn('builder', 'Sim Builder')}
        {tabBtn('looks', 'Character looks')}
        {tabBtn('languages', 'Languages (built-in sim)')}
        <span className="adm-beta">Beta</span>
        <a href="/" style={{ marginInlineStart: 'auto', fontSize: 13, color: '#c7d2fe' }}>Open the player ↗</a>
      </header>
      <main style={{ padding: 24, maxWidth: 1500, margin: '0 auto' }}>
        {tokenMsg !== null ? (
          <div style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: 14, padding: 20, maxWidth: 420, margin: '60px auto' }}>
            <h2 style={{ margin: '0 0 8px', fontSize: 18 }}>Admin token</h2>
            <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '0 0 14px' }}>{tokenMsg} Builders use ADMIN_TOKEN; SchoolSims reviewers use REVIEWER_TOKEN.</p>
            <input style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--border2)', borderRadius: 8, padding: '9px 11px' }}
              type="password" value={tokenInput} onChange={e => setTokenInput(e.target.value)} aria-label="Admin token" />
            <button className="btn-primary" style={{ marginTop: 12 }}
              onClick={() => { try { localStorage.setItem(TOKEN_KEY, tokenInput); } catch { /* ignore */ } setTokenMsg(null); setKey(k => k + 1); }}>
              Continue
            </button>
          </div>
        ) : tab === 'builder' ? (
          <SimBuilder key={key} onNeedToken={m => setTokenMsg(m)} />
        ) : tab === 'looks' ? (
          <LooksAdmin key={key} languages={langs} />
        ) : (
          <>
            <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '0 0 14px' }}>
              Locale studio for the built-in sim. AI-built sims get their languages from the Sim Builder preview.
            </p>
            <AdminApp key={key} />
          </>
        )}
      </main>
    </div>
  );
}
