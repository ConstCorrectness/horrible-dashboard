import { useEffect, useState } from 'react';
import { mapArt } from '../maps';

/**
 * The full-bleed scene behind the landing page and the deploy splash: a real
 * frame of the map, pushed down and away so it reads as a place rather than as
 * content competing with the type.
 *
 * Crossfades when the map changes — the outgoing frame stays mounted until the
 * incoming one has decoded, so a slow image never flashes the page black. With
 * no art for a map it renders only the grade, which is the graphite ground.
 */
export function MapBackdrop({
  mapId,
  focus = 'left',
}: {
  mapId: string;
  focus?: 'left' | 'center';
}) {
  const src = mapArt(mapId);
  const [layers, setLayers] = useState<{ src: string; key: number }[]>(() =>
    src ? [{ src, key: 0 }] : [],
  );

  useEffect(() => {
    if (!src) {
      setLayers([]);
      return;
    }
    let cancelled = false;
    const img = new Image();
    img.src = src;
    const show = () => {
      if (cancelled) return;
      setLayers((prev) =>
        prev.at(-1)?.src === src
          ? prev
          : [...prev.slice(-1), { src, key: (prev.at(-1)?.key ?? 0) + 1 }],
      );
    };
    // decode() rejects on a broken image; show it anyway and let the grade cover it.
    img.decode().then(show, show);
    return () => {
      cancelled = true;
    };
  }, [src]);  

  return (
    <div className={`backdrop backdrop-${focus}`} aria-hidden="true">
      {layers.map((layer, i) => (
        <img
          key={layer.key}
          src={layer.src}
          alt=""
          className="backdrop-img"
          style={{ animation: i > 0 || layers.length === 1 ? 'fade-in .9s ease both' : undefined }}
        />
      ))}
      <div className="backdrop-grade" />
      <div className="backdrop-grain" />
    </div>
  );
}
