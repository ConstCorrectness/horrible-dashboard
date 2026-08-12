import type { ModuleManifest } from '../../registry';
import { MachineSection } from './MachineSection';

/**
 * Hardware: one capability probe driving defaults everywhere.
 *
 * No pane of its own — the probe is not a place you go, it is a fact the rest of
 * the app reads. It contributes a settings section (what was found, and why each
 * default was chosen) plus the three overrides that let a user correct it, which
 * matters because vendor tools are not always installed and a container can hide
 * a card the user knows is there. See docs/modules/hardware.mdx.
 */
export const hardwareModule: ModuleManifest = {
  id: 'hardware',
  title: 'Hardware',
  settings: [
    {
      key: 'hardware.accelerator',
      title: 'Accelerator',
      description:
        'Override what the probe found. "auto" uses the detected GPU (falling back to CPU when nothing could be determined); "none" pins everything to the CPU. Choosing a specific one is an assertion, not a measurement — the app labels it as yours and a build that cannot load its runtime will fail at spawn. Re-probe from the section above for a change to take effect.',
      type: 'enum',
      enumValues: ['auto', 'none', 'cuda', 'rocm', 'metal', 'vulkan'],
      default: 'auto',
    },
    {
      key: 'hardware.vramMb',
      title: 'Accelerator memory (MB)',
      description:
        'Memory of the overridden accelerator, used to decide whether to offload layers. 0 leaves it unknown, which keeps layers on the CPU rather than guessing how much fits. Ignored unless the accelerator above is overridden.',
      type: 'number',
      default: 0,
    },
    {
      key: 'hardware.localTraining',
      title: 'Offer local training',
      description:
        'Whether this machine is presented as capable of local fine-tuning. "auto" says yes only with 6 GB or more of accelerator memory; the Kaggle and Colab push paths exist precisely because most machines are under that. This never blocks a run — it decides what is recommended.',
      type: 'enum',
      enumValues: ['auto', 'on', 'off'],
      default: 'auto',
    },
  ],
  // The reading itself cannot be a form control: it has three states (found /
  // looked and found nothing / could not ask) and every derived default carries
  // a reason string.
  settingsSections: [{ id: 'hardware.machine', title: 'Hardware', component: MachineSection }],
};

export * from './api';
