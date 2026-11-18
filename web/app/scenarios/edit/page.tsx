import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { ScenarioEditorView } from "./ScenarioEditorView";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <ScenarioEditorView />
    </Suspense>
  );
}
