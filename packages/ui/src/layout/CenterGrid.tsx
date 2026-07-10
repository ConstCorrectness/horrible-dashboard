/**
 * Recursive renderer of the center split tree: rows/columns of flex cells sized
 * by the split's fractions, with a draggable sash between each pair of siblings.
 */
import { Fragment } from 'react';
import type { LayoutNode } from '@horrible/core';

import { Area } from './Area';
import { Sash } from './Sash';

export function CenterGrid({
  node,
  focusedAreaId,
}: {
  node: LayoutNode;
  focusedAreaId: string | null;
}) {
  if (node.kind === 'area') {
    return <Area area={node} focused={node.id === focusedAreaId} />;
  }
  return (
    <div className={`frame-split frame-split--${node.orientation}`}>
      {node.children.map((child, i) => (
        <Fragment key={child.id}>
          {i > 0 && <Sash split={node} index={i} />}
          <div className="frame-cell" style={{ flexGrow: node.sizes[i], flexBasis: 0 }}>
            <CenterGrid node={child} focusedAreaId={focusedAreaId} />
          </div>
        </Fragment>
      ))}
    </div>
  );
}
