// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

export default function Loading() {
  return (
    <div className="flex h-full flex-col gap-4" aria-busy="true" aria-label="로딩 중">
      <div className="h-7 w-40 animate-pulse rounded bg-muted" aria-hidden="true" />
      <div className="glass flex-1 rounded-apple p-6">
        <div className="space-y-3">
          <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
          <div className="h-4 w-1/2 animate-pulse rounded bg-muted opacity-70" />
          <div className="h-4 w-5/6 animate-pulse rounded bg-muted opacity-50" />
        </div>
      </div>
    </div>
  );
}
