/** Status poll. Slow /api/status responses must not stack. */
export const STATUS_POLL_MS = 30_000;
/** Compact /api/discovery/summary (counts, farm). Its server cache moves every few seconds; nobody needs it faster. */
export const SUMMARY_POLL_MS = 60_000;
/** /api/discovery/blocks. The server rebuilds it at most every 10 minutes. */
export const BLOCKS_POLL_MS = 600_000;

/**
 * Arm the next poll only when the previous request has finished.
 * A numeric refetchInterval calls refetch({ cancelRefetch: true }) and
 * starts another HTTP request while the server is still building the last one.
 */
export function pollInterval(ms: number) {
  return (query: { state: { fetchStatus: string } }): number | false =>
    query.state.fetchStatus === "fetching" ? false : ms;
}
