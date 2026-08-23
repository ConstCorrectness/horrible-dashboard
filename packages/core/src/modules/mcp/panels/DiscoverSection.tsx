import { useCallback, useEffect, useState } from 'react';

import { DataList, DataRow } from '../../../DataList';
import { Button, Chip, EmptyState, Field } from '../../../Primitives';
import {
  Caption,
  CodeChip,
  ControlBar,
  ControlRow,
  ResourceCard,
  ResourceCardList,
  Stack,
  type CardTag,
} from '../../../ResourceCard';
import {
  discoverServers,
  probeServer,
  saveServer,
  toServerInput,
  type McpCatalogEntry,
  type McpInstallOption,
  type McpProbe,
} from '../api';

/**
 * Find a server, look inside it, then decide.
 *
 * The order is the point. A registry entry's description is marketing written by its
 * publisher; **Inspect** connects the real thing once, in a scratch session that is
 * never saved and never registers an agent tool, and shows its actual tools, its
 * `readOnlyHint` annotations and its own instructions. Adding a server used to mean
 * committing to it and finding out afterwards.
 *
 * Two things are deliberately loud. Inspecting a package option *runs third-party code
 * on this machine* — the same act as adding it, minus the persistence — so it says so.
 * And a secret an entry declares is collected into a separate field that never reaches
 * the config file, because `env` is persisted in the clear.
 *
 * The layout is `ResourceCard`'s and nothing here styles itself. This feed is where
 * that primitive came from: a catalog entry is an identity, a publisher's sentence, a
 * command you want to read before you commit, two or three inputs and a pair of
 * buttons — and it had been drawing all of that with 32 inline `style={{}}` objects,
 * each picking its own font size and gap. See `ResourceCard.tsx`.
 */

/**
 * The header tags for one entry.
 *
 * `curated` and a version are different *kinds* of fact and must not look alike:
 * one is this node vouching for the entry, the other is a number the publisher
 * chose. So the first is a verdict chip and the second is neutral.
 */
function tagsFor(entry: McpCatalogEntry): CardTag[] {
  const tags: CardTag[] = [];
  if (entry.source === 'curated') {
    tags.push({
      label: 'curated',
      kind: 'ok',
      dot: true,
      title: 'Shipped with this node and checked, rather than fetched from the registry.',
    });
  }
  if (entry.version) tags.push({ label: entry.version, title: 'Version the registry reports.' });
  return tags;
}

/**
 * What an install option resolves to on this machine.
 *
 * Split into the runner and the rest, because those are two different facts: the
 * lead word is whether this node will *execute a package* or *call a URL*, which
 * is the whole of what you are deciding when you press Inspect, and it is set in
 * the accent so it answers that before the line is read.
 */
function describe(option: McpInstallOption): { lead: string; rest: string; full: string } {
  const parts =
    option.kind === 'remote'
      ? { lead: option.transport, rest: option.url }
      : { lead: option.command, rest: option.args.join(' ') };
  return { ...parts, full: `${parts.lead} ${parts.rest}`.trim() };
}

/**
 * The action glyphs.
 *
 * Vector strokes inheriting `currentColor`, never an emoji — the button already
 * decides the colour, and an emoji is a third-party font's opinion about it that
 * also changes size between platforms.
 */
const iconProps = {
  width: 11,
  height: 11,
  viewBox: '0 0 12 12',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

const InspectIcon = () => (
  <svg {...iconProps}>
    <circle cx="5" cy="5" r="3.2" />
    <path d="M7.4 7.4 10.5 10.5" />
  </svg>
);

const AddIcon = () => (
  <svg {...iconProps}>
    <path d="M6 1.8v8.4M1.8 6h8.4" />
  </svg>
);

/**
 * What Inspect found.
 *
 * The tool list is a `DataList` rather than a `<ul>` so a tool's read-only
 * annotation is drawn with the same verdict vocabulary as everything else in the
 * app — and `readOnly` is genuinely a verdict here, since it decides whether the
 * agent may call the tool without asking.
 */
function ProbeResult({ probe }: { probe: McpProbe }) {
  if (!probe.ok) {
    return (
      <>
        <Caption tone="danger">{probe.error}</Caption>
        {probe.messages.length > 0 && (
          <Caption>
            {probe.messages.length} wire messages before it failed — the last was{' '}
            <CodeChip>{probe.messages[probe.messages.length - 1]?.method}</CodeChip>.
          </Caption>
        )}
      </>
    );
  }
  return (
    <>
      <ControlBar>
        <Chip kind="ok">connected</Chip>
        <CodeChip>
          {probe.serverName} {probe.serverVersion}
        </CodeChip>
        <Caption>
          {probe.tools.length} tools · {probe.prompts.length} prompts · {probe.resources.length}{' '}
          resources
        </Caption>
      </ControlBar>
      {probe.instructions && (
        <Caption>
          {probe.instructions.slice(0, 300)}
          {probe.instructions.length > 300 ? '…' : ''}
        </Caption>
      )}
      {probe.tools.length > 0 && (
        <DataList label="Tools this server exposes">
          {probe.tools.map((t, i) => (
            <DataRow
              key={t.name}
              index={i}
              // A gated tool is not a failure, so it is `warn` rather than `fail`:
              // it means "the agent must ask first", which is a state, not a fault.
              kind={t.readOnly ? 'ok' : 'warn'}
              title={t.name}
              badge={t.readOnly ? 'read-only' : 'gated'}
            >
              {t.description.slice(0, 120)}
            </DataRow>
          ))}
        </DataList>
      )}
    </>
  );
}

function EntryCard({
  entry,
  index,
  onAdded,
}: {
  entry: McpCatalogEntry;
  index: number;
  onAdded: () => void;
}) {
  const [choice, setChoice] = useState(0);
  const [id, setId] = useState(entry.suggestedId);
  const [extraArgs, setExtraArgs] = useState('');
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [probe, setProbe] = useState<McpProbe | null>(null);
  const [busy, setBusy] = useState<'' | 'probe' | 'add'>('');
  const [error, setError] = useState<string | null>(null);

  const option: McpInstallOption | undefined = entry.installs[choice];

  const input = useCallback(() => {
    if (!option) return null;
    const base = toServerInput(entry, option, { id });
    return {
      ...base,
      // Whitespace-split, the same as the manual form: this is what a user pastes
      // from a README, and the filesystem server's allowed directories arrive here.
      args: [...(base.args ?? []), ...(extraArgs.trim() ? extraArgs.trim().split(/\s+/) : [])],
      secretEnvValues: secrets,
    };
  }, [entry, option, id, extraArgs, secrets]);

  const run = async (kind: 'probe' | 'add') => {
    const payload = input();
    if (!payload) return;
    setBusy(kind);
    setError(null);
    try {
      if (kind === 'probe') {
        setProbe(await probeServer(payload));
      } else {
        await saveServer(payload);
        onAdded();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  };

  // The env vars that are NOT secret. They are read-only information — the values
  // are set in the server's own environment after adding — so they belong with the
  // prose rather than among the controls, where an unfillable field would read as
  // one the form had failed to render.
  const plainEnv = option?.env.filter((v) => !v.secret) ?? [];
  const secretEnv = option?.env.filter((v) => v.secret) ?? [];
  const blocked = option?.unsupported;

  return (
    <ResourceCard
      index={index}
      title={entry.title}
      identifier={entry.name}
      tags={tagsFor(entry)}
      kind={entry.source === 'curated' ? 'ok' : 'idle'}
      summary={entry.description}
      note={
        entry.installs.length === 0
          ? 'This entry describes no package or remote this node can run.'
          : blocked
            ? `Can’t install from here: ${blocked}`
            : entry.note
      }
      noteTone={blocked ? 'warn' : 'muted'}
      snippet={
        option && (
          <CodeChip lead={describe(option).lead} title={describe(option).full}>
            {describe(option).rest}
          </CodeChip>
        )
      }
      caption={
        option?.kind === 'package' && !blocked
          ? 'Inspect runs this package on your machine — the same act as adding it, without the persistence.'
          : undefined
      }
      actions={
        option &&
        !blocked && (
          <>
            <Button
              size="sm"
              icon={<InspectIcon />}
              disabled={busy !== '' || !id}
              onClick={() => void run('probe')}
            >
              {busy === 'probe' ? 'Connecting…' : 'Inspect'}
            </Button>
            <Button
              size="sm"
              intent="primary"
              icon={<AddIcon />}
              disabled={busy !== '' || !id}
              onClick={() => void run('add')}
            >
              {busy === 'add' ? 'Adding…' : 'Add'}
            </Button>
          </>
        )
      }
      footer={
        (error || probe) && (
          <>
            {error && <Caption tone="danger">{error}</Caption>}
            {probe && <ProbeResult probe={probe} />}
          </>
        )
      }
    >
      {option && !blocked && (
        <>
          <ControlRow>
            {entry.installs.length > 1 && (
              <Field label="Install as" hint="How this node will run it.">
                <select value={choice} onChange={(e) => setChoice(Number(e.target.value))}>
                  {/* Keyed by index, not by label: a registry entry may publish two
                      options with the same label (two "Hosted (http)" remotes), and
                      keying by it made React drop one of them and warn. The index is
                      the identity here anyway — it is what `choice` stores. */}
                  {entry.installs.map((o, i) => (
                    <option key={i} value={i}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Server id" hint="Prefixes this server’s agent tools." required>
              <input
                type="text"
                value={id}
                onChange={(e) => setId(e.target.value)}
                placeholder={entry.suggestedId}
              />
            </Field>
            {option.kind === 'package' && (
              <Field
                span="full"
                label="Extra arguments"
                hint="Appended to the command, split on spaces — a directory to allow, a flag from the README."
              >
                <input
                  type="text"
                  value={extraArgs}
                  onChange={(e) => setExtraArgs(e.target.value)}
                  placeholder="--root /srv/notes"
                />
              </Field>
            )}
          </ControlRow>

          {secretEnv.length > 0 && (
            <ControlRow>
              {secretEnv.map((v) => (
                <Field
                  key={v.name}
                  span="full"
                  label={v.name}
                  required={v.required}
                  // Kept apart from `env` on purpose: `env` is persisted in the
                  // clear, and this value never reaches the config file.
                  hint={v.description || 'Stored encrypted, never written to the config file.'}
                >
                  <input
                    type="password"
                    value={secrets[v.name] ?? ''}
                    onChange={(e) => setSecrets({ ...secrets, [v.name]: e.target.value })}
                    placeholder="•••"
                  />
                </Field>
              ))}
            </ControlRow>
          )}

          {plainEnv.length > 0 && (
            <Caption>
              Set in the server’s environment after adding:{' '}
              {plainEnv.map((v, i) => (
                <span key={v.name}>
                  {i > 0 && ' · '}
                  <CodeChip title={v.description || v.name}>{v.name}</CodeChip>
                </span>
              ))}
            </Caption>
          )}
        </>
      )}
    </ResourceCard>
  );
}

export function DiscoverSection({ onAdded }: { onAdded: () => void }) {
  const [query, setQuery] = useState('');
  const [entries, setEntries] = useState<McpCatalogEntry[]>([]);
  const [online, setOnline] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const search = useCallback(async (q: string) => {
    setLoading(true);
    try {
      const res = await discoverServers(q);
      setEntries(res.entries);
      setOnline(res.registryOnline);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void search('');
  }, [search]);

  return (
    <Stack>
      <ControlBar>
        <Field label="Search the MCP registry">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void search(query);
            }}
            placeholder="filesystem, github, postgres…"
          />
        </Field>
        <Button intent="primary" onClick={() => void search(query)} disabled={loading}>
          {loading ? 'Searching…' : 'Search'}
        </Button>
      </ControlBar>

      {/* A degraded list and an empty one look identical unless you say which. */}
      {!online && !loading && (
        <Caption tone="warn">The registry didn’t answer — showing the shipped list only.</Caption>
      )}
      {error && <Caption tone="danger">{error}</Caption>}

      {!loading && entries.length === 0 && !error && (
        <EmptyState title="Nothing matched">
          Try a broader term, or the name of the tool you want the agent to reach — the registry
          indexes package names, not descriptions.
        </EmptyState>
      )}

      <ResourceCardList label="MCP servers you can add">
        {entries.map((e, i) => (
          <EntryCard key={`${e.source}:${e.name}`} entry={e} index={i} onAdded={onAdded} />
        ))}
      </ResourceCardList>
    </Stack>
  );
}
