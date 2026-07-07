/**
 * The global quick-open host for `SymbolSearch` — a backdrop + centered panel,
 * self-contained (reads its own open state). AppShell renders it unconditionally;
 * it draws nothing when closed. Reuses the command palette's `.palette` styling.
 * See docs/modules/code.mdx.
 */
import { SymbolSearch } from './SymbolSearch';
import { symbolSearchModal, useSymbolSearchModalOpen } from './searchModal';

export function SymbolSearchModal() {
  const open = useSymbolSearchModalOpen();
  if (!open) return null;
  return (
    <div className="palette-backdrop" onClick={() => symbolSearchModal.set(false)}>
      <div className="palette symbol-search-modal" onClick={(e) => e.stopPropagation()}>
        <SymbolSearch onClose={() => symbolSearchModal.set(false)} />
      </div>
    </div>
  );
}
