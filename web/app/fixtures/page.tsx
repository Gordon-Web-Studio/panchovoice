import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { FixturesView } from "./FixturesView";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <FixturesView />
    </Suspense>
  );
}
