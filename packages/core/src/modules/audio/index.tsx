/**
 * Audio: default input, default output, and a routing matrix over both.
 *
 * Every module that makes a sound registers a **strip**; every output the user
 * picks is a **bus**; a strip can feed several buses at once. That last clause is
 * the feature — "play this video into my microphone *and* my headphones, but
 * keep my voice out of my headphones" is two rows and two columns, and is
 * unexpressible in a single default-output setting.
 *
 * The two settings here are deliberately the *only* two: a default input and a
 * default output are what someone wants before they have thought about routing,
 * and the matrix is what they want afterwards. The matrix is not a setting —
 * `SettingValue` is a scalar, and a routing table encoded into a string would be
 * a schema hiding inside a value that `GET /api/settings` hands to every plugin.
 *
 * See docs/modules/audio.mdx.
 */
import './audio.css';

import { registry, type ModuleManifest } from '../../registry';
import { AudioMixerPanel } from './panels/MixerPanel';
import { AudioDevicesSection } from './settings/DevicesSection';
import { connectAudio, ensureLoaded } from './store';

export const audioModule: ModuleManifest = {
  id: 'audio',
  title: 'Audio',
  panels: [
    {
      id: 'audio.mixer',
      title: 'Audio mixer',
      component: AudioMixerPanel,
      role: 'tool',
      icon: '🎚️',
      defaultDock: 'right',
      defaultDockSize: 420,
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'audio.openMixer',
      title: 'Audio: Open mixer',
      run: () => {
        connectAudio();
        void ensureLoaded();
        registry.openPanel('audio.mixer');
      },
    },
  ],
  settings: [
    {
      key: 'audio.startOnBoot',
      title: 'Start the mixer at launch',
      description:
        'Load the saved routing as soon as the app opens, rather than when a mixer pane is first shown. Leave this on if you route audio to somewhere other than your default output — with it off, sound plays to the default device until you open the mixer.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'audio.showVirtualHint',
      title: 'Suggest a virtual audio device',
      description:
        'Show a note when routing audio to another application would need a virtual audio device that is not installed. Turn this off if you have no interest in routing audio out of the dashboard.',
      type: 'boolean',
      default: true,
    },
  ],
  // The device picture cannot be a form control: it has the three states the
  // hardware probe has (found / looked and found nothing / could not ask), and
  // the per-platform answer about virtual devices is a paragraph, not a value.
  settingsSections: [
    { id: 'audio.devices', title: 'Audio devices', component: AudioDevicesSection },
  ],
};

export { mixer, dbToGain } from './engine';
export { connectAudio, ensureLoaded, getState, subscribeMixer } from './store';
export * from './types';
export * from './api';
