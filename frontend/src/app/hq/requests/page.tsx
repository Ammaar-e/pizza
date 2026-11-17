"use client";
import { PageTitle } from "@/components/PortalShell";
import ProductRequests from "@/components/portal/ProductRequests";

export default function Page() {
  return (
    <>
      <PageTitle title="Product requests" sub="Franchisee requests for new products awaiting headquarters approval." />
      <ProductRequests />
    </>
  );
}
