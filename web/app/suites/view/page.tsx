import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { SuiteDetailView } from "./SuiteDetailView";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <SuiteDetailView />
    </Suspense>
  );
}
