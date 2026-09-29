// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { SkeletonCard } from '@/components/common/SkeletonCard';
import { SkeletonTable } from '@/components/common/SkeletonTable';

export default function Loading() {
  return (
    <div className="space-y-6">
      <div className="h-7 w-40 animate-pulse rounded bg-muted" aria-hidden="true" />
      <SkeletonCard count={6} />
      <SkeletonTable rows={6} columns={5} />
      <SkeletonTable rows={5} columns={4} />
      <SkeletonTable rows={8} columns={3} />
    </div>
  );
}
