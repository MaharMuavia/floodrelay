"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // The SSE stream is the live channel; polling is only a safety net
            // for when it drops, so keep it slow and unobtrusive.
            refetchInterval: 20_000,
            refetchOnWindowFocus: true,
            staleTime: 5_000,
            retry: 2,
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
