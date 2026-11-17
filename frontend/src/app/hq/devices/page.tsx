"use client";
import { PageTitle } from "@/components/PortalShell";
import Devices from "@/components/portal/Devices";

export default function Page() {
  return (
    <>
      <PageTitle title="Devices" sub="Every POS terminal, register, kitchen display and peripheral in the network." />
      <Devices />
    </>
  );
}
