import { useCallback, useEffect, useState } from 'react';

import { CopyableLink, CopyableValue } from '../../../CopyableLink';
import { DataList, DataRow, type RowKind } from '../../../DataList';
import { IconClose } from '../../../glyphs';
import { Button, Chip } from '../../../Primitives';
import { ShareableLink } from '../../../ShareableLink';
import {
  getPublications,
  preflightPublish,
  publishNotebook,
  saveNotebookHtml,
  unpublishNotebook,
  type Publication,
  type PublishFinding,
  type PublishOut,
  type PublishTarget,
  type PublishVisibility,
} from '../api';

import './publish.css';

/**
 * Publish this notebook somewhere other people can read it.
 *
 * The order on screen is the order of the decision: where, who can see it, what is
 * about to leave the machine, and only then the button. Findings are shown before
 * the button rather than after a failed click, because by the time a secret is on
 * a gist the right move is revoking the key, not unpublishing.
 *
 * The findings list says "found these", never "this is clean". A pattern list does
 * not know every credential format, and a green tick over a notebook with an
 * unrecognised key in it would be the most harmful thing this panel could draw.
 */

/** Visibility, in the words each destination uses for it. */
const VISIBILITY_LABEL: Record<string, Partial<Record<PublishVisibility, string>>> = {
  gist: { secret: 'Secret', public: 'Public' },
  pages: { public: 'Public' },
  kaggle: { secret: 'Private', public: 'Public' },
  colab: { secret: 'Only me', public: 'Anyone with link' },
};

const VISIBILITY_HINT: Record<string, Partial<Record<PublishVisibility, string>>> = {
  gist: {
    secret: 'Unlisted, not private: anyone holding the link can read it.',
    public: 'Listed on your GitHub profile and findable by search.',
  },
  pages: { public: 'A public web page on github.io, in a public repository.' },
  kaggle: {
    secret: 'Only you can see it on Kaggle.',
    public: 'Anyone on Kaggle can find and copy it.',
  },
  colab: {
    secret: 'Only your Google account can open the link.',
    public: 'Anyone holding the link can open it in Colab.',
  },
};

/** What the second link on a publication is. */
const EXTRA_LABEL: Record<string, string> = { gist: 'Gist page', pages: 'nbviewer' };

const FINDING_KIND: Record<PublishFinding['kind'], RowKind> = {
  secret: 'fail',
  path: 'warn',
  traceback: 'info',
  widget: 'idle',
};

function visibilityLabel(target: string, visibility: PublishVisibility): string {
  return VISIBILITY_LABEL[target]?.[visibility] ?? visibility;
}

function Findings({ findings }: { findings: PublishFinding[] | null }) {
  if (findings === null) {
    return <p className="nb-publish__hint">Checking what would be published…</p>;
  }
  if (findings.length === 0) {
    return (
      <p className="nb-publish__hint">
        Nothing matched the checks for secrets, home paths or tracebacks. That is not proof there
        are none — skim it before publishing.
      </p>
    );
  }
  return (
    <DataList label="Before this leaves your machine">
      {findings.map((f, i) => (
        <DataRow
          key={`${f.cell_id}-${f.where}-${f.kind}-${i}`}
          index={i}
          title={f.label}
          kind={FINDING_KIND[f.kind]}
          meta={[`cell ${f.cell + 1}`, f.where, f.blocking ? 'needs confirmation' : 'warning']}
        >
          <code className="nb-publish__excerpt">{f.excerpt}</code>
        </DataRow>
      ))}
    </DataList>
  );
}

export function PublishPanel({ path, onClose }: { path: string; onClose: () => void }) {
  const [targets, setTargets] = useState<PublishTarget[]>([]);
  const [publications, setPublications] = useState<Publication[]>([]);
  const [targetId, setTargetId] = useState('gist');
  const [visibility, setVisibility] = useState<PublishVisibility>('secret');
  const [stripOutputs, setStripOutputs] = useState(false);
  const [findings, setFindings] = useState<PublishFinding[] | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PublishOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [savedHtml, setSavedHtml] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const res = await getPublications(path);
      setTargets(res.targets);
      setPublications(res.publications);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [path]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Re-scan whenever what would be published changes. Stripping outputs changes it.
  useEffect(() => {
    let cancelled = false;
    setFindings(null);
    setConfirming(false);
    preflightPublish(path, stripOutputs)
      .then((res) => {
        if (!cancelled) setFindings(res.findings);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [path, stripOutputs]);

  const target = targets.find((t) => t.id === targetId);

  // A destination offers only the visibilities it can honour — Pages has no
  // "secret" — so snap to one it has rather than sending a choice it will refuse.
  useEffect(() => {
    if (target && !target.visibilities.includes(visibility)) setVisibility(target.visibilities[0]);
  }, [target, visibility]);

  const existing = publications.find((p) => p.target === targetId);
  const blocking = findings?.filter((f) => f.blocking).length ?? 0;

  const publish = useCallback(
    async (acknowledged: boolean) => {
      if (!target) return;
      setBusy(true);
      setError(null);
      setResult(null);
      try {
        const out = await publishNotebook({
          path,
          target: target.id,
          visibility,
          stripOutputs,
          acknowledged,
        });
        setConfirming(false);
        if (!out.ok) {
          setError(out.error);
          if (out.findings.length) setFindings(out.findings);
        } else {
          setResult(out);
          await reload();
        }
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [path, target, visibility, stripOutputs, reload],
  );

  const remove = async (publication: Publication, forget: boolean) => {
    setError(null);
    try {
      const out = await unpublishNotebook(path, publication.target, forget);
      if (!out.ok) setError(out.error);
      else {
        if (result?.publication?.target === publication.target) setResult(null);
        await reload();
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRemoving(null);
    }
  };

  const saveHtml = async () => {
    setError(null);
    setSavedHtml(null);
    try {
      setSavedHtml((await saveNotebookHtml(path, stripOutputs)).path);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <section className="nb-publish" aria-label="Publish notebook">
      <div className="nb-publish__head">
        <span className="nb-publish__title">Publish</span>
        <span className="nb-publish__meta">{path}</span>
        <span className="nb-publish__spacer" />
        <button
          type="button"
          className="nb-publish__close"
          aria-label="Close the publish panel"
          onClick={onClose}
        >
          <IconClose />
        </button>
      </div>

      <div className="nb-publish__targets" role="radiogroup" aria-label="Where to publish">
        {targets.map((t) => (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={t.id === targetId}
            className={`nb-publish__target${t.id === targetId ? ' is-on' : ''}${
              t.available ? '' : ' is-unavailable'
            }`}
            onClick={() => {
              setTargetId(t.id);
              setResult(null);
              setError(null);
            }}
          >
            {t.label}
            {publications.some((p) => p.target === t.id) && (
              <span className="nb-publish__published" title="Published here" />
            )}
          </button>
        ))}
      </div>

      {target && !target.available && (
        <p className="nb-publish__notice nb-publish__notice--warn">{target.reason}</p>
      )}

      {target && (
        <div className="nb-publish__options">
          {target.visibilities.length > 1 ? (
            <div className="nb-publish__segmented" role="radiogroup" aria-label="Who can see it">
              {target.visibilities.map((v) => (
                <button
                  key={v}
                  type="button"
                  role="radio"
                  aria-checked={v === visibility}
                  className={`nb-publish__segment${v === visibility ? ' is-on' : ''}`}
                  onClick={() => setVisibility(v)}
                >
                  {visibilityLabel(target.id, v)}
                </button>
              ))}
            </div>
          ) : (
            <Chip kind="info">{visibilityLabel(target.id, target.visibilities[0])}</Chip>
          )}
          <label className="nb-publish__check">
            <input
              type="checkbox"
              checked={stripOutputs}
              onChange={(e) => setStripOutputs(e.target.checked)}
            />
            Strip outputs
          </label>
        </div>
      )}
      {target && (
        <p className="nb-publish__hint">{VISIBILITY_HINT[target.id]?.[visibility] ?? ''}</p>
      )}

      <Findings findings={findings} />

      <div className="nb-publish__actions">
        {confirming ? (
          <>
            <Button intent="danger" disabled={busy} onClick={() => void publish(true)}>
              {busy ? 'Publishing…' : 'Publish anyway'}
            </Button>
            <Button intent="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button
            intent="primary"
            disabled={!target?.available || busy || findings === null}
            onClick={() => (blocking > 0 ? setConfirming(true) : void publish(false))}
          >
            {busy
              ? 'Publishing…'
              : `${existing ? 'Update on' : 'Publish to'} ${target?.label ?? ''}`}
          </Button>
        )}
        <Button intent="ghost" onClick={() => void saveHtml()}>
          Save as HTML
        </Button>
      </div>

      {confirming && (
        <p className="nb-publish__notice nb-publish__notice--danger">
          {blocking} finding{blocking === 1 ? '' : 's'} look like secrets. Once published, treat a
          secret as leaked — revoke it even if you unpublish a minute later.
        </p>
      )}
      {error && <p className="nb-publish__notice nb-publish__notice--danger">{error}</p>}
      {savedHtml && <CopyableValue label="Saved" value={savedHtml} />}

      {result?.publication && (
        <>
          <ShareableLink
            url={result.publication.url}
            lead="Read my notebook"
            actions={
              result.publication.extra_url ? (
                <CopyableLink
                  url={result.publication.extra_url}
                  label={EXTRA_LABEL[result.publication.target] ?? 'Source'}
                />
              ) : undefined
            }
          />
          {result.note && <p className="nb-publish__hint">{result.note}</p>}
        </>
      )}

      {publications.length > 0 && (
        <DataList label="Published">
          {publications.map((p, i) => {
            const t = targets.find((x) => x.id === p.target);
            const canDelete = t?.can_unpublish ?? true;
            const asking = removing === p.target;
            return (
              <DataRow
                key={p.target}
                index={i}
                title={t?.label ?? p.target}
                kind="ok"
                meta={[
                  visibilityLabel(p.target, p.visibility),
                  new Date(p.published_at * 1000).toLocaleString(),
                ]}
                actions={
                  asking ? (
                    <>
                      <Button size="sm" intent="danger" onClick={() => void remove(p, !canDelete)}>
                        {canDelete ? 'Delete it' : 'Forget it'}
                      </Button>
                      <Button size="sm" intent="ghost" onClick={() => setRemoving(null)}>
                        Cancel
                      </Button>
                    </>
                  ) : (
                    <Button size="sm" intent="ghost" onClick={() => setRemoving(p.target)}>
                      {canDelete ? 'Unpublish' : 'Forget'}
                    </Button>
                  )
                }
              >
                <CopyableLink url={p.url} label={p.url.replace(/^https?:\/\//, '')} showCopy />
                {asking && (
                  <span className="nb-publish__hint">
                    {canDelete
                      ? `Deletes the copy on ${t?.label ?? p.target}. The link stops working.`
                      : `${t?.label ?? p.target} has no way to delete it from here — remove it on the site, then forget it.`}
                  </span>
                )}
              </DataRow>
            );
          })}
        </DataList>
      )}
    </section>
  );
}
