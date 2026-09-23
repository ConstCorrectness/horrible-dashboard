/**
 * The Python reference: browse the standard library, every installed package, and
 * the dashboard's own SDKs — module by module, at the versions actually installed.
 *
 * Until this pane, documentation arrived only by hovering a symbol already in your
 * code. That answers "what does this do" and never "what is there": nobody can hover
 * their way to `torch.nn.utils.clip_grad_norm_` without knowing it exists. The data
 * was all indexed (`code_symbols`, the same rows completion reads); what was missing
 * was a way in.
 *
 * Three columns, a reference browser's shape: *where* (corpus → package → module),
 * *what* (the module's classes and functions, each class opened onto its methods),
 * and *the page* (signature, the **whole** docstring — the index keeps only its
 * first paragraph, so the page asks the server to read the rest from the defining
 * file — an import line, and a link upstream pinned to the installed version).
 */
import './reference.css';

import { useCallback, useEffect, useMemo, useState } from 'react';

import { openExternal } from '../../external';
import {
  buildReferenceIndex,
  getReferenceDoc,
  getReferenceMembers,
  getReferenceModules,
  getReferenceSources,
  searchReference,
  subscribeReferenceQuery,
  takeReferenceQuery,
  type Corpus,
  type ReferenceCorpus,
  type ReferenceDoc,
  type ReferenceHit,
  type ReferenceMember,
  type ReferenceSources,
} from './reference-api';

const CORPUS_LABEL: Record<Corpus, string> = {
  pkg: 'Installed packages',
  std: 'Standard library',
  sdk: 'Dashboard SDKs',
};

/** The packages a research notebook lives in, pinned above the alphabetical rest. */
const FEATURED = new Set([
  'torch',
  'transformers',
  'datasets',
  'peft',
  'trl',
  'accelerate',
  'numpy',
  'pandas',
  'safetensors',
  'tokenizers',
]);

interface Target {
  source: string;
  module: string;
  name: string;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function ReferencePane() {
  const [sources, setSources] = useState<ReferenceSources | null>(null);
  const [error, setError] = useState('');
  const [corpus, setCorpus] = useState<Corpus>('pkg');
  const [source, setSource] = useState('pkg:torch');
  const [modules, setModules] = useState<{ module: string; count: number }[]>([]);
  const [moduleFilter, setModuleFilter] = useState('');
  const [module, setModule] = useState('');
  const [members, setMembers] = useState<ReferenceMember[]>([]);
  const [query, setQuery] = useState(() => takeReferenceQuery() ?? '');
  const [hits, setHits] = useState<ReferenceHit[] | null>(null);
  const [target, setTarget] = useState<Target | null>(null);
  const [doc, setDoc] = useState<ReferenceDoc | null>(null);
  const [status, setStatus] = useState('');

  const load = useCallback(() => {
    void getReferenceSources()
      .then(setSources)
      .catch((err: unknown) => setError(errorText(err)));
  }, []);
  useEffect(load, [load]);
  useEffect(() => subscribeReferenceQuery(setQuery), []);

  const byCorpus = useMemo(() => {
    const list = (sources?.corpora ?? []).filter((c) => c.corpus === corpus);
    return list.sort((a, b) => {
      const fa = FEATURED.has(a.name) ? 0 : 1;
      const fb = FEATURED.has(b.name) ? 0 : 1;
      return fa - fb || a.name.localeCompare(b.name);
    });
  }, [sources, corpus]);

  // Keep the selected source inside the corpus being shown.
  useEffect(() => {
    if (!byCorpus.length) return;
    if (!byCorpus.some((c) => c.source === source)) setSource(byCorpus[0].source);
  }, [byCorpus, source]);

  useEffect(() => {
    if (!source) return;
    let alive = true;
    setModules([]);
    setModule('');
    setModuleFilter('');
    void getReferenceModules(source)
      .then((list) => {
        if (!alive) return;
        setModules(list);
        setModule(list[0]?.module ?? '');
      })
      .catch((err: unknown) => alive && setStatus(errorText(err)));
    return () => {
      alive = false;
    };
  }, [source]);

  useEffect(() => {
    if (!module) return;
    let alive = true;
    void getReferenceMembers(source, module)
      .then((list) => alive && setMembers(list))
      .catch((err: unknown) => alive && setStatus(errorText(err)));
    return () => {
      alive = false;
    };
  }, [source, module]);

  // Search replaces the first two columns while it has text.
  useEffect(() => {
    const text = query.trim();
    if (!text) {
      setHits(null);
      return;
    }
    let alive = true;
    const timer = window.setTimeout(() => {
      void searchReference(text)
        .then((list) => alive && setHits(list))
        .catch((err: unknown) => alive && setStatus(errorText(err)));
    }, 160);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [query]);

  useEffect(() => {
    if (!target) return;
    let alive = true;
    setDoc(null);
    void getReferenceDoc(target.source, target.module, target.name)
      .then((d) => alive && setDoc(d))
      .catch((err: unknown) => alive && setStatus(errorText(err)));
    return () => {
      alive = false;
    };
  }, [target]);

  const corpusOf = (s: string): ReferenceCorpus | undefined =>
    sources?.corpora.find((c) => c.source === s);

  if (error) return <p className="ref-note">{error}</p>;
  if (!sources) return <p className="ref-dim ref-pad">Reading the index…</p>;
  if (sources.empty)
    return (
      <div className="ref-empty">
        <h3 className="ref-h">Nothing indexed yet</h3>
        <p className="ref-dim">
          The reference browses the same symbol index editor completion reads. Build it from{' '}
          <code>{sources.interpreter || 'the resolved Python'}</code> — a few minutes, once.
        </p>
        <button
          onClick={() =>
            void buildReferenceIndex().then((r) =>
              setStatus(r.started ? 'Building… reopen in a minute.' : (r.reason ?? 'Not started')),
            )
          }
        >
          Build the index
        </button>
        {status && <p className="ref-dim">{status}</p>}
      </div>
    );

  const shownModules = moduleFilter
    ? modules.filter((m) => m.module.toLowerCase().includes(moduleFilter.toLowerCase()))
    : modules;

  return (
    <div className="ref-root">
      <div className="ref-bar">
        <input
          type="search"
          className="ref-search"
          placeholder="Search every symbol — torch.nn.Lin, clip_grad, json.dumps"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search the Python reference"
        />
        <div className="ref-tabs" role="tablist" aria-label="Corpus">
          {(['pkg', 'std', 'sdk'] as Corpus[]).map((c) => (
            <button
              key={c}
              role="tab"
              aria-selected={corpus === c}
              className={`ref-tab${corpus === c ? ' ref-tab-on' : ''}`}
              onClick={() => {
                setCorpus(c);
                setQuery('');
              }}
            >
              {CORPUS_LABEL[c]}
            </button>
          ))}
        </div>
        <span className="ref-dim ref-mono" title={sources.interpreter}>
          Python {sources.python}
        </span>
      </div>

      <div className="ref-cols">
        {hits ? (
          <section className="ref-col ref-col-wide" aria-label="Search results">
            <h4 className="ref-h">
              {hits.length} match{hits.length === 1 ? '' : 'es'}
            </h4>
            <ul className="ref-list">
              {hits.map((h, i) => (
                <li key={`${h.source}:${h.module}:${h.name}:${i}`}>
                  <button
                    className="ref-item"
                    style={{ animationDelay: `${Math.min(i, 10) * 16}ms` }}
                    onClick={() => setTarget({ source: h.source, module: h.module, name: h.name })}
                  >
                    <span className={`ref-kind ref-kind-${h.kind}`}>{h.kind.slice(0, 1)}</span>
                    <span className="ref-mono">{h.name}</span>
                    <span className="ref-dim ref-mono ref-trail">{h.module}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : (
          <>
            <section className="ref-col" aria-label="Packages and modules">
              <select
                aria-label="Package"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                style={{ padding: '0 0.5rem' }}
              >
                {byCorpus.map((c) => (
                  <option key={c.source} value={c.source}>
                    {c.name}
                    {c.version ? ` ${c.version}` : ''}
                  </option>
                ))}
              </select>
              <input
                type="text"
                placeholder="Filter modules"
                value={moduleFilter}
                onChange={(e) => setModuleFilter(e.target.value)}
                aria-label="Filter modules"
                style={{ padding: '0 0.5rem' }}
              />
              <ul className="ref-list">
                {shownModules.map((m) => (
                  <li key={m.module}>
                    <button
                      className={`ref-item${m.module === module ? ' ref-item-on' : ''}`}
                      onClick={() => setModule(m.module)}
                    >
                      <span className="ref-mono">{m.module}</span>
                      <span className="ref-dim ref-trail">{m.count}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
            <section className="ref-col" aria-label={`Members of ${module}`}>
              <h4 className="ref-h ref-mono">{module || '—'}</h4>
              <ul className="ref-list">
                {members.map((m) => (
                  <MemberRow
                    key={m.name}
                    member={m}
                    selected={target}
                    onPick={(name) => setTarget({ source, module, name })}
                    source={source}
                    module={module}
                  />
                ))}
              </ul>
            </section>
          </>
        )}

        <article className="ref-page" aria-label="Documentation">
          {!target ? (
            <p className="ref-dim">
              Pick a symbol. The page shows its whole docstring (the index keeps only the first
              paragraph), how to import it, and upstream docs for the version you have installed.
            </p>
          ) : !doc ? (
            <p className="ref-dim">Reading {target.name}…</p>
          ) : (
            <DocPage doc={doc} corpus={corpusOf(doc.source)} />
          )}
        </article>
      </div>
      {status && <p className="ref-note">{status}</p>}
    </div>
  );
}

function MemberRow({
  member,
  selected,
  onPick,
  source,
  module,
}: {
  member: ReferenceMember;
  selected: Target | null;
  onPick: (name: string) => void;
  source: string;
  module: string;
}) {
  const [open, setOpen] = useState(false);
  const isOn = (name: string) =>
    selected?.source === source && selected.module === module && selected.name === name;
  return (
    <li>
      <button
        className={`ref-item${isOn(member.name) ? ' ref-item-on' : ''}`}
        onClick={() => {
          onPick(member.name);
          if (member.members?.length) setOpen(true);
        }}
        title={member.doc}
      >
        <span className={`ref-kind ref-kind-${member.kind}`}>{member.kind.slice(0, 1)}</span>
        <span className="ref-mono">{member.name}</span>
        {member.members?.length ? (
          <span
            className="ref-dim ref-trail"
            onClick={(e) => {
              e.stopPropagation();
              setOpen((v) => !v);
            }}
          >
            {open ? '−' : '+'}
            {member.members.length}
          </span>
        ) : null}
      </button>
      {open && member.members && (
        <ul className="ref-list ref-sublist">
          {member.members.map((m) => {
            const name = `${member.name}.${m.name}`;
            return (
              <li key={name}>
                <button
                  className={`ref-item${isOn(name) ? ' ref-item-on' : ''}`}
                  onClick={() => onPick(name)}
                  title={m.doc}
                >
                  <span className="ref-kind ref-kind-method">m</span>
                  <span className="ref-mono">{m.name}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}

function DocPage({ doc, corpus }: { doc: ReferenceDoc; corpus: ReferenceCorpus | undefined }) {
  const [note, setNote] = useState('');
  return (
    <div className="ref-doc">
      <div className="ref-doc-head">
        <span className="ref-dim ref-mono">{doc.module}</span>
        <h3 className="ref-title ref-mono">{doc.name}</h3>
        {corpus?.version && (
          <span className="ref-chip">
            {corpus.name} {corpus.version}
          </span>
        )}
      </div>
      {doc.definedIn && (
        <p className="ref-dim">
          Re-exported here; defined in <span className="ref-mono">{doc.definedIn}</span>.
        </p>
      )}
      {doc.signature && (
        <pre className="ref-sig">
          <span className="ref-sig-name">{doc.name.split('.').pop()}</span>
          {doc.signature}
        </pre>
      )}
      <div className="ref-actions">
        {doc.importLine && (
          <button
            className="btn-mini"
            onClick={() =>
              void navigator.clipboard
                .writeText(doc.importLine)
                .then(() => setNote('Import copied'))
                .catch(() => setNote(doc.importLine))
            }
          >
            Copy import
          </button>
        )}
        {doc.upstream && (
          <button
            className="btn-mini"
            title={doc.upstream.url}
            onClick={() =>
              void openExternal(doc.upstream!.url).then((ok) =>
                setNote(ok ? '' : `Could not open a browser — ${doc.upstream!.url}`),
              )
            }
          >
            {doc.upstream.label}
          </button>
        )}
        {note && <span className="ref-dim">{note}</span>}
      </div>
      {doc.importLine && <code className="ref-import">{doc.importLine}</code>}
      {doc.doc ? (
        <div className="ref-body">{doc.doc}</div>
      ) : (
        <p className="ref-dim">
          No docstring found. Names implemented in a compiled extension carry theirs only at
          runtime, and the index reads source without ever importing the package.
        </p>
      )}
      {doc.file && (
        <p className="ref-dim ref-mono ref-file" title={doc.file}>
          {doc.file}
          {doc.line ? `:${doc.line}` : ''}
        </p>
      )}
    </div>
  );
}
