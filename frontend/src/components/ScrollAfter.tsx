import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  SCROLL_ITEM_ATTR,
  VISIBLE_ROWS,
  hiddenBelow,
  listNeedsScroll,
  scrollportHeight,
} from "./scrollAfter";

type Chrome = { maxHeight: number; bar: number; hidden: number };

/**
 * Shows the first five rows or cards at their natural height, then scrolls
 * the rest inside the section. Section headings stay outside this element.
 */
export function ScrollAfter({
  children,
  label,
  itemCount,
  className = "",
}: {
  children: ReactNode;
  /** Accessible name for the scroll region, e.g. "Admitted champions". */
  label: string;
  itemCount: number;
  className?: string;
}) {
  const hintId = useId();
  const scroller = useRef<HTMLDivElement>(null);
  const rowSize = useRef(48);
  const [chrome, setChrome] = useState<Chrome | null>(null);
  const scrolling = chrome != null;

  useLayoutEffect(() => {
    const root = scroller.current;
    if (!root) return;

    const measure = () => {
      const items = root.querySelectorAll<HTMLElement>(`[${SCROLL_ITEM_ATTR}]`);
      if (!listNeedsScroll(items.length) || !listNeedsScroll(itemCount)) {
        setChrome((prev) => (prev === null ? prev : null));
        return;
      }
      const limit = items[VISIBLE_ROWS - 1];
      const rootRect = root.getBoundingClientRect();
      const bar = Math.max(0, root.offsetHeight - root.clientHeight);
      const next = scrollportHeight(limit.getBoundingClientRect().bottom, rootRect.top, root.scrollTop, bar);
      if (!Number.isFinite(next) || next < 24) return;
      rowSize.current = Math.max(32, Math.round((next - bar) / VISIBLE_ROWS));
      const unconstrained = root.clientHeight > next + 1;
      const atEnd = root.scrollTop + root.clientHeight >= root.scrollHeight - 2;
      const bottoms = Array.from(items, (item) => item.getBoundingClientRect().bottom);
      const hidden = atEnd ? 0 : hiddenBelow(items.length, bottoms, rootRect.bottom, unconstrained);
      setChrome((prev) => {
        if (
          prev &&
          Math.abs(prev.maxHeight - next) <= 1 &&
          prev.hidden === hidden &&
          Math.abs(prev.bar - bar) <= 1
        ) {
          return prev;
        }
        return { maxHeight: next, bar, hidden };
      });
    };

    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(root);
    const watch = new Set<Element>();
    const observeItems = () => {
      root.querySelectorAll<HTMLElement>(`[${SCROLL_ITEM_ATTR}]`).forEach((el) => {
        if (watch.has(el)) return;
        watch.add(el);
        ro.observe(el);
      });
    };
    observeItems();
    const mo = new MutationObserver(() => {
      observeItems();
      measure();
    });
    mo.observe(root, { childList: true, subtree: true });
    root.addEventListener("scroll", measure, { passive: true });
    return () => {
      ro.disconnect();
      mo.disconnect();
      root.removeEventListener("scroll", measure);
    };
  }, [itemCount]);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (!scrolling || e.target !== e.currentTarget) return;
    const el = e.currentTarget;
    const row = rowSize.current;
    let top = 0;
    let left = 0;
    switch (e.key) {
      case "ArrowDown":
        top = row;
        break;
      case "ArrowUp":
        top = -row;
        break;
      case "PageDown":
        top = Math.max(row, el.clientHeight - row);
        break;
      case "PageUp":
        top = -Math.max(row, el.clientHeight - row);
        break;
      case "Home":
        top = -el.scrollTop;
        break;
      case "End":
        top = el.scrollHeight;
        break;
      case "ArrowRight":
        left = Math.max(48, row);
        break;
      case "ArrowLeft":
        left = -Math.max(48, row);
        break;
      default:
        return;
    }
    e.preventDefault();
    el.scrollBy({ top, left });
  }

  const showHint = scrolling && chrome.hidden > 0;
  // Reserve the scrollbar before the first measure so wrapping (and therefore
  // the five-row height) does not change once the region is constrained.
  const expectScroll = scrolling || listNeedsScroll(itemCount);

  return (
    <div className={`min-w-0 max-w-full ${className}`}>
      <div className="relative min-w-0">
        <div
          ref={scroller}
          tabIndex={scrolling ? 0 : undefined}
          role={scrolling ? "region" : undefined}
          aria-label={
            scrolling
              ? `${label}. ${itemCount} items. First ${VISIBLE_ROWS} visible, scroll for the rest.`
              : undefined
          }
          aria-describedby={showHint ? hintId : undefined}
          onKeyDown={onKeyDown}
          data-scroll-after={scrolling ? "true" : "false"}
          className={
            expectScroll
              ? "overflow-x-auto overflow-y-auto overscroll-y-contain rounded-md [scrollbar-color:rgba(255,255,255,0.28)_transparent] [scrollbar-gutter:stable] [scrollbar-width:thin] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400"
              : "overflow-x-auto"
          }
          style={scrolling ? { maxHeight: chrome.maxHeight } : undefined}
        >
          {children}
        </div>
        {showHint ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 h-3 bg-gradient-to-t from-black/45 to-transparent"
            style={{ bottom: chrome.bar }}
          />
        ) : null}
      </div>
      {showHint ? (
        <p id={hintId} className="mt-1.5 text-[11px] leading-snug text-white/45">
          {chrome.hidden} more, scroll
        </p>
      ) : null}
    </div>
  );
}
