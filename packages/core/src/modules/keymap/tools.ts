/**
 * `keymap.*` agent tools — reading and editing the keyboard map on the user's
 * behalf ("I don't like this, make Tab do X instead").
 *
 * Frontend tools, because the resolved keymap and the live context only exist in
 * the browser. `describe` is the important one: it reports *why* a binding is or
 * isn't firing (shadowed, condition false, swallowed by a capturing pane, taken
 * by the host), so the agent can diagnose "this shortcut doesn't work" in one
 * turn instead of guessing. See docs/modules/keymap.mdx.
 */
import type { AgentToolDecl } from '../../registry';
import { registry } from '../../registry';
import { CONTEXT_KEYS, validateWhen } from '../../keymap/context';
import { bindingsFor, explainBinding, type ResolvedBinding } from '../../keymap/resolve';
import { checkReserved } from '../../keymap/reserved';
import { labelSpec, tryParseSpec } from '../../keymap/spec';
import { getKeymap, readKeyContext } from '../../keymap/state';
import { resetAllKeybindings, resetKeybindings, setKeybinding } from '../../keymap/overrides';

function describeBinding(binding: ResolvedBinding) {
  const ctx = readKeyContext();
  const chord = tryParseSpec(binding.key);
  const reserved = chord ? checkReserved(chord, ctx) : null;
  const why = explainBinding(binding, getKeymap(), ctx);
  return {
    key: binding.key,
    label: chord ? labelSpec(chord, { platform: ctx.platform }) : binding.key,
    command: binding.command,
    when: binding.when ?? null,
    source: binding.source,
    status: why.reason,
    // Populated only when the binding lost, so the agent can say *why*.
    shadowedBy: why.reason === 'shadowed' ? why.by.command : undefined,
    capturedBy: why.reason === 'captured' ? why.by : undefined,
    reserved: reserved ? { owner: reserved.owner, usable: reserved.preventable } : undefined,
  };
}

export const keymapAgentTools: AgentToolDecl[] = [
  {
    name: 'keymap.list',
    description:
      'List keyboard shortcuts. Returns each command with its bindings, the ' +
      'condition each is active under, whether it comes from defaults or the ' +
      "user's customizations, and whether the host (browser/OS) takes the chord " +
      'before the app can see it. Filter by a substring of the command id, title, ' +
      'or key.',
    params: {
      type: 'object',
      properties: {
        filter: { type: 'string', description: 'Substring of a command id, title, or key.' },
        source: {
          type: 'string',
          enum: ['all', 'user', 'default'],
          description: "Only bindings from this source. Defaults to 'all'.",
        },
      },
    },
    handler: (args) => {
      const filter = String(args.filter ?? '').toLowerCase();
      const source = String(args.source ?? 'all');
      const keymap = getKeymap();
      const ctx = readKeyContext();
      const rows = registry.commands
        .map((c) => ({
          command: c.id,
          title: c.title,
          bindings: bindingsFor(c.id, keymap, ctx)
            .filter((b) => source === 'all' || b.source === source)
            .map(describeBinding),
        }))
        .filter((r) => (source === 'all' ? true : r.bindings.length > 0))
        .filter(
          (r) =>
            !filter ||
            r.command.toLowerCase().includes(filter) ||
            r.title.toLowerCase().includes(filter) ||
            r.bindings.some((b) => b.key.toLowerCase().includes(filter)),
        );
      return { count: rows.length, commands: rows.slice(0, 120) };
    },
  },
  {
    name: 'keymap.describe',
    description:
      'Explain one key or one command: what a key currently does, or what is ' +
      'bound to a command — including why a binding is NOT firing (a more ' +
      'specific binding shadows it, its condition is false, a focused pane has ' +
      'captured the keyboard, or the browser/OS takes the chord). Use this before ' +
      'keymap.set when the user says a shortcut is wrong or does nothing.',
    params: {
      type: 'object',
      properties: {
        key: { type: 'string', description: "A key spec, e.g. 'tab' or 'mod+k'." },
        command: { type: 'string', description: 'A command id.' },
      },
    },
    handler: (args) => {
      const keymap = getKeymap();
      const ctx = readKeyContext();
      if (args.command) {
        const command = String(args.command);
        return {
          command,
          title: registry.commands.find((c) => c.id === command)?.title ?? null,
          bindings: bindingsFor(command, keymap, ctx).map(describeBinding),
        };
      }
      const spec = String(args.key ?? '');
      const chord = tryParseSpec(spec);
      if (!chord) return { error: `Not a valid key spec: "${spec}"` };
      // Match the whole first stroke, modifiers included. Comparing only the key
      // value would answer "what does mod+1 do?" with alt+1's binding.
      const first = chord[0];
      const key = keymap.filter((b) => {
        const s = b.chord[0];
        return (
          s.mod === first.mod &&
          s.ctrl === first.ctrl &&
          s.meta === first.meta &&
          s.alt === first.alt &&
          s.shift === first.shift &&
          s.value === first.value
        );
      });
      const reserved = checkReserved(chord, ctx);
      return {
        key: spec,
        label: labelSpec(chord, { platform: ctx.platform }),
        reserved: reserved
          ? {
              owner: reserved.owner,
              usable: reserved.preventable,
              note: reserved.preventable
                ? 'The app can take this key, overriding the host.'
                : 'The host never delivers this key to the app — pick another.',
            }
          : null,
        bindings: key.map(describeBinding),
      };
    },
  },
  {
    name: 'keymap.set',
    description:
      'Bind a key to a command, saved as a user customization. Pass `replaces` ' +
      'with the key currently bound to that command when REBINDING, or the old ' +
      'key keeps working alongside the new one. Use a `when` condition to scope ' +
      'the binding — call keymap.context for the vocabulary. Check keymap.describe ' +
      'first so you do not silently take a key another command already owns.',
    params: {
      type: 'object',
      properties: {
        key: {
          type: 'string',
          description:
            "Key spec: 'mod+k' (mod = ctrl/cmd), 'alt+shift+left', a sequence " +
            "'mod+k mod+s', or a physical key 'code:KeyW' for anything positional.",
        },
        command: { type: 'string', description: 'Command id to run.' },
        when: {
          type: 'string',
          description:
            'Optional condition, e.g. "paneFocus == \'editor.buffer\'". Only the ' +
            'context keys from keymap.context are valid.',
        },
        replaceKey: {
          type: 'string',
          description: 'The existing key for this command that the new one replaces.',
        },
      },
      required: ['key', 'command'],
    },
    sideEffect: true,
    specifierTemplate: '{command}',
    handler: async (args) => {
      const key = String(args.key);
      const command = String(args.command);
      const when = args.when ? String(args.when) : undefined;
      if (!registry.commands.some((c) => c.id === command)) {
        return { error: `Unknown command: ${command}. Call keymap.list to find valid ids.` };
      }
      if (when) {
        const check = validateWhen(when);
        if (!check.ok) return { error: check.error };
      }
      const chord = tryParseSpec(key);
      if (!chord) return { error: `Not a valid key spec: "${key}"` };

      const ctx = readKeyContext();
      const reserved = checkReserved(chord, ctx);
      if (reserved && !reserved.preventable) {
        // Refuse rather than write a binding that provably cannot fire — the
        // user would "successfully" rebind and then find nothing happens.
        return {
          error:
            `${key} is taken by ${reserved.owner} on this ${ctx.host}, and never ` +
            `reaches the app. Suggest a different chord.`,
        };
      }
      await setKeybinding({
        key,
        command,
        when,
        replaces: args.replaceKey ? { key: String(args.replaceKey), command } : undefined,
      });
      return {
        ok: true,
        key,
        command,
        when: when ?? null,
        warning: reserved ? `Overrides ${reserved.owner}.` : undefined,
      };
    },
  },
  {
    name: 'keymap.reset',
    description:
      "Remove the user's keybinding customizations, restoring the shipped " +
      'defaults. Give a command id to reset just that one; omit it to reset all.',
    params: {
      type: 'object',
      properties: {
        command: { type: 'string', description: 'Command id. Omit to reset everything.' },
      },
    },
    sideEffect: true,
    specifierTemplate: '{command}',
    handler: async (args) => {
      if (args.command) {
        await resetKeybindings(String(args.command));
        return { ok: true, reset: String(args.command) };
      }
      await resetAllKeybindings();
      return { ok: true, reset: 'all' };
    },
  },
  {
    name: 'keymap.context',
    description:
      'The vocabulary for `when` conditions, and its current values. These are ' +
      'the ONLY keys a condition may use — any other name is rejected.',
    handler: () => ({
      keys: CONTEXT_KEYS,
      operators: ['&&', '||', '!', '==', '!=', '()'],
      current: readKeyContext(),
      examples: [
        "paneFocus == 'editor.buffer'",
        "paneFocus == 'terminal.instance' && !dialogOpen",
        "platform == 'mac'",
      ],
    }),
  },
];
