import { jsxs as l, jsx as r } from "/plugin-runtime/jsx-runtime.js";
import { useState as a, useEffect as c } from "/plugin-runtime/react.js";
import { definePlugin as u } from "/plugin-runtime/sdk.js";
const n = "count";
function g(e) {
  return function() {
    const [i, s] = a(null);
    c(() => {
      e.storage.get(n).then((t) => s(t ?? 0));
    }, []);
    const d = async () => {
      const t = (i ?? 0) + 1;
      s(t), await e.storage.set(n, t);
    };
    return /* @__PURE__ */ l("div", { children: [
      /* @__PURE__ */ r("p", { children: "Hello from a marketplace plugin! 👋" }),
      /* @__PURE__ */ l("p", { children: [
        "Pressed ",
        /* @__PURE__ */ r("strong", { children: i ?? "…" }),
        " times — persisted server-side via plugin storage."
      ] }),
      /* @__PURE__ */ r("button", { onClick: () => {
        d();
      }, disabled: i === null, children: "Press me" })
    ] });
  };
}
const h = u({
  setup(e) {
    return {
      widgets: [
        {
          id: "hello-widget.greeting",
          title: "Hello Widget",
          component: g(e)
        }
      ],
      commands: [
        {
          id: "hello-widget.sayHello",
          title: "Hello Widget: Bump the counter",
          run: async () => {
            const o = await e.storage.get(n) ?? 0;
            await e.storage.set(n, o + 1);
          }
        }
      ]
    };
  }
});
export {
  h as default
};
