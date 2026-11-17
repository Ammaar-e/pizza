"use client";
import { PageTitle } from "@/components/PortalShell";
import Promotions from "@/components/portal/Promotions";

export default function Page() {
  return (
    <>
      <PageTitle title="Promotions & discounts" sub="Network-wide promotions, publishing, franchise permissions and performance." />
      <Promotions />
    </>
  );
}
