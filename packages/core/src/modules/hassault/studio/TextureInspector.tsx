import React from 'react';
import { IconTexture } from './StudioIcons';

export interface TextureSlot {
  name: string;
  type: 'base_color' | 'normal' | 'roughness' | 'metallic' | 'ambient_occlusion';
  path: string;
  resolution?: string;
}

export interface TextureInspectorProps {
  modelName: string;
  textures: string[];
  materials: string[];
  activeChannel: 'pbr' | 'albedo' | 'normal' | 'roughness' | 'wireframe';
  onChannelChange: (channel: 'pbr' | 'albedo' | 'normal' | 'roughness' | 'wireframe') => void;
}

export function TextureInspector({
  modelName,
  textures,
  materials,
  activeChannel,
  onChannelChange,
}: TextureInspectorProps) {
  // Classify detected textures by channel type based on naming convention
  const slots: TextureSlot[] = textures.map((t) => {
    const lower = t.toLowerCase();
    let type: TextureSlot['type'] = 'base_color';
    if (lower.includes('normal') || lower.includes('_n.') || lower.includes('_norm')) {
      type = 'normal';
    } else if (lower.includes('rough') || lower.includes('_r.')) {
      type = 'roughness';
    } else if (lower.includes('metal') || lower.includes('_m.')) {
      type = 'metallic';
    } else if (lower.includes('ao') || lower.includes('occlusion')) {
      type = 'ambient_occlusion';
    }

    const filename = t.split('/').pop() || t;
    return {
      name: filename,
      type,
      path: t,
    };
  });

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <IconTexture size={14} color="rgb(56, 189, 248)" />
          <span style={styles.title}>
            {modelName ? `${modelName} • ` : ''}Material &amp; Texture Channels (Principled BSDF)
          </span>
        </div>
        <span style={{ fontSize: '0.68rem', color: 'rgb(100, 116, 139)', fontFamily: 'monospace' }}>
          {materials.length} mat / {textures.length} tex
        </span>
      </div>

      {/* Render Channel Selector */}
      <div style={styles.channelRow}>
        <span style={styles.channelLabel}>Viewport Shading:</span>
        {(
          [
            { id: 'pbr', label: 'Full PBR' },
            { id: 'albedo', label: 'Base Color' },
            { id: 'normal', label: 'Normal Map' },
            { id: 'roughness', label: 'Roughness' },
            { id: 'wireframe', label: 'Wireframe' },
          ] as const
        ).map((ch) => {
          const isActive = activeChannel === ch.id;
          return (
            <button
              key={ch.id}
              onClick={() => onChannelChange(ch.id)}
              style={{
                ...styles.channelBtn,
                ...(isActive ? styles.channelBtnActive : styles.channelBtnInactive),
              }}
            >
              {ch.label}
            </button>
          );
        })}
      </div>

      {/* Texture Slots Grid */}
      <div style={styles.slotsGrid}>
        {slots.length > 0 ? (
          slots.map((slot, i) => {
            const typeColor =
              slot.type === 'base_color'
                ? 'rgb(56, 189, 248)'
                : slot.type === 'normal'
                ? 'rgb(168, 85, 247)'
                : slot.type === 'roughness'
                ? 'rgb(245, 158, 11)'
                : slot.type === 'metallic'
                ? 'rgb(16, 185, 129)'
                : 'rgb(148, 163, 184)';

            const typeLabel =
              slot.type === 'base_color'
                ? 'Base Color / Albedo'
                : slot.type === 'normal'
                ? 'Tangent Normal'
                : slot.type === 'roughness'
                ? 'Microfacet Roughness'
                : slot.type === 'metallic'
                ? 'Metallic Mask'
                : 'Ambient Occlusion';

            return (
              <div key={i} style={styles.slotCard}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
                  <span style={{ ...styles.slotBadge, color: typeColor, borderColor: `${typeColor}44`, background: `${typeColor}15` }}>
                    {typeLabel}
                  </span>
                  <span style={{ fontSize: '0.62rem', color: 'rgb(100, 116, 139)', fontFamily: 'monospace' }}>2048x2048</span>
                </div>
                <div style={styles.slotName} title={slot.path}>
                  {slot.name}
                </div>
                <div style={styles.slotPath} title={slot.path}>
                  {slot.path}
                </div>
              </div>
            );
          })
        ) : (
          <div style={styles.emptyNotice}>
            <span>No external textures linked (Procedural or Embedded GLTF textures)</span>
          </div>
        )}
      </div>

      {/* Material Names */}
      {materials.length > 0 && (
        <div style={styles.materialsSection}>
          <span style={styles.channelLabel}>Material Slots:</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {materials.map((m) => (
              <span key={m} style={styles.matPill}>
                {m}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
    padding: '10px 14px',
    background: 'rgb(13, 18, 29)',
    borderTop: '1px solid rgba(255, 255, 255, 0.08)',
    fontSize: '0.74rem',
    color: 'rgb(203, 213, 225)',
    userSelect: 'none',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  title: {
    fontSize: '0.72rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
    color: 'rgb(226, 232, 240)',
  },
  channelRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  channelLabel: {
    fontSize: '0.68rem',
    color: 'rgb(100, 116, 139)',
    fontWeight: 600,
  },
  channelBtn: {
    padding: '3px 8px',
    borderRadius: '4px',
    fontSize: '0.68rem',
    fontWeight: 600,
    border: 'none',
    cursor: 'pointer',
    transition: 'background 0.15s, color 0.15s',
  },
  channelBtnActive: {
    background: 'rgb(37, 99, 235)',
    color: 'rgb(255, 255, 255)',
  },
  channelBtnInactive: {
    background: 'rgba(255, 255, 255, 0.06)',
    color: 'rgb(148, 163, 184)',
  },
  slotsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
    gap: '6px',
    maxHeight: '120px',
    overflowY: 'auto',
  },
  slotCard: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: '5px',
    padding: '6px 8px',
  },
  slotBadge: {
    fontSize: '0.6rem',
    fontFamily: 'monospace',
    textTransform: 'uppercase',
    padding: '1px 4px',
    borderRadius: '3px',
    border: '1px solid',
  },
  slotName: {
    fontSize: '0.72rem',
    fontWeight: 600,
    color: 'rgb(241, 245, 249)',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  slotPath: {
    fontSize: '0.62rem',
    color: 'rgb(100, 116, 139)',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    marginTop: '1px',
  },
  emptyNotice: {
    gridColumn: '1 / -1',
    padding: '10px',
    color: 'rgb(100, 116, 139)',
    fontStyle: 'italic',
    fontSize: '0.7rem',
    textAlign: 'center',
  },
  materialsSection: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    paddingTop: '4px',
    borderTop: '1px solid rgba(255, 255, 255, 0.05)',
  },
  matPill: {
    padding: '2px 6px',
    background: 'rgba(255, 255, 255, 0.06)',
    color: 'rgb(203, 213, 225)',
    borderRadius: '3px',
    fontFamily: 'monospace',
    fontSize: '0.66rem',
  },
};
