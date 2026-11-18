import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { HomeView } from "./HomeView";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <HomeView />
    </Suspense>
  );
}
