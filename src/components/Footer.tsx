interface FooterProps {
  showBack: boolean;
  showNext: boolean;
  backLabel: string;
  nextLabel: string;
  onBack: () => void;
  onNext: () => void;
  currentNodeId: string;
  totalNodes: number;
  restartLabel?: string;
  onRestart?: () => void;
  progress?: number;
}

const NODE_ORDER = ['context', 'text_image', 'video', 'decision', 'outcome_a', 'outcome_b', 'summary'];
const DOT_NODES  = ['context', 'text_image', 'video', 'decision', 'summary'];

export default function Footer({ showBack, showNext, backLabel, nextLabel, onBack, onNext, currentNodeId, restartLabel, onRestart, progress = 0 }: FooterProps) {
  // Other sims have their own slide ids: place the dots by progress instead.
  const byProgress = !NODE_ORDER.includes(currentNodeId) && currentNodeId !== 'setup';
  const progressDot = Math.min(DOT_NODES.length - 1, Math.round(progress / 100 * (DOT_NODES.length - 1)));
  return (
    <footer className="footer-bar" style={{ height: 64, padding: '0 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
      <div className="footer-side" style={{ width: 120 }}>
        {showBack && (
          <button id="back-btn" onClick={onBack} className="btn-ghost">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg>
            <span id="back-btn-text">{backLabel}</span>
          </button>
        )}
      </div>

      <div className="footer-dots" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        {DOT_NODES.map((id, i) => {
          const idx = NODE_ORDER.indexOf(currentNodeId);
          const dotIdx = NODE_ORDER.indexOf(id);
          const active = byProgress ? i === progressDot
            : id === currentNodeId || (currentNodeId === 'outcome_a' && id === 'summary') || (currentNodeId === 'outcome_b' && id === 'summary');
          const past = byProgress ? i < progressDot : dotIdx < idx;
          return (
            <div
              key={i}
              className="dot"
              style={{
                width: active ? 20 : 8,
                height: 8,
                background: active ? 'var(--purple)' : past ? '#C4B5FD' : 'var(--border2)',
              }}
            />
          );
        })}
      </div>

      <div className="footer-side" style={{ width: 180, display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
        {showNext && (
          <button id="next-btn" onClick={onNext} className="btn-primary">
            <span id="next-btn-text">{nextLabel}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="9 18 15 12 9 6"/></svg>
          </button>
        )}
        {currentNodeId === 'summary' && restartLabel && onRestart && (
          <button id="sum-restart-btn" onClick={onRestart} className="btn-primary" style={{ whiteSpace: 'nowrap' }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-5"/></svg>
            {restartLabel}
          </button>
        )}
      </div>
    </footer>
  );
}
