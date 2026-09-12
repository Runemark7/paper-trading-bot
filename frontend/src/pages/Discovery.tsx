import DiscoveryBuckets from "../status/DiscoveryBuckets";

export default function Discovery() {
  return (
    <div className="space-y-4 min-w-0">
      <p className="text-sm text-white/55">
        Last-known qualification buckets from GET /api/discovery/summary — not a live
        job. Filter and sort already-tested rows by column. Champions and graduated names live
        on Champions.
      </p>
      <DiscoveryBuckets />
    </div>
  );
}
