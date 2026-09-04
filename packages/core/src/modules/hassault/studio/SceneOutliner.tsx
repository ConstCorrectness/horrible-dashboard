import React, { useState } from 'react';
import type { SceneNodeType, StudioScene } from './sceneTypes';
import {
  IconLayers,
  IconEye,
  IconEyeOff,
  IconLock,
  IconTrash,
  IconPlus,
  IconSun,
  IconSpawn,
  IconPickup,
  IconMesh,
} from './StudioIcons';

export interface SceneOutlinerProps {
  scene: StudioScene;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  onToggleVisibility: (nodeId: string) => void;
  onToggleLock: (nodeId: string) => void;
  onDeleteNode: (nodeId: string) => void;
  onAddEntity: (type: SceneNodeType, name?: string, weaponId?: string) => void;
}

export function SceneOutliner({
  scene,
  selectedNodeId,
  onSelectNode,
  onToggleVisibility,
  onToggleLock,
  onDeleteNode,
  onAddEntity,
}: SceneOutlinerProps) {
  const [showAddMenu, setShowAddMenu] = useState(false);

  const getNodeIcon = (type: SceneNodeType) => {
    switch (type) {
      case 'light':
        return <IconSun size={12} color="rgb(251, 191, 36)" />;
      case 'spawn_point':
        return <IconSpawn size={12} color="rgb(239, 68, 68)" />;
      case 'weapon_pickup':
        return <IconPickup size={12} color="rgb(245, 158, 11)" />;
      case 'mesh_prop':
        return <IconMesh size={12} color="rgb(52, 211, 153)" />;
      case 'collision_box':
      default:
        return <IconMesh size={12} color="rgb(34, 197, 94)" />;
    }
  };

  return (
    <div style={styles.container}>
      {/* Outliner Header */}
      <div style={styles.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <IconLayers size={13} color="rgb(148, 163, 184)" />
          <span style={styles.title}>Scene Outliner</span>
        </div>

        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setShowAddMenu(!showAddMenu)}
            style={styles.addBtn}
            title="Add Entity or Prop to Scene"
          >
            <IconPlus size={11} color="rgb(255, 255, 255)" />
            <span>Add</span>
          </button>

          {showAddMenu && (
            <div style={styles.dropdownMenu}>
              <div style={styles.dropdownHeader}>Team Spawns</div>
              <button
                onClick={() => {
                  onAddEntity('spawn_point', 'CLA Spawn');
                  setShowAddMenu(false);
                }}
                style={styles.dropdownItem}
              >
                <IconSpawn size={12} color="rgb(239, 68, 68)" />
                <span>CLA Team Spawn</span>
              </button>
              <button
                onClick={() => {
                  onAddEntity('spawn_point', 'RVS Spawn');
                  setShowAddMenu(false);
                }}
                style={styles.dropdownItem}
              >
                <IconSpawn size={12} color="rgb(59, 130, 246)" />
                <span>RVS Team Spawn</span>
              </button>

              <div style={styles.dropdownHeader}>Weapon Pickups</div>
              <button
                onClick={() => {
                  onAddEntity('weapon_pickup', 'FN FAL Rifle Pickup', 'fal');
                  setShowAddMenu(false);
                }}
                style={styles.dropdownItem}
              >
                <IconPickup size={12} color="rgb(245, 158, 11)" />
                <span>FN FAL Rifle</span>
              </button>
              <button
                onClick={() => {
                  onAddEntity('weapon_pickup', 'M4A1 Carbine Pickup', 'm4a1');
                  setShowAddMenu(false);
                }}
                style={styles.dropdownItem}
              >
                <IconPickup size={12} color="rgb(245, 158, 11)" />
                <span>M4A1 Carbine</span>
              </button>
              <button
                onClick={() => {
                  onAddEntity('weapon_pickup', 'Beretta 92 Pistol Pickup', 'beretta');
                  setShowAddMenu(false);
                }}
                style={styles.dropdownItem}
              >
                <IconPickup size={12} color="rgb(245, 158, 11)" />
                <span>Beretta 92 Pistol</span>
              </button>
              <button
                onClick={() => {
                  onAddEntity('weapon_pickup', 'Remington 870 CQB Pickup', 'shotgun');
                  setShowAddMenu(false);
                }}
                style={styles.dropdownItem}
              >
                <IconPickup size={12} color="rgb(245, 158, 11)" />
                <span>Remington 870 Shotgun</span>
              </button>
              <button
                onClick={() => {
                  onAddEntity('weapon_pickup', 'SVU-A Sniper Pickup', 'svua');
                  setShowAddMenu(false);
                }}
                style={styles.dropdownItem}
              >
                <IconPickup size={12} color="rgb(245, 158, 11)" />
                <span>SVU-A Sniper Rifle</span>
              </button>

              <div style={styles.dropdownHeader}>Environment</div>
              <button
                onClick={() => {
                  onAddEntity('light', 'Point Light');
                  setShowAddMenu(false);
                }}
                style={styles.dropdownItem}
              >
                <IconSun size={12} color="rgb(251, 191, 36)" />
                <span>Omni Light</span>
              </button>
              <button
                onClick={() => {
                  onAddEntity('collision_box', 'Collision Box');
                  setShowAddMenu(false);
                }}
                style={styles.dropdownItem}
              >
                <IconMesh size={12} color="rgb(34, 197, 94)" />
                <span>Collision Barrier</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Nodes Tree */}
      <div style={styles.treeList}>
        {scene.nodes.length > 0 ? (
          scene.nodes.map((node) => {
            const isSelected = selectedNodeId === node.id;
            return (
              <div
                key={node.id}
                onClick={() => onSelectNode(node.id)}
                style={{
                  ...styles.treeItem,
                  ...(isSelected ? styles.treeItemActive : styles.treeItemInactive),
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0, flex: 1 }}>
                  {getNodeIcon(node.type)}
                  <span style={styles.nodeName} title={node.name}>
                    {node.name}
                  </span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleVisibility(node.id);
                    }}
                    style={styles.iconBtn}
                    title={node.visible ? 'Hide object' : 'Show object'}
                  >
                    {node.visible ? <IconEye size={12} color="rgb(148, 163, 184)" /> : <IconEyeOff size={12} color="rgb(239, 68, 68)" />}
                  </button>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleLock(node.id);
                    }}
                    style={styles.iconBtn}
                    title={node.locked ? 'Unlock transform' : 'Lock transform'}
                  >
                    <IconLock size={12} color={node.locked ? 'rgb(251, 191, 36)' : 'rgb(100, 116, 139)'} />
                  </button>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteNode(node.id);
                    }}
                    style={{ ...styles.iconBtn, color: 'rgb(248, 113, 113)' }}
                    title="Delete object from scene"
                  >
                    <IconTrash size={12} color="rgb(248, 113, 113)" />
                  </button>
                </div>
              </div>
            );
          })
        ) : (
          <div style={styles.emptyOutliner}>
            <span>Scene has no objects. Click "+ Add" to place an entity.</span>
          </div>
        )}
      </div>

      {/* Outliner Summary */}
      <div style={styles.footer}>
        <span>{scene.nodes.length} objects in scene</span>
        <span style={{ color: 'rgb(100, 116, 139)' }}>Grid Snap: 0.5m</span>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    minHeight: 0,
    background: 'rgb(13, 18, 29)',
    userSelect: 'none',
  },
  header: {
    padding: '8px 10px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexShrink: 0,
  },
  title: {
    fontSize: '0.7rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
    color: 'rgb(203, 213, 225)',
  },
  addBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    background: 'rgb(37, 99, 235)',
    color: 'rgb(255, 255, 255)',
    border: 'none',
    borderRadius: '4px',
    padding: '2px 8px',
    fontSize: '0.66rem',
    fontWeight: 600,
    cursor: 'pointer',
  },
  dropdownMenu: {
    position: 'absolute',
    right: 0,
    top: '24px',
    background: 'rgb(21, 28, 42)',
    border: '1px solid rgba(255, 255, 255, 0.12)',
    borderRadius: '6px',
    padding: '4px',
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    zIndex: 50,
    boxShadow: '0 8px 24px rgba(0, 0, 0, 0.5)',
    minWidth: '160px',
  },
  dropdownHeader: {
    padding: '4px 8px 2px',
    fontSize: '0.62rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
    color: 'rgb(148, 163, 184)',
  },
  dropdownItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    background: 'transparent',
    border: 'none',
    color: 'rgb(226, 232, 240)',
    padding: '5px 8px',
    borderRadius: '4px',
    fontSize: '0.7rem',
    cursor: 'pointer',
    textAlign: 'left',
    transition: 'background 0.1s',
  },
  treeList: {
    flex: 1,
    overflowY: 'auto',
    padding: '6px',
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    minHeight: 0,
  },
  treeItem: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '4px 8px',
    borderRadius: '4px',
    cursor: 'pointer',
    fontSize: '0.72rem',
    transition: 'background 0.1s',
  },
  treeItemActive: {
    background: 'linear-gradient(90deg, rgba(37, 99, 235, 0.3) 0%, rgba(30, 58, 138, 0.15) 100%)',
    borderLeft: '2px solid rgb(59, 130, 246)',
    color: 'rgb(255, 255, 255)',
    fontWeight: 600,
  },
  treeItemInactive: {
    background: 'transparent',
    borderLeft: '2px solid transparent',
    color: 'rgb(203, 213, 225)',
  },
  nodeName: {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontSize: '0.7rem',
  },
  iconBtn: {
    background: 'transparent',
    border: 'none',
    padding: '2px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: '2px',
  },
  emptyOutliner: {
    padding: '16px 8px',
    textAlign: 'center',
    color: 'rgb(100, 116, 139)',
    fontSize: '0.68rem',
    fontStyle: 'italic',
  },
  footer: {
    padding: '6px 10px',
    borderTop: '1px solid rgba(255, 255, 255, 0.08)',
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: '0.64rem',
    color: 'rgb(100, 116, 139)',
    background: 'rgb(10, 14, 23)',
    flexShrink: 0,
  },
};
