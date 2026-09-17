/**
 * Running a suite on a friend's agent, from both ends.
 *
 * Not the "run on" picker above it. That borrows a friend's llama.cpp through a compute
 * lease while this node grades with its own catalog. This hands the cases to the
 * friend's node, which runs them through *its own* agent — its skills, its MCP servers,
 * its model — and reports back. The result answers "how does their setup do on my
 * suite", and none of it is verifiable from here, which the Compare view says.
 *
 * On the receiving end nothing runs until a person accepts, and the offer shows what
 * it will cost them first. See backend/modules/evals/fabric.py.
 */
import { useCallback, useEffect, useState } from 'react';

import { DataList, DataRow } from '../../DataList';
import { subscribeChannel } from '../../ws';
import {
  acceptOffer,
  declineOffer,
  listIncomingOffers,
  listRemoteFriends,
  offerSuite,
  stopOffer,
  type IncomingOffer,
  type RemoteFriend,
} from './api';

const mono = {
  fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)',
  fontSize: 11,
  color: 'var(--text-dim)',
} as const;

const heading = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase' as const,
  color: 'var(--text)',
};

const head = { display: 'flex', alignItems: 'baseline', gap: 8, margin: '16px 0 6px' };

/** "~12,400 tokens · free" — a floor, and said to be one. */
export function describeCost(offer: Pick<IncomingOffer, 'estimateTokens' | 'estimateCostUsd'>) {
  const tokens = `at least ~${offer.estimateTokens.toLocaleString('en-US')} input tokens`;
  if (offer.estimateCostUsd === null) return `${tokens} · price unknown`;
  if (offer.estimateCostUsd === 0) return `${tokens} · free (local model)`;
  return `${tokens} · at least $${offer.estimateCostUsd.toFixed(2)}`;
}

export function OfferToFriend({ suiteId, onOffered }: { suiteId: string; onOffered: () => void }) {
  const [friends, setFriends] = useState<RemoteFriend[]>([]);
  const [node, setNode] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    listRemoteFriends()
      .then(setFriends)
      .catch(() => setFriends([]));
  }, []);

  const offer = async () => {
    setBusy(true);
    setMessage('');
    try {
      const out = await offerSuite({ node_id: node, suite_id: suiteId });
      const name = friends.find((f) => f.node_id === node)?.name ?? 'your friend';
      setMessage(
        `Offered ${out.run.total} case${out.run.total === 1 ? '' : 's'} to ${name}. Nothing runs until they accept.` +
          (out.skipped.length
            ? ` ${out.skipped.length} left out — benchmark and judge cases do not run on another node.`
            : ''),
      );
      onOffered();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div style={head}>
        <span style={heading}>On a friend&apos;s agent</span>
        <span style={mono}>their node, their skills and model, their tokens</span>
      </div>
      {friends.length === 0 ? (
        <div style={mono}>No connected friend runs a build that accepts suite offers.</div>
      ) : (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            aria-label="Friend to offer the suite to"
            value={node}
            onChange={(e) => setNode(e.target.value)}
            style={{ padding: '0 0.6rem' }}
          >
            <option value="">choose a friend…</option>
            {friends.map((f) => (
              <option key={f.node_id} value={f.node_id}>
                {f.name}
              </option>
            ))}
          </select>
          <button disabled={!node || !suiteId || busy} onClick={() => void offer()}>
            {busy ? 'Offering…' : 'Offer suite'}
          </button>
        </div>
      )}
      <div style={{ ...mono, marginTop: 6 }}>
        Their node grades the cases and reports back; results rank beside yours, marked as reported.
        They must have remote suites turned on and accept this offer.
      </div>
      {message && <div style={{ ...mono, marginTop: 6 }}>{message}</div>}
    </>
  );
}

export function IncomingOffers() {
  const [offers, setOffers] = useState<IncomingOffer[]>([]);
  const [accepting, setAccepting] = useState(false);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');

  const reload = useCallback(() => {
    listIncomingOffers()
      .then((r) => {
        setOffers(r.offers);
        setAccepting(r.accepting);
      })
      .catch(() => setOffers([]));
  }, []);

  useEffect(reload, [reload]);
  useEffect(() => subscribeChannel('evals', () => reload()), [reload]);

  const act = async (id: string, fn: (id: string) => Promise<unknown>) => {
    setBusy(id);
    setMessage('');
    try {
      await fn(id);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
      reload();
    }
  };

  // Silent unless there is something to answer: a node not accepting offers, with
  // none waiting, has nothing to say here.
  if (offers.length === 0) return null;

  return (
    <>
      <div style={head}>
        <span style={heading}>Offers from friends</span>
        <span style={mono}>{offers.length} waiting or running</span>
      </div>
      {!accepting && (
        <div style={{ ...mono, color: 'var(--warn)' }}>
          Remote suites are off on this node (evals.acceptRemoteSuites), so these cannot be
          accepted.
        </div>
      )}
      <DataList label="Suite offers from friends">
        {offers.map((o, i) => (
          <DataRow
            key={o.offerId}
            index={i}
            title={`${o.fromName} · ${o.suiteName}`}
            kind={o.state === 'running' ? 'info' : 'warn'}
            meta={[
              o.state === 'running' ? `running on ${o.model}` : 'waiting for you',
              `${o.cases} case${o.cases === 1 ? '' : 's'}`,
              describeCost(o),
            ]}
            footnotes={
              o.state === 'pending' ? (
                <div style={mono}>
                  Runs on your agent with your model. No tool acts — every call gets the
                  suite&apos;s own fixture — but your tokens are spent, and your model name plus
                  your skills&apos; and MCP tools&apos; names and content hashes are sent back with
                  the answers.
                </div>
              ) : undefined
            }
            actions={
              o.state === 'pending' ? (
                <>
                  <button
                    disabled={!accepting || busy === o.offerId}
                    onClick={() => void act(o.offerId, acceptOffer)}
                  >
                    Accept
                  </button>
                  <button
                    disabled={busy === o.offerId}
                    onClick={() => void act(o.offerId, declineOffer)}
                  >
                    Decline
                  </button>
                </>
              ) : (
                <button
                  disabled={busy === o.offerId}
                  onClick={() => void act(o.offerId, stopOffer)}
                >
                  Stop
                </button>
              )
            }
          />
        ))}
      </DataList>
      {message && <div style={{ ...mono, marginTop: 6 }}>{message}</div>}
    </>
  );
}
