"use client";
import { PageTitle } from "@/components/PortalShell";
import Promotions from "@/components/portal/Promotions";

export default function Page() {
  return (
    <>
      <PageTitle title="Promotions" sub="Local promotions within headquarters policy, plus network promotions running in your stores." />
      <Promotions />
    </>
  );
}
