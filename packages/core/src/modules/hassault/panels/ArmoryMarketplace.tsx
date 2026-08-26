import { useEffect, useState } from 'react';

import {
  claimLevelUpDrop,
  equipSkin,
  executeTradeUp,
  getDropStatus,
  getSkinInventory,
  listSkinCatalog,
  type DropStatus,
  type SkinDefinition,
  type SkinInstance,
} from '../api';
import { WeaponSilhouette } from './WeaponSilhouettes';

const RARITY_BG: Record<string, string> = {
  consumer: 'rgba(176, 195, 217, 0.15)',
  industrial: 'rgba(94, 152, 217, 0.18)',
  mil_spec: 'rgba(75, 105, 255, 0.22)',
  restricted: 'rgba(136, 71, 255, 0.25)',
  classified: 'rgba(211, 44, 230, 0.28)',
  covert: 'rgba(235, 75, 75, 0.32)',
  special: 'rgba(255, 215, 0, 0.35)',
};

export function ArmoryMarketplace() {
  const [tab, setTab] = useState<'inventory' | 'catalog' | 'tradeup'>('inventory');
  const [inventory, setInventory] = useState<SkinInstance[]>([]);
  const [catalog, setCatalog] = useState<SkinDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Unboxing drop animation state
  const [claimingDrop, setClaimingDrop] = useState(false);
  const [latestDrop, setLatestDrop] = useState<SkinInstance | null>(null);
  // The level-up ledger. `null` until it has been read once — which is not the
  // same as zero, so the button says "Checking…" rather than telling a player
  // with three drops waiting that they have none.
  const [drops, setDrops] = useState<DropStatus | null>(null);

  // Trade-up contract selection (up to 10 instances)
  const [selectedTradeUpIds, setSelectedTradeUpIds] = useState<string[]>([]);
  const [tradeUpResult, setTradeUpResult] = useState<SkinInstance | null>(null);

  // Selected item for deep inspection
  const [inspectItem, setInspectItem] = useState<SkinInstance | SkinDefinition | null>(null);
  const [inspectFlourish, setInspectFlourish] = useState(false);

  const refreshData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [inv, cat, status] = await Promise.all([
        getSkinInventory(),
        listSkinCatalog(),
        getDropStatus(),
      ]);
      setInventory(inv);
      setCatalog(cat);
      setDrops(status);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load armory data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refreshData();
  }, []);

  const handleEquip = async (instanceId: string) => {
    try {
      await equipSkin(instanceId);
      setInventory((prev) =>
        prev.map((item) => {
          if (item.instanceId === instanceId) return { ...item, isEquipped: true };
          // If it matches weapon, unequip
          const curDef = item.definition;
          const targetDef = prev.find((x) => x.instanceId === instanceId)?.definition;
          if (curDef && targetDef && curDef.weaponId === targetDef.weaponId) {
            return { ...item, isEquipped: false };
          }
          return item;
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not equip item');
    }
  };

  const handleClaimDrop = async () => {
    // Guarded here as well as on the server, so a double-click never sends the
    // second request at all. The 409 is still what decides it: this count is a
    // copy, and another surface (a match that just ended) can spend it too.
    if (claimingDrop || (drops && drops.available <= 0)) return;
    setClaimingDrop(true);
    setLatestDrop(null);
    setError(null);
    try {
      const drop = await claimLevelUpDrop();
      setLatestDrop(drop);
      await refreshData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Drop claim failed');
      // The reason a claim fails is almost always that there was nothing to
      // claim, so re-read the ledger rather than leaving a button lit that the
      // server has just refused.
      void refreshData();
    } finally {
      setClaimingDrop(false);
    }
  };

  const handleExecuteTradeUp = async () => {
    if (selectedTradeUpIds.length !== 10) return;
    try {
      const forged = await executeTradeUp(selectedTradeUpIds);
      setTradeUpResult(forged);
      setSelectedTradeUpIds([]);
      await refreshData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Trade-Up contract failed');
    }
  };

  // Whether there is anything to claim. `drops === null` is deliberately not
  // ready: an unread ledger is not an empty one, but it is not a licence to
  // press the button either.
  const ready = drops !== null && drops.available > 0;

  const toggleTradeUpSelect = (instanceId: string) => {
    setSelectedTradeUpIds((prev) =>
      prev.includes(instanceId)
        ? prev.filter((id) => id !== instanceId)
        : prev.length < 10
          ? [...prev, instanceId]
          : prev,
    );
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.8rem',
        height: '100%',
        padding: '0.8rem',
        background: 'var(--bg-primary, #0d1117)',
        color: 'var(--text-primary, #c9d1d9)',
        overflow: 'auto',
      }}
    >
      {/* Top Header & Navigation Bar */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.6rem',
        }}
      >
        <div>
          <h3
            style={{
              margin: 0,
              fontSize: '1.15rem',
              color: '#38bdf8',
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
            }}
          >
            🎖 Armory & Skin Marketplace
          </h3>
          <p style={{ margin: '0.2rem 0 0 0', fontSize: '0.75rem', color: 'var(--text-dim)' }}>
            Counter-Strike float wear (0.00–1.00), pattern seeds, trade-up contracts, and rare
            level-up drops.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
          <button
            type="button"
            className={
              tab === 'inventory' ? 'games-town-toggle-btn active' : 'games-town-toggle-btn'
            }
            onClick={() => setTab('inventory')}
          >
            🎒 My Armory ({inventory.length})
          </button>
          <button
            type="button"
            className={tab === 'catalog' ? 'games-town-toggle-btn active' : 'games-town-toggle-btn'}
            onClick={() => setTab('catalog')}
          >
            📖 Collections Catalog
          </button>
          <button
            type="button"
            className={tab === 'tradeup' ? 'games-town-toggle-btn active' : 'games-town-toggle-btn'}
            onClick={() => setTab('tradeup')}
          >
            ⚗ Trade-Up Contract ({selectedTradeUpIds.length}/10)
          </button>
        </div>
      </div>

      {/* Level-Up Drop Banner — reads the ledger, and goes quiet when it is empty. */}
      <div
        style={{
          background: ready
            ? 'linear-gradient(90deg, color-mix(in srgb, var(--warn) 12%, transparent) 0%, transparent 100%)'
            : 'var(--bg-raised)',
          border: `1px solid ${ready ? 'color-mix(in srgb, var(--warn) 35%, transparent)' : 'var(--border-dim)'}`,
          // A 2px accent along the top rather than a glow around the whole
          // perimeter: the banner should read as armed, not as neon.
          borderTop: `2px solid ${ready ? 'var(--warn)' : 'var(--border-dim)'}`,
          borderRadius: 6,
          padding: '0.75rem 1rem',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.6rem',
        }}
      >
        <div style={{ display: 'flex', gap: '0.7rem', alignItems: 'center' }}>
          <CrateIcon lit={ready} />
          <div>
            <strong
              style={{
                color: ready ? 'var(--warn)' : 'var(--text-secondary)',
                fontSize: '0.8rem',
                textTransform: 'uppercase',
                letterSpacing: '0.14em',
              }}
            >
              Level-Up Drop
            </strong>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.2rem' }}>
              {drops === null
                ? 'Reading your progression…'
                : ready
                  ? `${drops.available} drop${drops.available === 1 ? '' : 's'} waiting from level ${drops.level}. Weighted rarity RNG — Mil-Spec through Special.`
                  : `Level ${drops.level} · one drop per level earned. ${drops.xpToNextDrop.toLocaleString()} XP in competitive matches earns the next.`}
            </div>
            {/* The progress toward the next drop, because "come back later" is
                not an answer a player can act on. */}
            {drops !== null && !ready && (
              <div
                style={{
                  height: 4,
                  width: 200,
                  maxWidth: '100%',
                  marginTop: '0.4rem',
                  background: 'var(--bg-tertiary)',
                  borderRadius: 2,
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    height: '100%',
                    width: `${drops.levelProgressPercent}%`,
                    background:
                      'linear-gradient(90deg, color-mix(in srgb, var(--warn) 55%, transparent), var(--warn))',
                    transition: 'width 0.5s ease-out',
                  }}
                />
              </div>
            )}
          </div>
        </div>
        <button
          type="button"
          className="games-play-btn"
          disabled={claimingDrop || !ready}
          onClick={handleClaimDrop}
          style={{
            background: ready ? 'color-mix(in srgb, var(--warn) 22%, transparent)' : 'transparent',
            borderColor: ready ? 'var(--warn)' : 'var(--border-dim)',
            color: ready ? 'var(--warn)' : 'var(--text-dim)',
            cursor: ready ? 'pointer' : 'not-allowed',
            height: 30,
            borderRadius: 6,
          }}
        >
          {claimingDrop
            ? 'Unboxing…'
            : drops === null
              ? 'Checking…'
              : ready
                ? `Claim Drop (${drops.available})`
                : 'No Drops Earned'}
        </button>
      </div>

      {/* Unboxed Drop Popout Alert */}
      {latestDrop && latestDrop.definition && (
        <div
          style={{
            background: RARITY_BG[latestDrop.definition.rarity] || 'rgba(56, 189, 248, 0.2)',
            border: `2px solid ${latestDrop.definition.rarityColor}`,
            borderRadius: 8,
            padding: '0.8rem',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            animation: 'fadeIn 0.3s ease-in-out',
          }}
        >
          <div>
            <span
              style={{
                fontSize: '0.7rem',
                textTransform: 'uppercase',
                fontWeight: 800,
                color: latestDrop.definition.rarityColor,
              }}
            >
              🎉 Unboxed New {latestDrop.definition.rarity.replace('_', ' ')} Item!
            </span>
            <div style={{ fontSize: '1.05rem', fontWeight: 700, marginTop: '0.2rem' }}>
              {latestDrop.definition.name} ({latestDrop.definition.weaponId.toUpperCase()})
            </div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.2rem' }}>
              Wear: <strong>{latestDrop.wearName}</strong> · Float:{' '}
              <code>{latestDrop.floatValue}</code> · Pattern Seed:{' '}
              <code>#{latestDrop.patternSeed}</code>
            </div>
          </div>
          <button type="button" className="games-ghost-btn" onClick={() => setLatestDrop(null)}>
            ✕ Dismiss
          </button>
        </div>
      )}

      {error && (
        <div
          style={{
            color: '#f87171',
            fontSize: '0.8rem',
            background: 'rgba(239, 68, 68, 0.1)',
            padding: '0.5rem',
            borderRadius: 4,
          }}
        >
          {error}
        </div>
      )}

      {/* TAB 1: Player Inventory */}
      {tab === 'inventory' && (
        <div>
          {loading ? (
            <div style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>
              Loading armory inventory…
            </div>
          ) : inventory.length === 0 ? (
            <div style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>
              No skins in armory yet. Play matches or claim a level-up drop!
            </div>
          ) : (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))',
                gap: '0.6rem',
              }}
            >
              {inventory.map((item) => {
                const def = item.definition;
                if (!def) return null;
                const rarityColor = def.rarityColor;
                return (
                  <div
                    key={item.instanceId}
                    style={{
                      background: RARITY_BG[def.rarity] || 'var(--bg-raised, #1c2128)',
                      border: `1px solid ${item.isEquipped ? '#38bdf8' : rarityColor}`,
                      borderRadius: 6,
                      padding: '0.6rem',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.35rem',
                      position: 'relative',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '0.68rem',
                          fontWeight: 800,
                          textTransform: 'uppercase',
                          color: rarityColor,
                        }}
                      >
                        {def.rarity.replace('_', ' ')}
                      </span>
                      {item.isEquipped && (
                        <span
                          style={{
                            fontSize: '0.65rem',
                            padding: '1px 5px',
                            borderRadius: 3,
                            background: '#38bdf8',
                            color: '#0f172a',
                            fontWeight: 800,
                          }}
                        >
                          EQUIPPED
                        </span>
                      )}
                    </div>

                    <div style={{ fontWeight: 700, fontSize: '0.9rem' }}>
                      {def.name}{' '}
                      <span style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>
                        [{def.weaponId.toUpperCase()}]
                      </span>
                    </div>

                    <div
                      style={{
                        height: 70,
                        borderRadius: 4,
                        background:
                          'radial-gradient(circle at center, rgba(255,255,255,0.06) 0%, rgba(0,0,0,0.4) 100%)',
                        border: '1px solid rgba(255,255,255,0.1)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        overflow: 'hidden',
                        padding: '0.2rem',
                      }}
                    >
                      <WeaponSilhouette
                        weaponId={def.weaponId}
                        baseColor={def.baseColor}
                        accentColor={def.accentColor}
                        patternType={def.patternType}
                        patternSeed={item.patternSeed}
                        floatValue={item.floatValue}
                      />
                    </div>

                    <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>
                      <div>
                        Wear: <strong style={{ color: '#ffffff' }}>{item.wearName}</strong>
                      </div>
                      <div>
                        Float: <code>{item.floatValue}</code> · Seed:{' '}
                        <code>#{item.patternSeed}</code>
                      </div>
                      {item.statTrackerKills !== null && (
                        <div style={{ color: '#ea580c', fontWeight: 700, marginTop: '0.1rem' }}>
                          StatTrak™: {item.statTrackerKills} kills
                        </div>
                      )}
                    </div>

                    <div style={{ display: 'flex', gap: '0.3rem', marginTop: '0.2rem' }}>
                      <button
                        type="button"
                        className={item.isEquipped ? 'games-ghost-btn' : 'games-play-btn'}
                        style={{ fontSize: '0.7rem', padding: '2px 8px', flex: 1 }}
                        onClick={() => handleEquip(item.instanceId)}
                        disabled={item.isEquipped}
                      >
                        {item.isEquipped ? 'Equipped' : 'Equip Slot'}
                      </button>
                      <button
                        type="button"
                        className="games-ghost-btn"
                        style={{ fontSize: '0.7rem' }}
                        onClick={() => {
                          setInspectItem(item);
                          setInspectFlourish(false);
                        }}
                      >
                        Inspect
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* TAB 2: Collections Master Catalog */}
      {tab === 'catalog' && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
            gap: '0.6rem',
          }}
        >
          {catalog.map((def) => (
            <div
              key={def.id}
              style={{
                background: RARITY_BG[def.rarity] || 'var(--bg-raised, #1c2128)',
                border: `1px solid ${def.rarityColor}`,
                borderRadius: 6,
                padding: '0.6rem',
                display: 'flex',
                flexDirection: 'column',
                gap: '0.35rem',
              }}
            >
              <div
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              >
                <span
                  style={{
                    fontSize: '0.68rem',
                    fontWeight: 800,
                    textTransform: 'uppercase',
                    color: def.rarityColor,
                  }}
                >
                  {def.rarity.replace('_', ' ')}
                </span>
                <span style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }}>
                  {def.collection}
                </span>
              </div>

              <div style={{ fontWeight: 700, fontSize: '0.9rem' }}>
                {def.name}{' '}
                <span style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>
                  [{def.weaponId.toUpperCase()}]
                </span>
              </div>

              <div
                style={{
                  height: 65,
                  borderRadius: 4,
                  background:
                    'radial-gradient(circle at center, rgba(255,255,255,0.06) 0%, rgba(0,0,0,0.4) 100%)',
                  border: '1px solid rgba(255,255,255,0.1)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '0.2rem',
                }}
              >
                <WeaponSilhouette
                  weaponId={def.weaponId}
                  baseColor={def.baseColor}
                  accentColor={def.accentColor}
                  patternType={def.patternType}
                  floatValue={0.02}
                />
              </div>

              <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>{def.description}</div>
            </div>
          ))}
        </div>
      )}

      {/* TAB 3: 10-to-1 Trade-Up Contract */}
      {tab === 'tradeup' && (
        <div
          style={{
            background: 'var(--bg-raised, #1c2128)',
            border: '1px solid var(--border-dim, #30363d)',
            borderRadius: 8,
            padding: '1rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.8rem',
          }}
        >
          <div>
            <h4 style={{ margin: 0, color: '#38bdf8' }}>
              ⚗ Trade-Up Contract (10 Items → 1 Higher Rarity)
            </h4>
            <p style={{ margin: '0.2rem 0 0 0', fontSize: '0.75rem', color: 'var(--text-dim)' }}>
              Select 10 skins of the exact same rarity tier. The forge will burn them and yield 1
              skin of the next higher rarity tier with float computed from the inputs.
            </p>
          </div>

          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button
              type="button"
              className="games-play-btn"
              disabled={selectedTradeUpIds.length !== 10}
              onClick={handleExecuteTradeUp}
            >
              🔥 Sign & Forge Contract ({selectedTradeUpIds.length}/10 selected)
            </button>
            {selectedTradeUpIds.length > 0 && (
              <button
                type="button"
                className="games-ghost-btn"
                onClick={() => setSelectedTradeUpIds([])}
              >
                Clear Selection
              </button>
            )}
          </div>

          {tradeUpResult && tradeUpResult.definition && (
            <div
              style={{
                background: RARITY_BG[tradeUpResult.definition.rarity] || 'rgba(56, 189, 248, 0.2)',
                border: `2px solid ${tradeUpResult.definition.rarityColor}`,
                borderRadius: 8,
                padding: '0.8rem',
              }}
            >
              <div
                style={{
                  color: tradeUpResult.definition.rarityColor,
                  fontWeight: 800,
                  fontSize: '0.8rem',
                }}
              >
                ⭐ Trade-Up Success!
              </div>
              <div style={{ fontSize: '1.1rem', fontWeight: 700, marginTop: '0.2rem' }}>
                {tradeUpResult.definition.name} ({tradeUpResult.definition.weaponId.toUpperCase()})
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
                Wear: <strong>{tradeUpResult.wearName}</strong> · Float:{' '}
                <code>{tradeUpResult.floatValue}</code>
              </div>
            </div>
          )}

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
              gap: '0.5rem',
            }}
          >
            {inventory.map((item) => {
              const def = item.definition;
              if (!def) return null;
              const isSelected = selectedTradeUpIds.includes(item.instanceId);
              return (
                <div
                  key={item.instanceId}
                  onClick={() => toggleTradeUpSelect(item.instanceId)}
                  style={{
                    background: isSelected
                      ? 'rgba(56, 189, 248, 0.25)'
                      : 'var(--bg-tertiary, #161b22)',
                    border: `1px solid ${isSelected ? '#38bdf8' : def.rarityColor}`,
                    borderRadius: 6,
                    padding: '0.5rem',
                    cursor: 'pointer',
                    opacity: selectedTradeUpIds.length === 10 && !isSelected ? 0.4 : 1,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                    }}
                  >
                    <span style={{ fontSize: '0.65rem', fontWeight: 700, color: def.rarityColor }}>
                      {def.rarity.toUpperCase()}
                    </span>
                    <input type="checkbox" checked={isSelected} readOnly />
                  </div>
                  <div style={{ fontWeight: 700, fontSize: '0.8rem', marginTop: '0.2rem' }}>
                    {def.name}{' '}
                    <span style={{ fontSize: '0.65rem', color: 'var(--text-dim)' }}>
                      [{def.weaponId}]
                    </span>
                  </div>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }}>
                    {item.wearName} (<code>{item.floatValue}</code>)
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Inspect Item Modal */}
      {inspectItem && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.75)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
          }}
          onClick={() => setInspectItem(null)}
        >
          <div
            style={{
              background: 'var(--bg-raised, #1c2128)',
              border: '1px solid var(--border-dim, #30363d)',
              borderRadius: 12,
              padding: '1.4rem',
              maxWidth: 480,
              width: '90%',
              display: 'flex',
              flexDirection: 'column',
              gap: '1rem',
              boxShadow: '0 25px 60px rgba(0,0,0,0.8)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {'definition' in inspectItem && inspectItem.definition ? (
              <>
                <div
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
                >
                  <span
                    style={{
                      color: inspectItem.definition.rarityColor,
                      fontWeight: 800,
                      fontSize: '0.8rem',
                      textTransform: 'uppercase',
                    }}
                  >
                    {inspectItem.definition.rarity.replace('_', ' ')} ·{' '}
                    {inspectItem.definition.collection}
                  </span>
                  <span style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>
                    Seed <code>#{inspectItem.patternSeed}</code>
                  </span>
                </div>

                <div>
                  <h3 style={{ margin: 0, fontSize: '1.3rem' }}>{inspectItem.definition.name}</h3>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>
                    {inspectItem.definition.weaponId.toUpperCase()} · {inspectItem.wearName} (
                    <code>{inspectItem.floatValue}</code>)
                  </div>
                </div>

                {/* 3D Weapon Silhouette Viewport */}
                <div
                  style={{
                    height: 140,
                    borderRadius: 8,
                    background:
                      'radial-gradient(circle at center, rgba(56, 189, 248, 0.12) 0%, rgba(13, 17, 23, 0.95) 75%)',
                    border: '1px solid var(--border-dim, #30363d)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    position: 'relative',
                    overflow: 'hidden',
                  }}
                >
                  <WeaponSilhouette
                    weaponId={inspectItem.definition.weaponId}
                    baseColor={inspectItem.definition.baseColor}
                    accentColor={inspectItem.definition.accentColor}
                    patternType={inspectItem.definition.patternType}
                    patternSeed={inspectItem.patternSeed}
                    floatValue={inspectItem.floatValue}
                    isInspecting={inspectFlourish}
                  />

                  <button
                    type="button"
                    className="games-ghost-btn"
                    style={{
                      position: 'absolute',
                      bottom: 8,
                      right: 8,
                      fontSize: '0.72rem',
                      padding: '2px 8px',
                      background: inspectFlourish ? 'rgba(56, 189, 248, 0.3)' : 'rgba(0,0,0,0.6)',
                    }}
                    onClick={() => setInspectFlourish((v) => !v)}
                  >
                    🎯 Ogre-Twitch Flourish [F]
                  </button>
                </div>

                {/* Float Wear Meter */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      fontSize: '0.72rem',
                    }}
                  >
                    <span>
                      Float Value: <code>{inspectItem.floatValue}</code>
                    </span>
                    <strong style={{ color: inspectItem.definition.rarityColor }}>
                      {inspectItem.wearName}
                    </strong>
                  </div>
                  <div
                    style={{
                      height: 6,
                      background: '#21262d',
                      borderRadius: 3,
                      position: 'relative',
                      overflow: 'hidden',
                    }}
                  >
                    <div
                      style={{
                        position: 'absolute',
                        left: `${Math.min(100, inspectItem.floatValue * 100)}%`,
                        top: 0,
                        bottom: 0,
                        width: 4,
                        background: '#ffffff',
                        boxShadow: '0 0 6px #ffffff',
                        transform: 'translateX(-50%)',
                      }}
                    />
                    <div
                      style={{
                        height: '100%',
                        width: '100%',
                        background:
                          'linear-gradient(90deg, #22c55e 0%, #38bdf8 15%, #eab308 38%, #f97316 45%, #ef4444 100%)',
                      }}
                    />
                  </div>
                </div>

                <div
                  style={{
                    fontSize: '0.78rem',
                    color: 'var(--text-dim)',
                    fontStyle: 'italic',
                    background: 'var(--bg-tertiary, #161b22)',
                    padding: '0.6rem',
                    borderRadius: 6,
                  }}
                >
                  "{inspectItem.definition.description}"
                </div>
              </>
            ) : null}
            <button type="button" className="games-ghost-btn" onClick={() => setInspectItem(null)}>
              Close Inspect
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * The supply-crate glyph on the drop banner.
 *
 * A stroke icon rather than 🎁: it takes its colour from the container, so
 * "armed" and "nothing to claim" are the same drawing in two colours instead of
 * an emoji that is always festive.
 */
function CrateIcon({ lit }: { lit: boolean }) {
  return (
    <svg
      width="26"
      height="26"
      viewBox="0 0 24 24"
      fill="none"
      stroke={lit ? 'var(--warn)' : 'var(--text-dim)'}
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flexShrink: 0, transition: 'stroke 0.3s ease' }}
    >
      <path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z" />
      <path d="M3 7.5 12 12l9-4.5M12 12v9" />
      {lit && <circle cx="12" cy="12" r="2.2" fill="var(--warn)" stroke="none" opacity="0.9" />}
    </svg>
  );
}
