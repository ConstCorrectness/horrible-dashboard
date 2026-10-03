/**
 * Change one frontmatter property of a page that is not open — the posts board
 * moving a card to another status column.
 *
 * Read, edit the one field (`myst/frontmatter.ts`, so nothing else in the file moves),
 * save against the revision just read. If someone saved in between, the edit is
 * re-applied once to the newer text; a second collision is reported rather than
 * retried forever.
 */
import { readPage, savePage, type Page } from './api';
import { withField, type FieldValue } from './myst/frontmatter';

export async function setPageField(
  site: string,
  path: string,
  key: string,
  value: FieldValue,
): Promise<Page> {
  let page = await readPage(site, path);
  for (let attempt = 0; attempt < 2; attempt++) {
    const result = await savePage(
      site,
      path,
      withField(page.content, key, value),
      page.meta.revision,
    );
    if ('page' in result) return result.page;
    page = result.conflict;
  }
  throw new Error(`${path} kept changing while its ${key} was being set; try again`);
}
