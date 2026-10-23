"use client";

import { memo, PropsWithChildren } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Prevent automatic refetching on various events
      refetchOnWindowFocus: false,
      refetchOnMount: false,
      refetchOnReconnect: false,
      // Retry failed requests up to 2 times with exponential backoff
      retry: 2,
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000),
      // Consider data stale after 5 minutes (reduces unnecessary refetches)
      staleTime: 5 * 60 * 1000,
      // Keep unused cache data for 10 minutes before garbage collection
      gcTime: 10 * 60 * 1000,
    },
    mutations: {
      // Retry mutations once on failure
      retry: 1,
    },
  },
});

export const ReactQueryClientProvider = ({ children }: PropsWithChildren) => {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

export default memo(ReactQueryClientProvider);
