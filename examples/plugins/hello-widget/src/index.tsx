/**
 * Reference plugin for the horrible-dashboard SDK. Contributes one dashboard
 * widget (a counter persisted through the plugin storage service) and one
 * command. See docs/architecture/plugin-sdk.md for the authoring guide.
 */
import { useEffect, useState } from 'react';

import { definePlugin, type PluginHost } from '@horrible/sdk';

const COUNT_KEY = 'count';

function makeGreetingWidget(host: PluginHost) {
  return function GreetingWidget() {
    const [count, setCount] = useState<number | null>(null);

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
        <p>Hello from a marketplace plugin! 👋</p>
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
    };
  },
});
