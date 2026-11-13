import { useEffect, useRef, useState } from 'react';
import type { VideoNode } from '../types';
import { useSpeech } from '../hooks/useSpeech';

interface Props {
  node: VideoNode;
  videoUrl: string;
  captionsUrl: string;
  captionsOn: boolean;
  onToggleCaptions: () => void;
  audioUrl: string | null;
  speechEnabled: boolean;
  speakerName: string;
  captionsOnLabel: string;
  captionsOffLabel: string;
  videoErrorLabel: string;
  retryLabel: string;
  photoUrl?: string;
  adminNote?: string;
  sign?: { code: string; url: string };
  signOn?: boolean;
  onToggleSign?: () => void;
}

export default function VideoSlide({ node, videoUrl, captionsUrl, captionsOn, onToggleCaptions, audioUrl, speechEnabled, speakerName, captionsOnLabel, captionsOffLabel, videoErrorLabel, retryLabel, photoUrl, adminNote, sign, signOn, onToggleSign }: Props) {
  useSpeech(audioUrl, speechEnabled);
  const [vidErr, setVidErr] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  const retry = () => { setVidErr(false); setRetryKey(k => k + 1); };
  const mainRef = useRef<HTMLVideoElement>(null);
  const signRef = useRef<HTMLVideoElement>(null);
  const showSign = !!(sign && signOn);
  const synced = showSign && !!videoUrl && !vidErr;

  // Keep the signer in step with the character's video: play, pause, seek and speed follow it.
  useEffect(() => {
    const m = mainRef.current, s = signRef.current;
    if (!synced || !m || !s) return;
    const follow = () => {
      if (Math.abs(s.currentTime - m.currentTime) > 0.4) s.currentTime = m.currentTime;
      s.playbackRate = m.playbackRate;
      if (m.paused) s.pause(); else s.play().catch(() => {});
    };
    const evs = ['play', 'pause', 'seeked', 'ratechange', 'timeupdate'] as const;
    evs.forEach(e => m.addEventListener(e, follow));
    follow();
    return () => evs.forEach(e => m.removeEventListener(e, follow));
  }, [synced, retryKey, sign?.url]);

  return (
    <div className="main-card slide-enter" style={{ maxWidth: 1020, width: '100%', padding: 60 }}>
      <span id="vid-badge" className="step-badge" style={{ marginBottom: 20, display: 'inline-block' }}>{node.step_badge}</span>
      <div className="slide-grid-video" style={{ gap: 36 }}>
        <div className="video-wrap">
          {!videoUrl ? (
            // No clip for this look + language yet: show the character and what they say.
            <div className="video-fallback">
              {photoUrl && <img className="vf-blur" src={photoUrl} alt="" aria-hidden="true" />}
              {photoUrl && <img className="vf-photo" src={photoUrl} alt={speakerName} />}
              <div className="vf-text">
                {node.script && <p dir="auto">“{node.script}”</p>}
                {adminNote && <span className="vf-admin">{adminNote}</span>}
              </div>
            </div>
          ) : !vidErr ? (
            <video
              key={retryKey}
              ref={mainRef}
              controls
              style={{ width: '100%', height: '100%' }}
              onError={() => setVidErr(true)}
            >
              <source src={videoUrl} type="video/mp4" />
              {captionsOn && captionsUrl && <track default kind="captions" src={captionsUrl} />}
            </video>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12 }}>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" strokeWidth="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
              <span style={{ fontSize: 13, color: '#94a3b8' }}>{videoErrorLabel}</span>
              <button onClick={retry} style={{ fontSize: 12, color: '#C4B5FD', background: 'none', border: '1px solid #334155', borderRadius: 20, padding: '5px 14px', cursor: 'pointer' }}>↺ {retryLabel}</button>
            </div>
          )}
          {showSign && (
            <video
              ref={signRef}
              className={`sign-pip${synced ? '' : ' sign-solo'}`}
              src={sign!.url}
              muted
              playsInline
              controls={!synced}
              aria-label={`Sign language interpreter (${sign!.code.toUpperCase()})`}
            />
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="quote-card">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="rgba(107,70,193,0.4)" style={{ marginBottom: 8 }}><path d="M3 21c3 0 7-1 7-8V5c0-1.25-.756-2.017-2-2H4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V20c0 1 0 1 1 1z"/><path d="M15 21c3 0 7-1 7-8V5c0-1.25-.757-2.017-2-2h-4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2h.75c0 2.25.25 4-2.75 4v3c0 1 0 1 1 1z"/></svg>
            <p id="vid-quote" style={{ fontSize: 18, fontWeight: 600, color: '#f1f5f9', lineHeight: 1.5, margin: '0 0 12px' }}>{node.quote}</p>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="var(--purple)"><polygon points="5 3 19 12 5 21 5 3"/></svg>
              <span style={{ fontSize: 11, color: '#94a3b8' }}>{speakerName}</span>
            </div>
          </div>
          <div className="callout">
            <p id="vid-watch-listen-title" style={{ fontSize: 12, fontWeight: 700, color: 'var(--purple)', letterSpacing: '0.06em', textTransform: 'uppercase', margin: '0 0 6px' }}>{node.watch_listen_title}</p>
            <p id="vid-watch-listen-body" style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.6, margin: 0 }}>{node.watch_listen_body}</p>
          </div>
          <button
            onClick={onToggleCaptions}
            style={{ fontSize: 12, color: captionsOn ? 'var(--purple)' : 'var(--text-3)', background: captionsOn ? 'rgba(107,70,193,0.08)' : 'var(--surface2)', border: `1px solid ${captionsOn ? 'rgba(107,70,193,0.25)' : 'var(--border2)'}`, borderRadius: 8, padding: '6px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5 }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="6" width="20" height="14" rx="2"/><path d="M12 20v-4"/><path d="M6 6V4"/><path d="M18 6V4"/></svg>
            {captionsOn ? captionsOnLabel : captionsOffLabel}
          </button>
          {sign && onToggleSign && (
            <button
              onClick={onToggleSign}
              aria-pressed={!!signOn}
              style={{ fontSize: 12, color: signOn ? 'var(--purple)' : 'var(--text-3)', background: signOn ? 'rgba(107,70,193,0.08)' : 'var(--surface2)', border: `1px solid ${signOn ? 'rgba(107,70,193,0.25)' : 'var(--border2)'}`, borderRadius: 8, padding: '6px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5 }}
            >
              <span aria-hidden="true">🤟</span>
              {sign.code.toUpperCase()} {signOn ? 'On' : 'Off'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
