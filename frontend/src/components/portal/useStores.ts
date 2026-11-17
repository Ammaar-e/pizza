"use client";
import { useCallback, useEffect, useState } from "react";
import { portalApi } from "@/components/PortalShell";
import { Store, results } from "@/lib/api";

export function useStores() {
  const [stores, setStores] = useState<Store[]>([]);
  const reload = useCallback(() => portalApi.get("/stores/?page_size=200").then((d) => setStores(results<Store>(d))), []);
  useEffect(() => {
    reload();
  }, [reload]);
  return { stores, reload };
}
