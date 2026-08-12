import { apiGet, apiPost } from '../../api';

/** One GPU the machine reports. */
export interface Accelerator {
  /** cuda | rocm | metal | vulkan */
  kind: string;
  name: string;
  /**
   * Null when the device reports no memory size — `vulkaninfo --summary` does
   * not, and inventing one would be inventing the number the offload decision
   * keys off.
   */
  vramMb: number | null;
  /** Apple silicon: this "VRAM" is the machine's RAM, shared with the CPU. */
  unified: boolean;
  /** Whether `vramMb` was measured. False for unified memory and for overrides. */
  exact: boolean;
  detectedBy: string;
}

/** Something the probe could not determine, and why. */
export interface ProbeNote {
  kind: string;
  reason: string;
}

export interface HardwareProfile {
  os: string;
  arch: string;
  cpuCount: number;
  ramMb: number | null;
  ramExact: boolean;
  accelerators: Accelerator[];
  notes: ProbeNote[];
  probedAt: number;
  /** True when the user overrode the probe in settings. */
  overridden: boolean;
  /**
   * False when an empty accelerator list is a gap rather than a finding — i.e.
   * `nvidia-smi` was not on PATH. Render "unknown", never "none".
   */
  certain: boolean;
  primary: Accelerator | null;
}

export interface HardwareDefaults {
  llamaVariant: string;
  gpuLayers: number;
  threads: number;
  traceTokenCap: number;
  localTraining: boolean;
  /** Why each default was chosen, keyed by field name. */
  reasons: Record<string, string>;
}

export interface Hardware {
  profile: HardwareProfile;
  defaults: HardwareDefaults;
}

export const getHardware = () => apiGet<Hardware>('/hardware');

/** Re-run the probe. Also how a settings override takes effect. */
export const refreshHardware = () => apiPost<Hardware>('/hardware/refresh', {});
