"use client";
import { PageTitle } from "@/components/PortalShell";
import Drivers from "@/components/portal/Drivers";

export default function Page() {
  return (
    <>
      <PageTitle title="Delivery dispatch" sub="Drivers, dispatch and delivery zones by store." />
      <Drivers />
    </>
  );
}
