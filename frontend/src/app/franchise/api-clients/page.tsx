"use client";
import { PageTitle } from "@/components/PortalShell";
import ApiClients from "@/components/portal/ApiClients";

export default function Page() {
  return (
    <>
      <PageTitle title="API clients" sub="Your website or call-centre integrations." />
      <ApiClients />
    </>
  );
}
