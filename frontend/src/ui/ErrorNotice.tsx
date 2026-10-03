import { errorMessage } from "../api/problem";

/** A failed request, said plainly, with the reference support needs (spec §10). */
export function ErrorNotice({ error }: { error: unknown }) {
  return (
    <p role="alert" className="notice notice-error">
      {errorMessage(error)}
    </p>
  );
}
