import type { SimNode, SimData } from './types';

/** Slides a node can lead to. */
export function nextIds(node: SimNode): string[] {
  if (node.type === 'decision') return node.choices.map(c => c.target_node_id);
  return node.next_node_id ? [node.next_node_id] : [];
}

/** Longest number of slides left from `id` to the end (inclusive). Used for progress. */
export function remainingSlides(nodes: Record<string, SimNode>, id: string, memo: Record<string, number> = {}): number {
  if (id in memo) return memo[id];
  memo[id] = 1; // guards against loops
  const node = nodes[id];
  if (!node) return 1;
  const rest = nextIds(node).map(n => remainingSlides(nodes, n, memo));
  memo[id] = 1 + (rest.length ? Math.max(...rest) : 0);
  return memo[id];
}

/** Text read aloud for a slide (matches backend simgen.narration_text). */
export function narrationText(node: SimNode): string {
  switch (node.type) {
    case 'decision':
      return [node.prompt, ...node.choices.map(c => `Option ${c.id}: ${c.label}`)].join('. ');
    case 'context':
      return [node.title, node.body, node.learning_objective_body].filter(Boolean).join(' ');
    case 'summary':
      return [node.title, node.body, node.question].filter(Boolean).join(' ');
    case 'video':
      return node.quote;
    default:
      return [node.title, node.body].filter(Boolean).join(' ');
  }
}

/** Narration texts to preload, per slide. The built-in sim keeps its original rules. */
export function narrationTexts(data: SimData): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [id, node] of Object.entries(data.nodes)) out[id] = narrationText(node);
  return out;
}

export const ADMIN_TOKEN_KEY = 'iclass_admin_token';
export const adminToken = () => { try { return localStorage.getItem(ADMIN_TOKEN_KEY) ?? ''; } catch { return ''; } };
