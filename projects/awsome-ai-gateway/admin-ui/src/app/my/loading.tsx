// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { SkeletonCard } from '@/components/common/SkeletonCard';

export default function Loading() {
  return (
    <div className="space-y-6">
      <div className="h-7 w-32 animate-pulse rounded bg-muted" aria-hidden="true" />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1">
          <SkeletonCard count={1} />
        </div>
        <div className="lg:col-span-2">
          <SkeletonCard count={3} />
        </div>
      </div>
    </div>
  );
}
