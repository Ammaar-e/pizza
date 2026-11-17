"use client";
import { PageTitle } from "@/components/PortalShell";
import Reports from "@/components/portal/Reports";
import { useStores } from "@/components/portal/useStores";

export default function Page() {
  const { stores } = useStores();
  return (
    <>
      <PageTitle title="Sales & reporting" sub="Sales, channels and store comparisons for your stores." />
      <Reports storeFilter={stores} />
    </>
  );
}
