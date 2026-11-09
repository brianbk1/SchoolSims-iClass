export type NodeType = 'context' | 'text_image' | 'video' | 'decision' | 'outcome' | 'summary';
export type ImpactDirection = 'up' | 'down' | 'mid';

export interface Impact {
  key: string;
  label: string;
  direction: ImpactDirection;
  body: string;
}

export interface Choice {
  id: string;
  label: string;
  target_node_id: string;
}

export interface BaseNode {
  type: NodeType;
  next_node_id: string | null;
  step_badge: string;
}

export interface ContextNode extends BaseNode {
  type: 'context';
  title: string;
  body: string;
  learning_objective_label: string;
  learning_objective_body: string;
}

export interface TextImageNode extends BaseNode {
  type: 'text_image';
  title: string;
  body: string;
  character_name: string;
  character_title: string;
  character_alt: string;
  ti_point1: string;
  ti_point2: string;
}

export interface VideoNode extends BaseNode {
  type: 'video';
  quote: string;
  watch_listen_title: string;
  watch_listen_body: string;
}

export interface DecisionNode extends BaseNode {
  type: 'decision';
  next_node_id: null;
  prompt: string;
  keyboard_hint: string;
  choices: Choice[];
}

export interface OutcomeNode extends BaseNode {
  type: 'outcome';
  you_chose: string;
  title: string;
  body: string;
  impacts: Impact[];
}

export interface SummaryNode extends BaseNode {
  type: 'summary';
  next_node_id: null;
  title: string;
  reflection_prompt_label: string;
  question: string;
}

export type SimNode = ContextNode | TextImageNode | VideoNode | DecisionNode | OutcomeNode | SummaryNode;

export interface UIStrings {
  start_btn: string;
  back_btn: string;
  next_btn: string;
  restart_btn: string;
  lang_label: string;
  eth_label: string;
  ethnicity_labels: Record<string, string>;
  language_labels: Record<string, string>;
  sim_title: string;
  setup_subtitle: string;
  before_you_begin_title: string;
  before_you_begin_body: string;
  sim_error_title: string;
  sim_error_body: string;
  retry_btn: string;
  brief_scenario_label: string;
  brief_scenario_body: string;
  brief_objective_label: string;
  brief_objective_body: string;
  cast_panel_label: string;
  cast_panel_subtitle: string;
  cast_panel_body: string;
  help_panel_title: string;
  help_safe_title: string;
  help_safe_body: string;
  help_no_right_title: string;
  help_no_right_body: string;
  help_replay_title: string;
  help_replay_body: string;
  rail_brief_tip: string;
  rail_cast_tip: string;
  rail_help_tip: string;
  badge_en: string;
  option_label: string;
  speech_locale: string;
  review_title: string;
  review_rating_label: string;
  review_experience_label: string;
  review_improvements_label: string;
  review_submit_btn: string;
  review_thank_you: string;
  review_fit_label?: string;
  review_fit_yes?: string;
  review_fit_partly?: string;
  review_fit_no?: string;
  review_fit_details_label?: string;
  review_select_rating?: string;
  setup_badge?: string;
  suggested_hint?: string;
  captions_on?: string;
  captions_off?: string;
  video_error?: string;
}

export interface Media {
  photo: string;
  video_template: string;
  photo_alt: string;
  character_name?: string;
  has_video?: boolean;
  base_lang?: string;
  captions?: string | null;
}

export interface SimData {
  start_node_id: string;
  nodes: Record<string, SimNode>;
  ui: UIStrings;
  media: Media;
}

export type AudioMap = Record<string, string>; // node_id → /tts/xxx.mp3

// Language or regional locale code from scenario.json, e.g. 'es', 'es-AR'.
export type Lang = string;
export type Ethnicity = 'african' | 'caucasian' | 'east_asian' | 'latin' | 'south_asian';

export type ModalType = 'brief' | 'cast' | 'help' | null;
