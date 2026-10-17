/**
 * Settings → Music connections: the services that feed the library.
 *
 * None of them plays anything. Spotify lends its playlists, Last.fm and
 * ListenBrainz take scrobbles and answer "what's similar" for the DJ. Each row is
 * the shared connect flow (`requestConnect`), not a bespoke form.
 */
import { useConnector } from '../../connectors/useConnectors';
import { requestConnect } from '../../connectors/store';
import { DataList, DataRow } from '../../DataList';
import { Button } from '../../Primitives';

const SERVICES: { id: string; label: string; does: string }[] = [
  {
    id: 'spotify',
    label: 'Spotify',
    does: 'Import your playlists and Liked Songs. Read-only; songs are downloaded into your library.',
  },
  {
    id: 'lastfm',
    label: 'Last.fm',
    does: 'Scrobble what you play, import loved tracks, and power the DJ and radio.',
  },
  {
    id: 'listenbrainz',
    label: 'ListenBrainz',
    does: 'Submit what you play to the open listening archive.',
  },
];

function ServiceRow({
  id,
  label,
  does,
  index,
}: {
  id: string;
  label: string;
  does: string;
  index: number;
}) {
  const { connected, connector } = useConnector(id);
  const account = connector?.account?.label;
  return (
    <DataRow
      index={index}
      kind={connected ? 'ok' : 'idle'}
      title={label}
      meta={connected && account ? [account] : ['not connected']}
      actions={
        <Button
          size="sm"
          intent={connected ? 'ghost' : 'primary'}
          onClick={() => requestConnect(id)}
        >
          {connected ? 'Manage' : 'Connect'}
        </Button>
      }
    >
      {does}
    </DataRow>
  );
}

export function MusicConnectionsSection() {
  return (
    <DataList label="Music connections">
      {SERVICES.map((s, i) => (
        <ServiceRow key={s.id} index={i} {...s} />
      ))}
    </DataList>
  );
}
