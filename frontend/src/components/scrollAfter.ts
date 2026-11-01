/** How many list rows or cards stay fully visible before the rest scrolls. */
export const VISIBLE_ROWS = 5;

/** Attribute marking a row or card that counts toward the visible window. */
export const SCROLL_ITEM_ATTR = "data-scroll-item";

/**
 * Border-box height of a scrollport that shows content through the bottom
 * edge of the last fully visible item. `scrollTop` is added back so a
 * measurement taken while already scrolled still describes that window.
 * `extra` is the horizontal scrollbar, which sits inside the border box
 * and would otherwise cover the last visible row. Ceil avoids clipping
 * that row on a fractional device pixel.
 */
export function scrollportHeight(
  limitBottom: number,
  rootTop: number,
  scrollTop: number,
  extra = 0,
): number {
  return Math.ceil(limitBottom - rootTop + scrollTop + extra);
}

/** Items whose bottom edge is still past the visible bottom. */
export function itemsBelowFold(bottoms: readonly number[], viewBottom: number, slack = 4): number {
  let hidden = 0;
  for (const bottom of bottoms) {
    if (bottom > viewBottom + slack) hidden += 1;
  }
  return hidden;
}

export function listNeedsScroll(itemCount: number, visible = VISIBLE_ROWS): boolean {
  return itemCount > visible;
}

/**
 * How many items remain below the fold. Before the max-height is applied
 * the container is as tall as every row, so the count is the tail past
 * `visible` rather than a viewport test that would see them all.
 */
export function hiddenBelow(
  itemCount: number,
  bottoms: readonly number[],
  viewBottom: number,
  unconstrained: boolean,
  visible = VISIBLE_ROWS,
): number {
  if (!listNeedsScroll(itemCount, visible)) return 0;
  if (unconstrained) return Math.max(0, itemCount - visible);
  return itemsBelowFold(bottoms, viewBottom);
}
