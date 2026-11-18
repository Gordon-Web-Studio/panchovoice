"use client";
import { Welcome } from "@/components/Welcome";
import { usePageContext } from "@/lib/hooks";

export default function Page() {
  usePageContext("onboarding");
  return <Welcome />;
}
