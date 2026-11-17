"use client";
import { PageTitle } from "@/components/PortalShell";
import Integrations from "@/components/portal/Integrations";

export default function Page() {
  return (
    <>
      <PageTitle title="Delivery integrations" sub="Connect each store to Uber Eats, DoorDash and SkipTheDishes independently." />
      <Integrations />
    </>
  );
}
