/** Status poll. Slow /api/status responses must not stack. */
export const STATUS_POLL_MS = 30_000;

/**
 * Arm the next poll only when the previous request has finished.
 * A numeric refetchInterval calls refetch({ cancelRefetch: true }) and
 * starts another HTTP request while the server is still building the last one.
 */
export function pollInterval(ms: number) {
  return (query: { state: { fetchStatus: string } }): number | false =>
    query.state.fetchStatus === "fetching" ? false : ms;
}
