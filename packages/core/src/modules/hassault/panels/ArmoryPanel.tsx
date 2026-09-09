import { registry } from '../../../registry';
import { ArmoryMarketplace } from './ArmoryMarketplace';

export function ArmoryPanel() {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: 'var(--bg, rgb(13, 17, 23))',
        color: 'var(--text, rgb(241, 245, 249))',
        overflow: 'hidden',
      }}
    >
      {/* Top Interoperability Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0.6rem 1rem',
          borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
          background: 'var(--bg-raised, rgba(22, 27, 34, 0.95))',
          fontSize: '0.75rem',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <span style={{ fontWeight: 800, color: 'rgb(245, 158, 11)' }}>⚔ ARMORY & SKINS</span>
          <span style={{ color: 'rgb(148, 163, 184)', fontSize: '0.7rem' }}>
            Collections, Crate Drops & 10-to-1 Trade-Up Forge
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.7rem', padding: '3px 8px' }}
            onClick={() => registry.openPanel('hassault.play')}
            title="Open HorribleAssault gameplay"
          >
            🎮 Play Arena
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.7rem', padding: '3px 8px', color: 'rgb(56, 189, 248)' }}
            onClick={() => registry.openPanel('hassault.studio')}
            title="Open 3D Model Studio"
          >
            ◈ 3D Studio
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.7rem', padding: '3px 8px' }}
            onClick={() => registry.openPanel('hassault.companion')}
            title="Open Match Companion"
          >
            ⌖ Companion
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.7rem', padding: '3px 8px' }}
            onClick={() => registry.openPanel('hassault.console')}
            title="Open Developer Console"
          >
            ⌨ Console
          </button>
        </div>
      </div>

      {/* Embedded Armory Marketplace */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <ArmoryMarketplace />
      </div>
    </div>
  );
}
