import DiscoveryBuckets from "../status/DiscoveryBuckets";
import FarmControl from "../status/FarmControl";

export default function Discovery() {
  return (
    <div className="space-y-4 min-w-0">
      <p className="text-sm text-white/55">
        Last-known qualification buckets from GET /api/discovery/summary — not a live
        job. Start / Stop requires the paper ingest token (this tab only; not in the
        public bundle). Filter and sort already-tested rows by column. Champions and
        graduated names live on Champions.
      </p>
      <FarmControl />
      <DiscoveryBuckets />
    </div>
  );
}
