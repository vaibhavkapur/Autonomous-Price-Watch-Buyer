"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** Visible label for the simulated clock used by the accelerated demo (plan §20). */
export function ClockBadge() {
  const [clock, setClock] = useState<{ now: string; simulated: boolean } | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api<{ clock: { now: string; simulated: boolean } }>("/v1/metrics")
        .then((m) => alive && setClock(m.clock))
        .catch(() => alive && setClock(null));
    load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  if (!clock) return <span className="badge">API offline</span>;
  return (
    <span className={"badge " + (clock.simulated ? "sim" : "")} title="Server clock used for every expiry decision">
      {clock.simulated ? "SIMULATED CLOCK " : "clock "} {clock.now.replace("T", " ").slice(0, 19)}Z
    </span>
  );
}
