import { Suspense } from "react";
import { JobScreen } from "@/components/JobScreen";
import { Spinner } from "@/components/ui";

export default function JobPage({ params }: PageProps<"/jobs/[id]">) {
  return (
    <Suspense
      fallback={
        <div className="grid flex-1 place-items-center text-muted">
          <Spinner className="size-5" />
        </div>
      }
    >
      {params.then(({ id }) => (
        <JobScreen id={id} />
      ))}
    </Suspense>
  );
}
