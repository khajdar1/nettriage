import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { sha256Hex } from "../../api/client";
import { fakeStorage } from "../../test/fakeStorage";
import { ORG_ID, OWNER, UPLOAD_ID, VIEWER, upload } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const UPLOADS = `GET ${ORG}/uploads`;
const PAGE = `/app/orgs/${ORG_ID}/uploads`;
const SIGNED_URL =
  "https://nettriage-dev-uploads.s3.eu-north-1.amazonaws.com/o/u?X-Amz-Signature=1";
const SIGNED_HEADERS = { "x-amz-checksum-sha256": "c2hh", "x-amz-meta-traceparent": "00-ab-cd-01" };
const NONE = { body: { uploads: [], next_cursor: null } };

function created(filename = "port-scan.log") {
  return {
    status: 201,
    body: {
      upload: upload({
        original_filename: filename,
        status: "pending_upload",
        rows_parsed: null,
        rows_rejected: null,
      }),
      upload_url: SIGNED_URL,
      upload_headers: SIGNED_HEADERS,
      expires_at: "2026-10-04T09:35:00Z",
    },
  };
}

test("uploads are listed with their status, rows and a link to their findings", async () => {
  signedInAs(OWNER, {
    [UPLOADS]: {
      body: {
        uploads: [
          upload(),
          upload({
            id: "01a10500-0000-7000-8000-000000000002",
            original_filename: "notes.txt",
            status: "failed",
            failure_reason: "No line in this file is a VPC flow log record.",
            rows_parsed: 0,
          }),
        ],
        next_cursor: null,
      },
    },
  });

  renderAt(PAGE);

  const analyzed = await screen.findByRole("row", { name: /port-scan\.log/ });
  expect(within(analyzed).getByText("Analyzed")).toBeInTheDocument();
  expect(within(analyzed).getByText("3 rejected")).toBeInTheDocument();
  expect(
    within(analyzed).getByRole("link", { name: "Findings from port-scan.log" }),
  ).toHaveAttribute("href", `/app/orgs/${ORG_ID}/findings?upload=${UPLOAD_ID}`);
  const failed = screen.getByRole("row", { name: /notes\.txt/ });
  expect(within(failed).getByText("Failed")).toBeInTheDocument();
  expect(within(failed).getByText("No line in this file is a VPC flow log record.")).toBeVisible();
  expect(within(failed).queryByRole("link")).toBeNull();
  expect(document.title).toBe("Uploads · Acme Security · NetTriage");
});

test("a contributor uploads a file: it's fingerprinted, sent to storage as signed, and listed", async () => {
  let reads = 0;
  const fake = signedInAs(OWNER, {
    [UPLOADS]: () => ({
      body: { uploads: ++reads === 1 ? [] : [upload({ status: "processing" })], next_cursor: null },
    }),
    [`POST ${ORG}/uploads`]: created(),
  });
  const storage = fakeStorage();
  const { user } = renderAt(PAGE);
  const content = "2 123456789012 eni-1 10.0.3.17 10.0.0.5 40000 22 6 1 40 1 2 REJECT OK\n";

  await user.upload(
    await screen.findByLabelText("Flow log file"),
    new File([content], "port-scan.log"),
  );
  await user.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByText(/Uploaded port-scan\.log/)).toBeInTheDocument();
  const post = fake.requests.find((request) => request.method === "POST");
  const bytes = new TextEncoder().encode(content);
  expect(await post?.json()).toEqual({
    filename: "port-scan.log",
    size_bytes: bytes.byteLength,
    sha256: await sha256Hex(bytes),
  });
  expect(post?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
  expect(storage.puts).toHaveLength(1);
  expect(storage.puts[0]?.method).toBe("PUT");
  expect(storage.puts[0]?.url).toBe(SIGNED_URL);
  expect(storage.puts[0]?.headers).toEqual(SIGNED_HEADERS);
  expect(new TextDecoder().decode(storage.puts[0]?.body)).toBe(content);
  expect(await screen.findByText("Analyzing")).toBeInTheDocument();
});

test("once a file is uploaded it's cleared, so a second click can't send it twice", async () => {
  const fake = signedInAs(OWNER, { [UPLOADS]: NONE, [`POST ${ORG}/uploads`]: created() });
  fakeStorage();
  const { user } = renderAt(PAGE);
  const input = await screen.findByLabelText("Flow log file");

  await user.upload(input, new File(["x\n"], "port-scan.log"));
  await user.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByText(/Uploaded port-scan\.log/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
  expect(fake.requests.filter((request) => request.method === "POST")).toHaveLength(1);
});

test("a file over 25 MB, or an empty one, is refused before anything is sent", async () => {
  const fake = signedInAs(OWNER, { [UPLOADS]: NONE });
  const { user } = renderAt(PAGE);
  const input = await screen.findByLabelText("Flow log file");

  await user.upload(input, new File([new Uint8Array(25 * 1024 * 1024 + 1)], "huge.log"));
  await user.click(screen.getByRole("button", { name: "Upload" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Files can be at most 25 MB.");

  await user.upload(input, new File([], "empty.log"));
  await user.click(screen.getByRole("button", { name: "Upload" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This file is empty.");
  expect(fake.requests.some((request) => request.method === "POST")).toBe(false);
});

test("storage refusing the file says so, and the same file can be sent again", async () => {
  const fake = signedInAs(OWNER, { [UPLOADS]: NONE, [`POST ${ORG}/uploads`]: created("a.log") });
  fakeStorage({ status: 403 });
  const { user } = renderAt(PAGE);

  await user.upload(await screen.findByLabelText("Flow log file"), new File(["x\n"], "a.log"));
  await user.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Storage refused the file (status 403). Upload it again.",
  );
  fakeStorage();
  await user.click(screen.getByRole("button", { name: "Upload" }));
  expect(await screen.findByText(/Uploaded a\.log/)).toBeInTheDocument();
  const keys = fake.requests
    .filter((request) => request.method === "POST")
    .map((request) => request.headers.get("Idempotency-Key"));
  expect(new Set(keys).size).toBe(2);
});

test("an upload the API refuses says why", async () => {
  signedInAs(OWNER, {
    [UPLOADS]: NONE,
    [`POST ${ORG}/uploads`]: {
      status: 503,
      body: { title: "Service Unavailable", detail: "Uploads are paused.", trace_id: "t5" },
    },
  });
  const { user } = renderAt(PAGE);

  await user.upload(await screen.findByLabelText("Flow log file"), new File(["x\n"], "a.log"));
  await user.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Uploads are paused. (reference t5)");
});

test("a viewer sees the uploads, with nothing to upload", async () => {
  signedInAs(VIEWER, { [UPLOADS]: { body: { uploads: [upload()], next_cursor: null } } });

  renderAt(PAGE);

  expect(await screen.findByRole("row", { name: /port-scan\.log/ })).toBeInTheDocument();
  expect(screen.queryByLabelText("Flow log file")).toBeNull();
});

test("no uploads yet says so", async () => {
  signedInAs(VIEWER, { [UPLOADS]: NONE });

  renderAt(PAGE);

  expect(await screen.findByText("No uploads yet.")).toBeInTheDocument();
});

test("older uploads load a page at a time", async () => {
  const older = upload({
    id: "01a10500-0000-7000-8000-000000000009",
    original_filename: "old.log",
  });
  signedInAs(OWNER, {
    [UPLOADS]: (request) =>
      new URL(request.url).searchParams.get("cursor") === "c2"
        ? { body: { uploads: [older], next_cursor: null } }
        : { body: { uploads: [upload()], next_cursor: "c2" } },
  });
  const { user } = renderAt(PAGE);

  await user.click(await screen.findByRole("button", { name: "Show older uploads" }));

  expect(await screen.findByRole("row", { name: /old\.log/ })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /port-scan\.log/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show older uploads" })).toBeNull();
});
