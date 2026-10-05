import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api, idempotencyKey } from "../../../api/client";
import { unwrap } from "../../../api/problem";
import { findingKey } from "../../../findings/finding";
import type { Org } from "../../../orgs/org";
import { ErrorNotice } from "../../../ui/ErrorNotice";

/**
 * A comment on the finding (spec §7): plain text, kept as written. Its idempotency key lasts as
 * long as its text, so resending a comment that failed never posts it twice.
 */
export function CommentForm({ org, findingId }: { org: Org; findingId: string }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [key, setKey] = useState(idempotencyKey);
  const comment = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/orgs/{org_id}/findings/{finding_id}/comments", {
          params: { path: { org_id: org.id, finding_id: findingId } },
          body: { text: text.trim() },
          headers: { "Idempotency-Key": key },
        }),
      ),
    onSuccess: async () => {
      setText("");
      setKey(idempotencyKey());
      await queryClient.invalidateQueries({ queryKey: findingKey(org.id, findingId) });
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    comment.mutate();
  }
  return (
    <form className="comment-form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="comment-text">Add a comment</label>
        <textarea
          id="comment-text"
          rows={3}
          maxLength={2000}
          value={text}
          aria-describedby="comment-hint"
          onChange={(event) => {
            setText(event.target.value);
            setKey(idempotencyKey());
          }}
        />
        <p id="comment-hint" className="muted">
          Up to 2,000 characters. Everyone in the organization can read it.
        </p>
      </div>
      <button type="submit" className="button" disabled={text.trim() === "" || comment.isPending}>
        Comment
      </button>
      {comment.isError && <ErrorNotice error={comment.error} />}
    </form>
  );
}
