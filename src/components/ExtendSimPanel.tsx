import { useState } from 'react';
import { adminToken } from '../simPath';

interface Props {
  simId: string;
  simTitle: string;
  onClose: () => void;
}

/** "Extend Sim": admins add AI-written decision points to the sim they are playing.
 *  The result is a new draft that goes through review; the live sim is unchanged. */
export default function ExtendSimPanel({ simId, simTitle, onClose }: Props) {
  const [decisions, setDecisions] = useState(1);
  const [options, setOptions] = useState(3);
  const [tracks, setTracks] = useState(2);
  const [slides, setSlides] = useState(1);
  const [prosCons, setProsCons] = useState(true);
  const [summary, setSummary] = useState(true);
  const [notes, setNotes] = useState('');
  const [name, setName] = useState(() => { try { return localStorage.getItem('iclass_builder_name') ?? ''; } catch { return ''; } });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [doneId, setDoneId] = useState('');

  const maxTracks = decisions * options;
  const submit = async () => {
    setBusy(true); setError('');
    try { localStorage.setItem('iclass_builder_name', name); } catch { /* ignore */ }
    try {
      const r = await fetch(`/api/admin/sims/${encodeURIComponent(simId)}/extend`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Token': adminToken() },
        body: JSON.stringify({
          decisions, options_per_decision: options, total_tracks: Math.min(Math.max(tracks, decisions), maxTracks),
          slides_per_track: slides, follow_up_tracks: slides > 1, pros_cons: prosCons, summary, notes, created_by: name,
        }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail ?? `HTTP ${r.status}`);
      setDoneId(body.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-panel modal-enter" role="dialog" aria-labelledby="xs-title"
      style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width: 'calc(100% - 24px)', maxWidth: 520, maxHeight: '90dvh', overflowY: 'auto', padding: 26, zIndex: 50 }}>
      <h3 id="xs-title" style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-1)', margin: '0 0 4px' }}>Extend this sim</h3>
      <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '0 0 18px', lineHeight: 1.5 }}>
        AI writes new decision points after the last one in <b>{simTitle}</b>. You get a new draft to edit and submit for
        review; learners keep playing the current version until it's published.
      </p>

      {doneId ? (
        <div>
          <p style={{ fontSize: 14, color: '#166534', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 10, padding: '10px 14px' }}>
            Draft created. Review and edit it in the Sim Builder, then submit it for review.
          </p>
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <a className="btn-primary" href={`/admin#sim=${encodeURIComponent(doneId)}`}>Open in Sim Builder</a>
            <button className="btn-ghost" onClick={onClose}>Close</button>
          </div>
        </div>
      ) : (
        <>
          <div className="xs-grid">
            <div><label htmlFor="xs-dec">New decisions</label>
              <input id="xs-dec" type="number" min={1} max={6} value={decisions} onChange={e => setDecisions(+e.target.value || 1)} /></div>
            <div><label htmlFor="xs-opt">Options each</label>
              <input id="xs-opt" type="number" min={2} max={4} value={options} onChange={e => setOptions(+e.target.value || 2)} /></div>
            <div><label htmlFor="xs-trk">Total new tracks ({decisions}–{maxTracks})</label>
              <input id="xs-trk" type="number" min={decisions} max={maxTracks} value={tracks} onChange={e => setTracks(+e.target.value || 1)} /></div>
            <div><label htmlFor="xs-sl">Slides per track</label>
              <input id="xs-sl" type="number" min={1} max={3} value={slides} onChange={e => setSlides(+e.target.value || 1)} /></div>
          </div>
          <div style={{ display: 'flex', gap: 18, margin: '14px 0', fontSize: 14, color: 'var(--text-2)' }}>
            <label><input type="checkbox" checked={prosCons} onChange={e => setProsCons(e.target.checked)} /> Pros / cons</label>
            <label><input type="checkbox" checked={summary} onChange={e => setSummary(e.target.checked)} /> Update summary</label>
          </div>
          <div className="xs-field" style={{ marginBottom: 12 }}>
            <label htmlFor="xs-notes">What should happen next? (optional)</label>
            <textarea id="xs-notes" rows={3} value={notes} onChange={e => setNotes(e.target.value)}
              placeholder="e.g. The parent escalates to the superintendent; add a decision about communicating with staff." />
          </div>
          <div className="xs-field" style={{ marginBottom: 16 }}>
            <label htmlFor="xs-name">Your name</label>
            <input id="xs-name" value={name} onChange={e => setName(e.target.value)} />
          </div>
          {error && <p role="alert" style={{ fontSize: 13, color: '#991b1b', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: '8px 12px' }}>{error}</p>}
          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
            <button className="btn-ghost" onClick={onClose} disabled={busy}>Cancel</button>
            <button className="btn-primary" onClick={submit} disabled={busy}>{busy ? 'Writing… (1–3 min)' : 'Generate extension'}</button>
          </div>
        </>
      )}
    </div>
  );
}
