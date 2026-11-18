import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { RunsView } from "./RunsView";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <RunsView />
    </Suspense>
  );
}
