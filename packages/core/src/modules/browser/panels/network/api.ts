/** Client for the browser module's active network probes (`/api/browser/net/*`). */
import { apiGet, apiPost } from '../../../../api';

export interface DnsHop {
  level: 'root' | 'tld' | 'authoritative' | 'answer';
  zone: string;
  server: string;
  server_name: string;
  query: string;
  referral: string[];
  referral_zone: string;
  /** Addresses shipped with a referral, without which in-zone NS lookups loop. */
  glue: Record<string, string[]>;
  answers: string[];
  /** An alias answer — resolution continues at this name, usually in another zone. */
  cname: string;
  /** The parent published a DS record: this link of the chain of trust is signed. */
  signed: boolean;
  rtt_ms?: number | null;
  error?: string | null;
}

export interface DnsChain {
  name: string;
  record_type: string;
  hops: DnsHop[];
  addresses: string[];
  dnssec: boolean;
  elapsed_ms: number;
  notes: string[];
}

export interface GeoPoint {
  ip: string;
  lat: number;
  lon: number;
  city?: string | null;
  country?: string | null;
}

export interface TraceHop {
  ttl: number;
  host: string;
  ip: string;
  rtt_ms: number[];
  timeout: boolean;
  geo?: GeoPoint | null;
}

export interface TraceResult {
  host: string;
  hops: TraceHop[];
  elapsed_ms: number;
  error?: string | null;
}

export interface GeoStatus {
  available: boolean;
  path: string;
  attribution: string;
  hint: string;
}

export function probeDns(target: string, recordType = 'A'): Promise<DnsChain> {
  return apiPost<DnsChain>('/browser/net/dns', { target, record_type: recordType });
}

export function probeTrace(target: string): Promise<TraceResult> {
  return apiPost<TraceResult>('/browser/net/trace', { target });
}

export function geoStatus(): Promise<GeoStatus> {
  return apiGet<GeoStatus>('/browser/net/geo');
}
