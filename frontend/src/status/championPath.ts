/** Encode a paper-book name for `/champions/<name>` — keeps `&`, `/`, spaces in one segment. */
export function championDetailPath(name: string): string {
  return `/champions/${encodeURIComponent(name)}`;
}

/** Decode a route param or splat. React Router may already decode; a second pass is a no-op. */
export function decodeChampionName(raw: string | undefined): string {
  if (!raw) return "";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}
