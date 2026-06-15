import { jsxs as l, jsx as s } from "/plugin-runtime/jsx-runtime.js";
import { useState as u, useSyncExternalStore as m, useEffect as p } from "/plugin-runtime/react.js";
import { definePlugin as f } from "/plugin-runtime/sdk.js";
const n = "count", g = "hello-widget.greetingText", d = "Hello from a marketplace plugin! 👋";
function h(e) {
  return function() {
    const [i, o] = u(null), c = m(
      e.settings.subscribe,
      () => e.settings.get(g)
    ) ?? d;
    p(() => {
      e.storage.get(n).then((t) => o(t ?? 0));
    }, []);
    const a = async () => {
      const t = (i ?? 0) + 1;
      o(t), await e.storage.set(n, t);
    };
    return /* @__PURE__ */ l("div", { children: [
      /* @__PURE__ */ s("p", { children: c }),
      /* @__PURE__ */ l("p", { children: [
        "Pressed ",
        /* @__PURE__ */ s("strong", { children: i ?? "…" }),
        " times — persisted server-side via plugin storage."
      ] }),
      /* @__PURE__ */ s("button", { onClick: () => {
        a();
      }, disabled: i === null, children: "Press me" })
    ] });
  };
}
const G = f({
  setup(e) {
    return {
      widgets: [
        {
          id: "hello-widget.greeting",
          title: "Hello Widget",
          component: h(e)
        }
      ],
      commands: [
        {
          id: "hello-widget.sayHello",
          title: "Hello Widget: Bump the counter",
          run: async () => {
            const r = await e.storage.get(n) ?? 0;
            await e.storage.set(n, r + 1);
          }
        }
      ],
      settings: [
        {
          key: g,
          title: "Greeting text",
          description: "The message the Hello Widget shows on the dashboard.",
          type: "string",
          default: d
        }
      ]
    };
  }
});
export {
  G as default
};
