import { useEffect, useState } from 'react';
import AdminApp from './AdminApp';
import SimBuilder, { api, type LangOpt } from './SimBuilder';
import LooksAdmin from './LooksAdmin';
import SignLanguageAdmin from './SignLanguageAdmin';

const TOKEN_KEY = 'iclass_admin_token';
const readToken = () => { try { return localStorage.getItem(TOKEN_KEY) ?? ''; } catch { return ''; } };

type Tab = 'new' | 'review' | 'report' | 'notifications' | 'looks' | 'languages' | 'sign' | 'sim';

const tabFromHash = (): Tab => {
  const h = window.location.hash.replace('#', '');
  if (h.startsWith('sim=')) return 'sim';
  return (['review', 'report', 'notifications', 'looks', 'languages', 'sign'].includes(h) ? h : 'new') as Tab;
};

export default function AdminRoot() {
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const [langs, setLangs] = useState<LangOpt[]>([]);
  const [pending, setPending] = useState(0);
  const [tokenMsg, setTokenMsg] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState(readToken());
  const [key, setKey] = useState(0);

  useEffect(() => { api<{ language_options: LangOpt[] }>('/api/admin/whoami').then(w => setLangs(w.language_options ?? [])).catch(() => {}); }, [key]);
  // How many things wait for review (badge on the Review tab).
  useEffect(() => {
    const count = () => api<{ sims: { status: string; pending_langs?: string[] }[] }>('/api/admin/sims')
      .then(r => setPending(r.sims.filter(s => s.status === 'pending_review' || (s.status === 'published' && (s.pending_langs ?? []).length)).length))
      .catch(() => {});
    count();
    const t = setInterval(count, 60000);
    return () => clearInterval(t);
  }, [key, tab]);
  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const link = (t: Exclude<Tab, 'sim'>, text: React.ReactNode) => (
    <a href={`#${t}`} className={`adm-tab${tab === t ? ' on' : ''}`} aria-current={tab === t ? 'page' : undefined}>{text}</a>
  );
  const builderTab = tab !== 'looks' && tab !== 'languages' && tab !== 'sign';

  return (
    <div style={{ minHeight: '100vh', background: '#f4f5f9' }}>
      <header className="adm-header">
        <span style={{ fontWeight: 800, fontSize: 17, color: '#fff' }}>SchoolSims</span>
        <nav className="adm-nav" aria-label="Admin">
          {link('new', '✦ Create sim')}
          {link('review', <>Review{pending > 0 && <span className="adm-badge">{pending}</span>}</>)}
          {link('report', 'Report')}
          {link('notifications', 'Notifications')}
          {link('looks', 'Character looks')}
          {link('languages', 'Languages (built-in sim)')}
          {link('sign', 'Sign language')}
        </nav>
        <span className="adm-beta">Beta</span>
        <a href="/" target="_blank" rel="noreferrer" style={{ marginInlineStart: 'auto', fontSize: 13, color: '#c7d2fe', whiteSpace: 'nowrap' }}>Open the player ↗</a>
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
        ) : builderTab ? (
          <SimBuilder key={key} onNeedToken={m => setTokenMsg(m)} />
        ) : tab === 'looks' ? (
          <LooksAdmin key={key} languages={langs} />
        ) : tab === 'sign' ? (
          <SignLanguageAdmin key={key} />
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
