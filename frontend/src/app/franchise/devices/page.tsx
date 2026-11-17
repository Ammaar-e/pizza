"use client";
import { PageTitle } from "@/components/PortalShell";
import Devices from "@/components/portal/Devices";

export default function Page() {
  return (
    <>
      <PageTitle title="Devices" sub="Terminals and displays registered to your stores." />
      <Devices />
    </>
  );
}
