"use client";

import {
  Skeleton,
  SkeletonCard,
  SkeletonLine,
  SkeletonPage,
} from "@/platform/components/ui/Skeleton";

/**
 * Generic loading placeholder.
 * Shows a contextual skeleton with a title area + card grid.
 * Individual pages can override with their own `loading.tsx`.
 */
const Loading = ({ message = "Loading..." }: { message?: string }) => {
  return (
    <SkeletonPage title={message}>
      {/* Stat-card row */}
      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <SkeletonCard key={i} delay={i * 80}>
            <div className="space-y-3">
              <SkeletonLine width="w-20" height="h-3" />
              <SkeletonLine width="w-28" height="h-6" delay={60} />
              <SkeletonLine width="w-16" height="h-3" delay={120} />
            </div>
          </SkeletonCard>
        ))}
      </div>

      {/* Two-column content area */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <SkeletonCard className="min-h-[280px]" delay={100}>
          <div className="space-y-4">
            <SkeletonLine width="w-32" height="h-5" />
            <Skeleton className="h-48 w-full rounded-lg" delay={150} />
          </div>
        </SkeletonCard>
        <SkeletonCard className="min-h-[280px]" delay={160}>
          <div className="space-y-4">
            <SkeletonLine width="w-40" height="h-5" />
            <Skeleton className="h-48 w-full rounded-lg" delay={200} />
          </div>
        </SkeletonCard>
      </div>
    </SkeletonPage>
  );
};

export default Loading;
