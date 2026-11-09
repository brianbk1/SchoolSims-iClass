import { useState } from 'react';
import type { SummaryNode, OutcomeNode } from '../types';
import { useSpeech } from '../hooks/useSpeech';

interface Props {
  node: SummaryNode;
  chosenNode: OutcomeNode | null;
  optionLabel: string;
  restartLabel: string;
  onRestart: () => void;
  audioUrl: string | null;
  speechEnabled: boolean;
  lang: string;
  ethnicity: string;
  chosenId: string | null;
  reviewTitle: string;
  reviewRatingLabel: string;
  reviewExperienceLabel: string;
  reviewImprovementsLabel: string;
  reviewSubmitBtn: string;
  reviewThankYou: string;
  fitLabels: { question: string; yes: string; partly: string; no: string; details: string };
  selectRatingHint: string;
}

type Fit = '' | 'yes' | 'partly' | 'no';

export default function SummarySlide({
  node, chosenNode, optionLabel,
  audioUrl, speechEnabled,
  lang, ethnicity, chosenId,
  reviewTitle, reviewRatingLabel, reviewExperienceLabel,
  reviewImprovementsLabel, reviewSubmitBtn, reviewThankYou,
  fitLabels, selectRatingHint,
}: Props) {
  useSpeech(audioUrl, speechEnabled);

  const [rating, setRating] = useState(0);
  const [hoverRating, setHoverRating] = useState(0);
  const [experience, setExperience] = useState('');
  const [improvements, setImprovements] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // Optional: did the character and setting feel accurate for the learner's region?
  const [fit, setFit] = useState<Fit>('');
  const [fitDetails, setFitDetails] = useState('');

  const handleSubmit = async () => {
    if (rating === 0 || submitting) return;
    setSubmitting(true);
    try {
      await fetch('/api/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rating,
          experience,
          improvements,
          lang,
          ethnicity,
          chosen_option: chosenId ?? '',
          regional_fit: fit,
          regional_fit_details: fit === 'yes' ? '' : fitDetails,
        }),
      });
      setSubmitted(true);
    } catch {
      // silent fail — form stays visible for retry
    } finally {
      setSubmitting(false);
    }
  };

  const Star = ({ index }: { index: number }) => {
    const filled = index <= (hoverRating || rating);
    return (
      <svg
        width="28" height="28" viewBox="0 0 24 24"
        fill={filled ? 'var(--purple)' : 'none'}
        stroke="var(--purple)" strokeWidth="1.5"
        style={{ cursor: 'pointer', transition: 'fill 0.15s' }}
        onMouseEnter={() => setHoverRating(index)}
        onMouseLeave={() => setHoverRating(0)}
        onClick={() => setRating(index)}
      >
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
      </svg>
    );
  };

  const ratingHint = rating === 0 && hoverRating === 0 ? selectRatingHint : '';

  const fitOptions: { value: Exclude<Fit, ''>; label: string }[] = [
    { value: 'yes', label: fitLabels.yes },
    { value: 'partly', label: fitLabels.partly },
    { value: 'no', label: fitLabels.no },
  ];

  return (
    <div className="slide-enter" style={{ maxWidth: 1020, width: '100%' }}>
      <span className="step-badge" style={{ marginBottom: 16, display: 'inline-block' }}>{node.step_badge}</span>
      <h2 id="sum-title" style={{ fontSize: 36, fontWeight: 700, color: 'var(--text-1)', margin: '0 0 28px', letterSpacing: '-0.3px' }}>{node.title}</h2>

      {/* Two-column grid: left = recap + reflection, right = review form */}
      <div className="sum-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 32 }}>
        {/* Left column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {chosenNode && (
            <div id="sum-recap" style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 16, padding: '24px 28px' }}>
              <span className="step-badge" style={{ marginBottom: 12, display: 'inline-block' }}>{optionLabel} {chosenNode.you_chose.slice(-1)}</span>
              <h3 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-1)', margin: '0 0 8px' }}>{chosenNode.title}</h3>
              <p style={{ fontSize: 14, color: 'var(--text-2)', margin: 0, lineHeight: 1.6 }}>{chosenNode.body}</p>
            </div>
          )}

          <div style={{ background: 'var(--purple)', borderRadius: 16, padding: '28px 28px', flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.7)" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              <p style={{ fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.7)', letterSpacing: '0.08em', textTransform: 'uppercase', margin: 0 }}>{node.reflection_prompt_label}</p>
            </div>
            <p style={{ fontSize: 20, fontWeight: 600, color: '#fff', margin: 0, lineHeight: 1.5 }}>{node.question}</p>
          </div>
        </div>

        {/* Right column: review form */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 16, padding: '28px 28px' }}>
          {submitted ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8 }}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--purple)" strokeWidth="2">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
              </svg>
              <p style={{ fontSize: 16, fontWeight: 600, color: 'var(--purple)', margin: 0 }}>
                {reviewThankYou}
              </p>
            </div>
          ) : (
            <div>
              <h3 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-1)', margin: '0 0 16px' }}>
                {reviewTitle}
              </h3>

              <label style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-1)', display: 'block', marginBottom: 8 }}>
                {reviewRatingLabel}
              </label>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 20 }}>
                {[1, 2, 3, 4, 5].map(i => <Star key={i} index={i} />)}
                {ratingHint && <span style={{ fontSize: 13, color: 'var(--text-3)', marginInlineStart: 4 }}>{ratingHint}</span>}
              </div>

              <p id="fit-question" style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-1)', margin: '0 0 8px' }}>
                {fitLabels.question}
              </p>
              <div role="radiogroup" aria-labelledby="fit-question" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: fit === 'partly' || fit === 'no' ? 10 : 20 }}>
                {fitOptions.map(o => {
                  const on = fit === o.value;
                  return (
                    <button
                      key={o.value}
                      type="button"
                      role="radio"
                      aria-checked={on}
                      onClick={() => setFit(on ? '' : o.value)}
                      style={{
                        padding: '7px 14px', borderRadius: 999, fontSize: 13, fontWeight: 500, cursor: 'pointer',
                        fontFamily: "'DM Sans', sans-serif",
                        border: `1px solid ${on ? 'var(--purple)' : 'var(--border2)'}`,
                        background: on ? 'var(--purple)' : 'var(--surface)',
                        color: on ? '#fff' : 'var(--text-2)',
                        transition: 'background 0.15s, color 0.15s',
                      }}
                    >
                      {o.label}
                    </button>
                  );
                })}
              </div>
              {(fit === 'partly' || fit === 'no') && (
                <>
                  <label htmlFor="fit-details" style={{ fontSize: 13, color: 'var(--text-2)', display: 'block', marginBottom: 6 }}>
                    {fitLabels.details}
                  </label>
                  <textarea
                    id="fit-details"
                    dir="auto"
                    value={fitDetails}
                    onChange={e => setFitDetails(e.target.value)}
                    rows={2}
                    maxLength={1000}
                    style={{
                      width: '100%', boxSizing: 'border-box', borderRadius: 8,
                      border: '1px solid var(--border2)', padding: '10px 12px',
                      fontFamily: "'DM Sans', sans-serif", fontSize: 14,
                      color: 'var(--text-1)', background: 'var(--surface)',
                      resize: 'vertical', marginBottom: 16,
                    }}
                  />
                </>
              )}

              <label style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-1)', display: 'block', marginBottom: 6 }}>
                {reviewExperienceLabel}
              </label>
              <textarea
                value={experience}
                onChange={e => setExperience(e.target.value)}
                rows={3}
                maxLength={1000}
                style={{
                  width: '100%', boxSizing: 'border-box', borderRadius: 8,
                  border: '1px solid var(--border2)', padding: '10px 12px',
                  fontFamily: "'DM Sans', sans-serif", fontSize: 14,
                  color: 'var(--text-1)', background: 'var(--surface)',
                  resize: 'vertical', marginBottom: 16,
                }}
              />

              <label style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-1)', display: 'block', marginBottom: 6 }}>
                {reviewImprovementsLabel}
              </label>
              <textarea
                value={improvements}
                onChange={e => setImprovements(e.target.value)}
                rows={3}
                maxLength={1000}
                style={{
                  width: '100%', boxSizing: 'border-box', borderRadius: 8,
                  border: '1px solid var(--border2)', padding: '10px 12px',
                  fontFamily: "'DM Sans', sans-serif", fontSize: 14,
                  color: 'var(--text-1)', background: 'var(--surface)',
                  resize: 'vertical', marginBottom: 20,
                }}
              />

              <button
                onClick={handleSubmit}
                disabled={rating === 0 || submitting}
                style={{
                  width: '100%', padding: '12px 0', borderRadius: 10,
                  border: 'none', background: rating === 0 ? 'var(--surface2)' : '#e2e0e7',
                  color: rating === 0 ? 'var(--text-3)' : 'var(--text-1)',
                  fontSize: 14, fontWeight: 600, fontFamily: "'DM Sans', sans-serif",
                  cursor: rating === 0 ? 'default' : 'pointer',
                  transition: 'background 0.15s',
                }}
              >
                {submitting ? '...' : reviewSubmitBtn}
              </button>
            </div>
          )}
        </div>
      </div>

    </div>
  );
}
