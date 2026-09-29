/**
 * Typed client for `/api/briefing/*` and the one arXiv call the briefing makes.
 */
import { apiGet, apiPost } from '../../api';

export interface BriefingPaper {
  arxiv_id: string;
  title: string;
  summary: string;
  authors: string[];
  organization: string | null;
  upvotes: number;
  comments: number;
  github_url: string | null;
  github_stars: number | null;
  thumbnail: string | null;
  /** ISO timestamp of when it was submitted to the daily list. */
  submitted_at: string;
}

export interface BriefingStory {
  id: string;
  title: string;
  url: string;
  domain: string;
  points: number;
  comments: number;
  /** Epoch seconds. */
  created_at: number;
  discussion_url: string;
}

interface PapersResponse {
  papers: BriefingPaper[];
  fetched_at: number;
  stale: boolean;
}

interface NewsResponse {
  stories: BriefingStory[];
  fetched_at: number;
  stale: boolean;
}

export function fetchPapers(refresh = false): Promise<PapersResponse> {
  return apiGet<PapersResponse>(`/briefing/papers${refresh ? '?refresh=true' : ''}`);
}

export function fetchNews(refresh = false): Promise<NewsResponse> {
  return apiGet<NewsResponse>(`/briefing/news${refresh ? '?refresh=true' : ''}`);
}

export function arxivAbsUrl(id: string): string {
  return `https://arxiv.org/abs/${id}`;
}

export function arxivPdfUrl(id: string): string {
  return `https://arxiv.org/pdf/${id}`;
}

export function hfPaperUrl(id: string): string {
  return `https://huggingface.co/papers/${id}`;
}

/**
 * File a briefing paper into the library through the arXiv module's own route —
 * the guarded PDF pipeline and the paper's real metadata, not a second download
 * path. Tagged `briefing` so the library can say where it came from.
 */
export function saveBriefingPaper(arxivId: string): Promise<unknown> {
  return apiPost('/arxiv/download', { arxiv_id: arxivId, tags: ['briefing'] });
}
