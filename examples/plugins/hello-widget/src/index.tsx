/**
 * Reference plugin for the horrible-dashboard SDK. Contributes one dashboard
 * widget (a counter persisted through the plugin storage service), one command,
 * and one user setting (the greeting text) read live from the settings page —
 * demonstrating both `host.storage` (bookkeeping) and `host.settings`
 * (user-configurable). See docs/architecture/plugin-sdk.md.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';

import { definePlugin, type PluginHost } from '@horribledashboard/sdk';

const COUNT_KEY = 'count';
const GREETING_KEY = 'hello-widget.greetingText';
const DEFAULT_GREETING = 'Hello from a marketplace plugin! 👋';

function makeGreetingWidget(host: PluginHost) {
  return function GreetingWidget() {
    const [count, setCount] = useState<number | null>(null);
    // Re-render whenever any setting changes, then read our greeting.
    const greeting =
      useSyncExternalStore(host.settings.subscribe, () =>
        host.settings.get<string>(GREETING_KEY),
      ) ?? DEFAULT_GREETING;

    useEffect(() => {
      void host.storage.get<number>(COUNT_KEY).then((saved) => setCount(saved ?? 0));
    }, []);

    const bump = async () => {
      const next = (count ?? 0) + 1;
      setCount(next);
      await host.storage.set(COUNT_KEY, next);
    };

    return (
      <div>
        <p>{greeting}</p>
        <p>
          Pressed <strong>{count ?? '…'}</strong> times — persisted server-side via plugin storage.
        </p>
        <button onClick={() => void bump()} disabled={count === null}>
          Press me
        </button>
      </div>
    );
  };
}

export default definePlugin({
  setup(host) {
    return {
      widgets: [
        {
          id: 'hello-widget.greeting',
          title: 'Hello Widget',
          component: makeGreetingWidget(host),
          role: 'widget',
          icon: '👋',
        },
      ],
      commands: [
        {
          id: 'hello-widget.sayHello',
          title: 'Hello Widget: Bump the counter',
          run: async () => {
            const current = (await host.storage.get<number>(COUNT_KEY)) ?? 0;
            await host.storage.set(COUNT_KEY, current + 1);
          },
        },
      ],
      settings: [
        {
          key: GREETING_KEY,
          title: 'Greeting text',
          description: 'The message the Hello Widget shows on the dashboard.',
          type: 'string',
          default: DEFAULT_GREETING,
        },
      ],
    };
  },
});
