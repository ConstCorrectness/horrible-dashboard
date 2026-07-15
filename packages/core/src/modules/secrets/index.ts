import { type ModuleManifest } from '../../registry';
import { SecretsSettings } from './SecretsSettings';

export const secretsModule: ModuleManifest = {
  id: 'secrets',
  title: 'Secrets',
  settingsSections: [
    { id: 'secrets.providers', title: 'External Providers', component: SecretsSettings },
  ],
};
