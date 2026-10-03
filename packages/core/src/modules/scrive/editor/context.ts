/**
 * What a node view needs to know about the page it is in: the site and the page's
 * path, so a relative image or a raw block's preview resolves media the way MyST
 * does. TipTap renders React node views through portals inside `EditorContent`, so
 * context from above the editor reaches them.
 */
import { createContext, useContext } from 'react';

export interface ScriveDoc {
  site: string;
  pagePath: string;
}

export const ScriveDocContext = createContext<ScriveDoc>({ site: '', pagePath: '' });

export const useScriveDoc = () => useContext(ScriveDocContext);
