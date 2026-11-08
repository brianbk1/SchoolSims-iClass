import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { Lang, Ethnicity } from '../types';

interface OptionItem {
  value: string;
  label: string;
}

interface SetupUI {
  sim_title: string;
  setup_subtitle: string;
  before_you_begin_title: string;
  before_you_begin_body: string;
  lang_label: string;
  eth_label: string;
  start_btn: string;
  setup_badge?: string;
  suggested_hint?: string;
}

interface OptionsData {
  languages: OptionItem[];
  ethnicities: OptionItem[];
  available_combos: [string, string][];
  suggested?: Record<string, string | null>;
  setup_ui: SetupUI;
}

interface Props {
  lang: Lang;
  ethnicity: Ethnicity;
  onLangChange: (l: Lang) => void;
  onEthnicityChange: (e: Ethnicity) => void;
  onStart: () => void;
}

function CustomSelect<T extends string>({ value, options, onChange, id }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; id: string }) {
  const [open, setOpen] = useState(false);
  const [menuStyle, setMenuStyle] = useState<React.CSSProperties>({});
  const triggerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const current = options.find(o => o.value === value);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const inTrigger = triggerRef.current?.contains(e.target as Node);
      const inMenu = menuRef.current?.contains(e.target as Node);
      if (!inTrigger && !inMenu) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const handleOpen = () => {
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const approxMenuH = Math.min(220, options.length * 44);
      const openUp = spaceBelow < approxMenuH + 10;
      setMenuStyle({
        position: 'fixed',
        ...(openUp
          ? { bottom: window.innerHeight - rect.top + 6 }
          : { top: rect.bottom + 6 }),
        left: rect.left,
        width: rect.width,
        zIndex: 9999,
      });
    }
    setOpen(o => !o);
  };

  return (
    <div ref={triggerRef} className={`csel${open ? ' open' : ''}`} id={id}>
      <div className="csel-trigger" onClick={handleOpen}>
        <span className="csel-value">{current?.label}</span>
        <svg className="csel-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="6 9 12 15 18 9"/></svg>
      </div>
      {open && createPortal(
        <div ref={menuRef} className="csel-menu" style={{ ...menuStyle, display: 'block' }}>
          {options.map(o => (
            <div
              key={o.value}
              className={`csel-opt${o.value === value ? ' selected' : ''}`}
              onClick={() => { onChange(o.value); setOpen(false); }}
            >
              {o.label}
            </div>
          ))}
        </div>,
        document.body
      )}
    </div>
  );
}

export default function SetupSlide({ lang, ethnicity, onLangChange, onEthnicityChange, onStart }: Props) {
  const [options, setOptions] = useState<OptionsData | null>(null);

  useEffect(() => {
    fetch(`/api/options?lang=${lang}`)
      .then(r => r.json())
      .then((data: OptionsData) => {
        setOptions(data);
        const available = data.ethnicities
          .filter(e => data.available_combos.some(([eth, lng]) => eth === e.value && lng === lang))
          .map(e => e.value);
        // Pre-select the character suggested for this language (learner can still change it);
        // otherwise make sure the current character has a video in this language.
        const suggested = data.suggested?.[lang];
        if (suggested && available.includes(suggested)) {
          onEthnicityChange(suggested as Ethnicity);
        } else if (available.length > 0 && !available.includes(ethnicity)) {
          onEthnicityChange(available[0] as Ethnicity);
        }
      })
      .catch(() => {});
  }, [lang]);

  const u = options?.setup_ui;

  // Filter ethnicities to only those with available video for current language
  const availableEthnicities = options
    ? options.ethnicities.filter(e =>
        options.available_combos.some(([eth, lng]) => eth === e.value && lng === lang)
      )
    : [];

  if (!u) {
    return (
      <div className="main-card slide-enter" style={{ maxWidth: 1020, width: '100%', padding: 60, textAlign: 'center' }}>
        <p style={{ color: 'var(--text-3)' }}>Loading...</p>
      </div>
    );
  }

  return (
    <div className="main-card slide-enter" style={{ maxWidth: 1020, width: '100%', padding: 60 }}>
      <div style={{ textAlign: 'center', marginBottom: 36 }}>
        <span className="step-badge" style={{ marginBottom: 16, display: 'inline-block' }}>● {u.setup_badge ?? 'Interactive Simulation'}</span>
        <h1 id="setup-title" style={{ fontSize: 42, fontWeight: 700, color: 'var(--text-1)', margin: '0 0 12px', letterSpacing: '-0.5px' }}>{u.sim_title}</h1>
        <p id="setup-subtitle" style={{ fontSize: 16, color: 'var(--text-3)', lineHeight: 1.6, margin: 0 }}>{u.setup_subtitle}</p>
      </div>

      <div className="callout" style={{ marginBottom: 28 }}>
        <p id="before-you-begin-title" style={{ fontSize: 12, fontWeight: 700, color: 'var(--purple)', letterSpacing: '0.06em', textTransform: 'uppercase', margin: '0 0 6px' }}>{u.before_you_begin_title}</p>
        <p id="before-you-begin-body" style={{ color: 'var(--text-2)', fontSize: 14, lineHeight: 1.6, margin: 0 }}>{u.before_you_begin_body}</p>
      </div>

      <div className="slide-grid-halves" style={{ gap: 24, marginBottom: 36 }}>
        <div>
          <label id="setup-lang-label" style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--text-3)', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 8 }}>{u.lang_label}</label>
          <CustomSelect<Lang>
            id="lang-select"
            value={lang}
            options={(options?.languages ?? []).map(o => ({ value: o.value as Lang, label: o.label }))}
            onChange={onLangChange}
          />
        </div>
        <div>
          <label id="setup-eth-label" style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--text-3)', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 8 }}>{u.eth_label}</label>
          <CustomSelect<Ethnicity>
            id="eth-select"
            value={ethnicity}
            options={availableEthnicities.map(o => ({ value: o.value as Ethnicity, label: o.label }))}
            onChange={onEthnicityChange}
          />
          {options?.suggested?.[lang] === ethnicity && u.suggested_hint && (
            <p id="eth-suggested-hint" style={{ fontSize: 12, color: 'var(--text-3)', margin: '6px 2px 0' }}>{u.suggested_hint}</p>
          )}
        </div>
      </div>

      <div style={{ textAlign: 'center' }}>
        <button id="setup-start-btn" onClick={onStart} className="btn-primary" style={{ fontSize: 16, padding: '14px 44px' }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          {u.start_btn}
        </button>
      </div>
    </div>
  );
}
