import type { ContextNode } from '../types';
import { useSpeech } from '../hooks/useSpeech';

export default function ContextSlide({ node, audioUrl, speechEnabled }: { node: ContextNode; audioUrl: string | null; speechEnabled: boolean }) {
  useSpeech(audioUrl, speechEnabled);
  return (
    <div className="main-card slide-enter" style={{ maxWidth: 1020, width: '100%', padding: 60 }}>
      <span id="ctx-badge" className="step-badge" style={{ marginBottom: 20, display: 'inline-block' }}>{node.step_badge}</span>
      <h2 id="ctx-title" style={{ fontSize: 36, fontWeight: 700, color: 'var(--text-1)', margin: '0 0 16px', letterSpacing: '-0.3px' }}>{node.title}</h2>
      {node.image_url && (
        <img className="scene-img" src={node.image_url} alt={node.image_alt ?? ''} onError={e => (e.currentTarget.style.display = 'none')} />
      )}
      <p id="ctx-body" style={{ fontSize: 17, color: 'var(--text-2)', lineHeight: 1.7, margin: node.learning_objective_body ? '0 0 28px' : 0 }}>{node.body}</p>
      {node.learning_objective_body && (
        <div className="callout purple-rule">
          <p id="ctx-objective-label" style={{ fontSize: 12, fontWeight: 700, color: 'var(--purple)', letterSpacing: '0.06em', textTransform: 'uppercase', margin: '0 0 6px' }}>{node.learning_objective_label}</p>
          <p id="ctx-objective" style={{ fontSize: 15, color: 'var(--text-2)', margin: 0, lineHeight: 1.6 }}>{node.learning_objective_body}</p>
        </div>
      )}
    </div>
  );
}
