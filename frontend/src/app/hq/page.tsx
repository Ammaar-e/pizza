"use client";
import { PageTitle } from "@/components/PortalShell";
import Reports from "@/components/portal/Reports";
import { useStores } from "@/components/portal/useStores";

export default function Page() {
  const { stores } = useStores();
  return (
    <>
      <PageTitle title="Network dashboard" sub="Sales, channels and store comparisons across all franchisees." />
      <Reports storeFilter={stores} />
    </>
  );
}
