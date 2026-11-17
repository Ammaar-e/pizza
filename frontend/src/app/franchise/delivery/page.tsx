"use client";
import { PageTitle } from "@/components/PortalShell";
import Drivers from "@/components/portal/Drivers";

export default function Page() {
  return (
    <>
      <PageTitle title="Delivery & drivers" sub="Driver profiles, delivery zones, fees and the live dispatch board." />
      <Drivers />
    </>
  );
}
