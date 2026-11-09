import type { DecisionNode } from '../types';
import { useSpeech } from '../hooks/useSpeech';

interface Props {
  node: DecisionNode;
  chosenId: string | null;
  onChoose: (targetNodeId: string, choiceId: string) => void;
  audioUrl: string | null;
  speechEnabled: boolean;
}

export default function DecisionSlide({ node, chosenId, onChoose, audioUrl, speechEnabled }: Props) {
  useSpeech(audioUrl, speechEnabled);
  return (
    <div className="main-card slide-enter" style={{ maxWidth: 1020, width: '100%', padding: 60 }}>
      <span id="dec-badge" className="step-badge" style={{ marginBottom: 20, display: 'inline-block' }}>{node.step_badge}</span>
      <h2 id="dec-prompt" style={{ fontSize: 34, fontWeight: 700, color: 'var(--text-1)', margin: '0 0 6px', letterSpacing: '-0.3px' }}>{node.prompt}</h2>
      <p id="dec-kbd-hint" className="kbd-only" style={{ fontSize: 13, color: 'var(--text-3)', margin: '0 0 28px' }}>{node.keyboard_hint}</p>
      <div className="slide-grid-halves" style={{ gap: 24 }}>
        {node.choices.map(choice => (
          <button
            key={choice.id}
            id={`choice-${choice.id.toLowerCase()}`}
            onClick={() => onChoose(choice.target_node_id, choice.id)}
            className={`choice-card${chosenId === choice.id ? ' selected' : ''}`}
            data-letter={choice.id}
            style={{ textAlign: 'start', padding: 32 }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20 }}>
              <span className="choice-letter" style={{ width: 48, height: 48, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, fontWeight: 700 }}>{choice.id}</span>
              <span className="kbd-only" style={{ fontSize: 10, fontWeight: 500, color: 'var(--text-3)', background: 'var(--surface2)', border: '1px solid var(--border2)', padding: '2px 7px', borderRadius: 4, fontFamily: "'DM Mono', monospace" }}>[{choice.id}]</span>
            </div>
            <p id={`dec-opt-${choice.id.toLowerCase()}`} style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-1)', lineHeight: 1.5, margin: 0 }}>{choice.label}</p>
          </button>
        ))}
      </div>
    </div>
  );
}
