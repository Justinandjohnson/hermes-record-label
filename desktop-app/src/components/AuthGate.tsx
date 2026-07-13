import type { ReactNode } from "react";

export default function AuthGate({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
