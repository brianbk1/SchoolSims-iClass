import { useState, useEffect, useCallback } from 'react';
import type { SimData, Lang, Ethnicity, ModalType, SimNode, AudioMap, TextImageNode, DecisionNode, PathStep, Choice } from './types';
import { remainingSlides, narrationTexts, adminToken } from './simPath';
import ExtendSimPanel from './components/ExtendSimPanel';
import Header from './components/Header';
import LeftRail from './components/LeftRail';
import Footer from './components/Footer';
import SetupSlide from './components/SetupSlide';
import ContextSlide from './components/ContextSlide';
import TextImageSlide from './components/TextImageSlide';
import VideoSlide from './components/VideoSlide';
import DecisionSlide from './components/DecisionSlide';
import OutcomeSlide from './components/OutcomeSlide';
import SummarySlide from './components/SummarySlide';
import LoadingState from './components/LoadingState';
import ErrorState from './components/ErrorState';
import ModalBackdrop from './components/ModalBackdrop';

// The built-in sim keeps its hand-tuned progress; other sims use the path length.
const PROGRESS: Record<string, number> = {
  setup: 0, context: 16, text_image: 33, video: 50,
  decision: 67, outcome_a: 83, outcome_b: 83, summary: 100,
};

const initialSim = () => new URLSearchParams(window.location.search).get('sim') || 'default';

const RTL_LANGS = new Set(['ar', 'he', 'ur', 'fa']);

export default function App() {
  const [lang, setLang] = useState<Lang>('en');
  // Tell the browser which language the page is in (fonts, hyphenation, screen readers),
  // and lay the page out right-to-left for Arabic, Hebrew and Urdu.
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = RTL_LANGS.has(lang.split('-')[0]) ? 'rtl' : 'ltr';
  }, [lang]);
  const [ethnicity, setEthnicity] = useState<Ethnicity>('african');
  const [simData, setSimData] = useState<SimData | null>(null);
  const [currentNodeId, setCurrentNodeId] = useState<string>('setup');
  const [historyStack, setHistoryStack] = useState<string[]>([]);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalType>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [speechEnabled, setSpeechEnabled] = useState(true);
  const [audioMap, setAudioMap] = useState<AudioMap>({});
  const [timerSecs, setTimerSecs] = useState(0);
  const [captionsOn, setCaptionsOn] = useState(true);
  // Sign-language videos for this sim: node id -> sign language code -> URL.
  const [signVideos, setSignVideos] = useState<Record<string, Record<string, string>>>({});
  const [signOn, setSignOn] = useState(() => { try { return localStorage.getItem('iclass_sign') === '1'; } catch { return false; } });
  const [simId, setSimId] = useState<string>(initialSim);
  // decision id -> choice the learner made (for pros/cons, scores and the summary recap)
  const [picks, setPicks] = useState<Record<string, Choice>>({});
  const [isAdmin, setIsAdmin] = useState(false);

  // Admins (signed in on /admin in this browser, or running locally) get "Extend Sim".
  useEffect(() => {
    fetch('/api/admin/whoami', { headers: { 'X-Admin-Token': adminToken() } })
      .then(r => setIsAdmin(r.ok)).catch(() => setIsAdmin(false));
  }, []);

  useEffect(() => {
    setSignVideos({});
    fetch(`/api/sign-videos?sim=${encodeURIComponent(simId)}`, { headers: { 'X-Admin-Token': adminToken() } })
      .then(r => (r.ok ? r.json() : { videos: {} })).then(d => setSignVideos(d.videos ?? {})).catch(() => {});
  }, [simId]);
  const toggleSign = () => setSignOn(on => { try { localStorage.setItem('iclass_sign', on ? '0' : '1'); } catch { /* ignore */ } return !on; });

  // Keep ?sim= in the address bar so a sim can be linked directly.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (simId === 'default') url.searchParams.delete('sim'); else url.searchParams.set('sim', simId);
    window.history.replaceState(null, '', url.toString());
  }, [simId]);

  // Timer
  useEffect(() => {
    if (currentNodeId === 'setup') { setTimerSecs(0); return; }
    const id = setInterval(() => setTimerSecs(s => s + 1), 1000);
    return () => clearInterval(id);
  }, [currentNodeId === 'setup']);

  const startSimulation = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/simulation?lang=${lang}&ethnicity=${ethnicity}&sim=${encodeURIComponent(simId)}`,
        { headers: { 'X-Admin-Token': adminToken() } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: SimData = await r.json();

      // Narration for every slide (the server builds the actual text), then preload audio.
      const texts = narrationTexts(data);

      const ttsR = await fetch('/api/tts/preload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texts, lang, sim: simId }),
      });
      if (ttsR.ok) {
        const map: AudioMap = await ttsR.json();
        setAudioMap(map);
      } else {
        console.error('TTS preload failed:', ttsR.status, await ttsR.text());
      }

      setSimData(data);
      setCurrentNodeId(data.start_node_id);
      setHistoryStack([]);
      setChosenId(null);
      setPicks({});
      setTimerSecs(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [lang, ethnicity, simId]);

  const goNext = useCallback(() => {
    if (!simData) return;
    const node = simData.nodes[currentNodeId];
    if (!node || !node.next_node_id) return;
    setHistoryStack(h => [...h, currentNodeId]);
    setCurrentNodeId(node.next_node_id);
  }, [simData, currentNodeId]);

  const goBack = useCallback(() => {
    if (historyStack.length === 0) {
      setCurrentNodeId('setup');
      setSimData(null);
      setChosenId(null);
      return;
    }
    setHistoryStack(h => {
      const next = [...h];
      const prev = next.pop()!;
      setCurrentNodeId(prev);
      return next;
    });
  }, [historyStack]);

  const chooseOption = useCallback((targetNodeId: string, choiceId: string) => {
    const node = simData?.nodes[currentNodeId];
    const choice = node?.type === 'decision' ? node.choices.find(c => c.id === choiceId) : undefined;
    if (choice) setPicks(p => ({ ...p, [currentNodeId]: choice }));
    setHistoryStack(h => [...h, currentNodeId]);
    setChosenId(choiceId);
    setCurrentNodeId(targetNodeId);
  }, [currentNodeId, simData]);

  const restart = useCallback(() => {
    setCurrentNodeId('setup');
    setSimData(null);
    setChosenId(null);
    setPicks({});
    setHistoryStack([]);
    setTimerSecs(0);
  }, []);

  const isSetup = currentNodeId === 'setup';
  const ui = simData?.ui;
  const currentNode: SimNode | null = simData?.nodes[currentNodeId] ?? null;
  const builtin = (simData?.sim ?? simId) === 'default';
  const progress = isSetup ? 0
    : builtin ? (PROGRESS[currentNodeId] ?? 0)
    : simData ? Math.round(100 * historyStack.length / Math.max(1, historyStack.length + remainingSlides(simData.nodes, currentNodeId) - 1))
    : 0;

  // Decisions made on the way to this slide, in order (history ids that are decisions).
  const path: PathStep[] = simData
    ? historyStack.filter(id => simData.nodes[id]?.type === 'decision' && picks[id]).map(id => {
        const d = simData.nodes[id] as DecisionNode;
        return { decisionId: id, badge: d.step_badge, prompt: d.prompt, choice: picks[id] };
      })
    : [];
  const lastPick = path.length ? path[path.length - 1].choice : null;
  const featured = simData ? (Object.values(simData.nodes).find(n => n.type === 'text_image') as TextImageNode | undefined) : undefined;

  // keyboard A/B on decision node
  useEffect(() => {
    if (!currentNode || currentNode.type !== 'decision') return;
    const handler = (e: KeyboardEvent) => {
      const key = e.key.toUpperCase();
      const choice = currentNode.choices.find(c => c.id === key);
      if (choice) {
        e.preventDefault();
        chooseOption(choice.target_node_id, choice.id);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [currentNode, chooseOption]);

  const showBack = !isSetup && !loading && !error;
  const showNext = !isSetup && !loading && !error && currentNode?.type !== 'decision' && currentNode?.type !== 'summary' && currentNode?.next_node_id != null;

  return (
    <div id="app" style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Header
        simTitle={ui?.sim_title ?? 'Leadership Decision Simulation'}
        progress={progress}
        slideLabel={isSetup ? 'Setup' : currentNode?.step_badge ?? ''}
        timerSecs={timerSecs}
        lang={isSetup ? null : lang.toUpperCase()}
        ethnicity={isSetup ? null : (simData?.ui.ethnicity_labels?.[ethnicity] ?? ethnicity)}
        speechEnabled={speechEnabled}
        onToggleSpeech={() => { setSpeechEnabled(s => !s); window.speechSynthesis.cancel(); }}
        onRestart={isSetup ? undefined : restart}
        restartLabel={ui?.restart_btn ?? 'Restart Simulation'}
        adminHref={isAdmin ? (simId !== 'default' ? `/admin#sim=${encodeURIComponent(simId)}` : '/admin#new') : undefined}
      />

      <div className="app-body" style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        <LeftRail
          onBrief={() => setModal('brief')}
          onCast={() => setModal('cast')}
          onHelp={() => setModal('help')}
          onReset={restart}
          briefTip={ui?.rail_brief_tip ?? 'Brief'}
          castTip={ui?.rail_cast_tip ?? 'Cast'}
          helpTip={ui?.rail_help_tip ?? 'Help'}
        />

        <main className="stage" style={{ flex: 1, overflowY: 'auto', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px 32px' }}>
          {loading && <LoadingState />}
          {!loading && error && (
            <ErrorState
              title={ui?.sim_error_title ?? 'Failed to load simulation'}
              body={error}
              retryLabel={ui?.retry_btn ?? 'Retry'}
              onRetry={startSimulation}
            />
          )}
          {!loading && !error && isSetup && (
            <SetupSlide
              key={currentNodeId}
              lang={lang}
              ethnicity={ethnicity}
              onLangChange={setLang}
              onEthnicityChange={setEthnicity}
              simId={simId}
              onSimChange={setSimId}
              onStart={startSimulation}
            />
          )}
          {!loading && !error && !isSetup && currentNode?.type === 'context' && (
            <ContextSlide key={currentNodeId} node={currentNode} audioUrl={audioMap[currentNodeId] ?? null} speechEnabled={speechEnabled} />
          )}
          {!loading && !error && !isSetup && currentNode?.type === 'text_image' && simData && (
            <TextImageSlide key={currentNodeId} node={currentNode} photoUrl={simData.media.photo} photoAlt={currentNode.character_alt} audioUrl={audioMap[currentNodeId] ?? null} speechEnabled={speechEnabled} />
          )}
          {!loading && !error && !isSetup && currentNode?.type === 'video' && simData && (
            <VideoSlide
              key={currentNodeId}
              node={currentNode}
              videoUrl={currentNode.own_video ? (currentNode.video_url ?? '') : simData.media.video_template.replace('{lang}', lang)}
              captionsUrl={currentNode.own_video ? (currentNode.captions_url ?? '') : (simData.media.captions ?? `/assets/captions_${lang.split('-')[0]}.vtt`)}
              photoUrl={simData.media.photo}
              adminNote={isAdmin && currentNode.own_video && !currentNode.video_url
                ? `Admins only: no video yet for this look in ${lang}. Make it in Sim Builder → Images and character videos.`
                : undefined}
              captionsOn={captionsOn}
              onToggleCaptions={() => setCaptionsOn(c => !c)}
              sign={pickSign(signVideos[currentNodeId], lang)}
              signOn={signOn}
              onToggleSign={toggleSign}
              audioUrl={audioMap[currentNodeId] ?? null}
              speechEnabled={speechEnabled}
              speakerName={simData.media.character_name ?? ''}
              captionsOnLabel={ui?.captions_on ?? 'CC On'}
              captionsOffLabel={ui?.captions_off ?? 'CC Off'}
              videoErrorLabel={ui?.video_error ?? 'Video failed to load'}
              retryLabel={ui?.retry_btn ?? 'Retry'}
            />
          )}
          {!loading && !error && !isSetup && currentNode?.type === 'decision' && (
            <DecisionSlide key={currentNodeId} node={currentNode} chosenId={picks[currentNodeId]?.id ?? null} onChoose={chooseOption} audioUrl={audioMap[currentNodeId] ?? null} speechEnabled={speechEnabled} />
          )}
          {!loading && !error && !isSetup && currentNode?.type === 'outcome' && (
            <OutcomeSlide key={currentNodeId} node={currentNode} audioUrl={audioMap[currentNodeId] ?? null} speechEnabled={speechEnabled}
              choice={lastPick} optionLabel={ui?.option_label ?? 'Option'} />
          )}
          {!loading && !error && !isSetup && currentNode?.type === 'summary' && simData && (
            <SummarySlide
              key={currentNodeId}
              node={currentNode}
              chosenNode={builtin && chosenId ? (simData.nodes[chosenId === 'A' ? 'outcome_a' : 'outcome_b'] as import('./types').OutcomeNode) : null}
              path={builtin ? [] : path}
              optionLabel={ui?.option_label ?? 'Option'}
              restartLabel={ui?.restart_btn ?? 'Restart Simulation'}
              onRestart={restart}
              audioUrl={audioMap[currentNodeId] ?? null}
              simId={simData.sim ?? simId}
              speechEnabled={speechEnabled}
              lang={lang}
              ethnicity={ethnicity}
              chosenId={path.map(p => p.choice.id).join('') || chosenId}
              reviewTitle={ui?.review_title ?? 'Rate This Simulation'}
              reviewRatingLabel={ui?.review_rating_label ?? 'How would you rate this simulation?'}
              reviewExperienceLabel={ui?.review_experience_label ?? 'How was your experience?'}
              reviewImprovementsLabel={ui?.review_improvements_label ?? 'Any suggested improvements?'}
              reviewSubmitBtn={ui?.review_submit_btn ?? 'Submit Feedback'}
              reviewThankYou={ui?.review_thank_you ?? 'Thank you for your feedback!'}
              fitLabels={{
                question: ui?.review_fit_label ?? 'Did the character and setting feel accurate for your region?',
                yes: ui?.review_fit_yes ?? 'Yes, it felt right',
                partly: ui?.review_fit_partly ?? 'Partly',
                no: ui?.review_fit_no ?? 'No, something felt off',
                details: ui?.review_fit_details_label ?? 'What felt off? (names, places, money, holidays, wording…)',
              }}
              selectRatingHint={ui?.review_select_rating ?? 'Select a rating'}
            />
          )}
        </main>
      </div>

      {isAdmin && !isSetup && simData && (
        <button id="extend-sim-btn" className="extend-fab" onClick={() => setModal('extend')}
          title="Add AI-written decision points to this sim (admins only)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          Extend Sim
        </button>
      )}

      <Footer
        showBack={showBack}
        showNext={showNext}
        backLabel={ui?.back_btn ?? 'Back'}
        nextLabel={ui?.next_btn ?? 'Next'}
        onBack={goBack}
        onNext={goNext}
        currentNodeId={currentNodeId}
        totalNodes={simData ? Object.keys(simData.nodes).length : 0}
        progress={progress}
        restartLabel={ui?.restart_btn ?? 'Restart Simulation'}
        onRestart={restart}
      />

      {modal && (
        <ModalBackdrop onClose={() => setModal(null)}>
          {modal === 'brief' && ui && (
            <div className="modal-panel modal-enter" style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width: '100%', maxWidth: 480, padding: 28, zIndex: 50 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
                <div>
                  <p style={{ fontSize: 10, fontWeight: 700, color: 'var(--purple)', letterSpacing: '0.1em', textTransform: 'uppercase', margin: '0 0 3px' }}>{ui.brief_scenario_label}</p>
                  <p style={{ fontSize: 13, color: 'var(--text-2)', margin: 0 }}>{ui.brief_scenario_body}</p>
                </div>
                <button onClick={() => setModal(null)} className="rail-btn" style={{ width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginInlineStart: 12 }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
              <div className="callout" style={{ marginBottom: 16 }}>
                <p style={{ fontSize: 10, fontWeight: 700, color: 'var(--purple)', letterSpacing: '0.1em', textTransform: 'uppercase', margin: '0 0 6px' }}>{ui.brief_objective_label}</p>
                <p style={{ fontSize: 13, color: 'var(--text-2)', margin: 0 }}>{ui.brief_objective_body}</p>
              </div>
            </div>
          )}
          {modal === 'cast' && (
            <div id="modal-cast" className="modal-panel modal-enter" style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width: '100%', maxWidth: 480, overflow: 'hidden', zIndex: 50 }}>
              {/* Header strip */}
              <div style={{ position: 'relative', padding: '20px 24px 20px', background: 'linear-gradient(135deg,#1e293b,#0f172a)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <p style={{ fontSize: 10, fontWeight: 700, color: '#C4B5FD', letterSpacing: '0.1em', textTransform: 'uppercase', margin: '0 0 2px' }}>{ui?.cast_panel_label ?? 'Cast'}</p>
                  <p style={{ fontSize: 13, color: '#94a3b8', margin: 0 }}>{ui?.cast_panel_subtitle ?? 'Featured Character'}</p>
                </div>
                <button onClick={() => setModal(null)} className="rail-btn" style={{ width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#94a3b8', flexShrink: 0 }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
              {/* Body */}
              <div className="cast-body" style={{ padding: '24px', display: 'flex', gap: 24, alignItems: 'flex-start' }}>
                <div style={{ width: 110, height: 130, borderRadius: 14, overflow: 'hidden', border: '2px solid var(--border)', background: 'var(--surface2)', flexShrink: 0 }}>
                  {simData?.media.photo && <img src={simData.media.photo} style={{ width: '100%', height: '100%', objectFit: 'cover' }} alt={ui?.cast_panel_subtitle ?? ''} onError={e => (e.currentTarget.style.display = 'none')} />}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p id="cast-name" style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-1)', margin: '0 0 4px', letterSpacing: '-0.3px' }}>
                    {featured?.character_name ?? simData?.media.character_name ?? ''}
                  </p>
                  <p id="cast-role" style={{ fontSize: 13, color: 'var(--purple)', fontWeight: 600, margin: '0 0 16px' }}>
                    {featured?.character_title ?? ''}
                  </p>
                  <div className="callout">
                    <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.6, margin: 0 }}>{ui?.cast_panel_body ?? ''}</p>
                  </div>
                </div>
              </div>
            </div>
          )}
          {modal === 'extend' && simData && (
            <ExtendSimPanel simId={simData.sim ?? simId} simTitle={ui?.sim_title ?? ''} onClose={() => setModal(null)} />
          )}
          {modal === 'help' && ui && (
            <div id="modal-help" className="modal-panel modal-enter" style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width: '100%', maxWidth: 420, padding: 28, zIndex: 50 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                <h3 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-1)', margin: 0 }}>{ui.help_panel_title}</h3>
                <button onClick={() => setModal(null)} className="rail-btn" style={{ width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
              {[{ t: ui.help_safe_title, b: ui.help_safe_body }, { t: ui.help_no_right_title, b: ui.help_no_right_body }, { t: ui.help_replay_title, b: ui.help_replay_body }].map(({ t, b }) => (
                <div key={t} className="callout" style={{ marginBottom: 10 }}>
                  <p style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-1)', margin: '0 0 4px' }}>{t}</p>
                  <p style={{ fontSize: 12, color: 'var(--text-2)', margin: 0 }}>{b}</p>
                </div>
              ))}
            </div>
          )}
        </ModalBackdrop>
      )}
    </div>
  );
}

// Which sign language goes with the spoken language; falls back to whatever exists (ASL first).
const SIGN_FOR: Record<string, string> = { 'en-GB': 'bsl', 'en-AU': 'auslan', 'es-MX': 'lsm', 'es-ES': 'lse', 'es-AR': 'lsa', fr: 'lsf', he: 'isl', en: 'asl', 'en-US': 'asl' };
function pickSign(videos: Record<string, string> | undefined, lang: string): { code: string; url: string } | undefined {
  if (!videos) return undefined;
  const want = SIGN_FOR[lang] ?? SIGN_FOR[lang.split('-')[0]];
  const code = (want && videos[want]) ? want : (videos.asl ? 'asl' : Object.keys(videos)[0]);
  return code ? { code, url: videos[code] } : undefined;
}
