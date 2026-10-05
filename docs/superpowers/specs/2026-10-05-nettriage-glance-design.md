# NetTriage: Findings at a Glance (Plan 6d design)

| | |
|---|---|
| **Status** | Design approved by the owner on 2026-10-05; this written spec awaits the owner's review |
| **Amends** | `2026-09-26-nettriage-m1-design.md` (revision 2): §7 (API) and §10 (frontend). Plan 6d's docs task copies the decisions below into those sections |
| **Mockups** | https://claude.ai/artifact/YYqKhQxaipQBttbVKms73K (private), approved |

## 1. Why

After walking through runbook B12 on dev, the owner asked for a web app that is "more practical and prettier", with more detail. Asked which details mattered most, the owner chose two:
- **my work:** the findings assigned to me;
- **findings at a glance:** how each organization is doing.

They then left the design to Claude ("Do some thinking, don't depend on me for design").

The pages from Plans 6a and 6b open straight into lists. Nothing shows at a glance what is unresolved, how bad it is, who owns it, or what arrived last, so a person has to page through findings to know any of it.

**Success:**
- **After signing in**, a person sees the unresolved findings assigned to them in every organization, worst first, and each organization's state, without opening anything.
- **Opening an organization** shows its unresolved findings by severity and status, and its quick views, above the list. Every number filters the list.
- **A finding's page** says, in one line under its title, where it stands: status, assignee, when it was detected, its window, its upload, and the AI's verdict.
- The **Sweep** look stays, with amber only for what a detector found.

**Out of scope** (YAGNI, not asked for):
- an organization switcher in the header;
- triage of several findings at once;
- previous and next links between findings;
- charts over time beyond the usage page's.

## 2. Words

- **Unresolved** means status **Open** or **Investigating**: the findings still to be dealt with. **Resolved** and **False positive** are closed.
- **Yours** means assigned to the signed-in person.
- **New in the last day** means detected (created) within the last 24 hours.

## 3. The pages

### 3.1 Home (`/app`)

The page signed-in people land on. It keeps its job as the organization switcher (§10), and becomes:

1. **Assigned to you.** A table of the unresolved findings assigned to the signed-in person, in all their organizations (at most 3, §5.7), most severe first, then newest:
   - **Severity:** bars and word.
   - **Finding:** the title, linking to the finding's page. Under it: the detector's name, the flow from source to destination (and port, when there is one) in mono, and the AI status.
   - **Status**, the **Organization** name, and **Detected** as a relative time.
   - The heading's note counts them: "3 unresolved, in 2 organizations".
   - If there are none: "Nothing is assigned to you. Pick up an unassigned finding in one of your organizations."
   - At most 20 per organization are fetched. If an organization has more, the table ends with a link: "N more in Acme Security".
2. **Organizations.** One card per membership, in the order `/me` lists them:
   - its name, linking to the organization, and the person's role;
   - **unresolved by severity:** four counts, each with its severity bars. Each links to the organization's findings with that severity and **Unresolved**, and a zero is dimmed;
   - **facts:** Unassigned (unresolved), Yours (unresolved), Members, and Last upload (a relative time and its status, or "None yet");
   - a primary **Open findings** button, and a note counting the closed findings ("31 resolved or false positive").
3. **Create an organization** stays at the bottom, as today. An organization the person creates appears among the cards.

### 3.2 Findings at a glance (`/app/orgs/:org/findings`)

Organizations still open here (the owner's decision, Plan 6b). Under the heading, the note counts "N unresolved of M". Then a panel:
- **Unresolved, by severity:** four tiles. Each holds the severity's bars drawn large, the count in expanded Archivo, and the word. Each tile is a link that sets the severity filter and **Unresolved**; a zero tile is dimmed.
- **Quick views:** pills with counts.
  - **Yours** sets the assignee to me and **Unresolved**.
  - **Unassigned** sets the assignee to nobody and **Unresolved**.
  - **New in the last day** sets the new filter and status to any.

  The pill for the view in use looks pressed (`aria-current="true"`).
- **All M, by status:**
  - a track splitting the total by status in ink tints (Open darkest, then Investigating, then Resolved, then False positive hatched), drawn in SVG with its widths as attributes (the CSP allows no inline styles) and hidden from screen readers;
  - under it, a legend whose four entries link to that status, with their counts.
- **Last upload:** its file name, linking to the list filtered to it, how long ago, and its number of findings. If there's none: "No uploads yet."

The filters gain **Assignee** (Anyone, Yours, Unassigned) and the status choice **Unresolved**, which becomes the default. The status select reads: Unresolved, Any status, Open, Investigating, Resolved, False positive.

In the address:
- `status` is absent (Unresolved), `any`, or one status;
- `assignee` is `me` or `none`;
- `new=day`.

As in Plan 6b, a value that isn't real is read as no filter, and only what differs from the defaults is written.

Each row gains:
- under the title: the detector's name, the flow (source → destination, with the port when there is one), and the AI status. The AI status is **Explained** in teal, **Not explained yet** with a hollow dot (no analysis, or one pending), or **No explanation** with a hollow dot, when the latest attempt failed or was skipped;
- the **Assignee** with initials in a dark disc, or "You", or "Unassigned" with a dashed disc;
- **Detected** as a relative time, with the exact time on hover.

The panel is read from the organization's overview (§4.1). Its numbers come from the API, so a filter never changes them.

### 3.3 A finding's page (`/app/orgs/:org/findings/:id`)

Under the title, a summary strip (a description list) reads:
- **Status:** the state chip.
- **Assignee:** initials and name, or "Unassigned".
- **Detected:** relative.
- **Window:** start to end, as `HH:MM to HH:MM` with the date when they differ, in the person's local time like the details.
- **Upload:** the file name, linking to the list filtered to it. It comes from the upload's own read, so it shows when that succeeds.
- **AI:** the verdict. If the AI agrees: "Agrees: High, medium confidence". If it suggests another severity: "Suggests Medium". Otherwise one of "Not explained yet", "Queued", or "No explanation", for a failure (the AI panel says why).

Everything else stays as Plan 6b built it.

### 3.4 Uploads (`/app/orgs/:org/uploads`)

- The table gains a **Findings** column for analyzed uploads: "6 findings, worst High", or "No findings".
- The **Uploaded** column shows a relative time.

### 3.5 Times everywhere

A shared `<Ago>` component writes a time as "just now" (under a minute), "N min ago", "N h ago", "yesterday", "N days ago" (under a week), or else the date. It renders a `<time dateTime>` whose `title` is the exact local date and time.

It's used for:
- the home's tables and cards;
- the findings list;
- the finding's strip;
- uploads;
- the activity timeline.

The audit log keeps exact times, as a record should.

## 4. The API (amends §7)

### 4.1 `GET /api/v1/orgs/{org}/overview`

- **Permission:** `findings:read`, so every role.
- **Reads:** the org's findings and uploads under row-level security, in one transaction.

```json
{
  "unresolved_by_severity": { "critical": 2, "high": 9, "medium": 14, "low": 3 },
  "by_status": { "open": 18, "investigating": 10, "resolved": 28, "false_positive": 4 },
  "unresolved_unassigned": 7,
  "unresolved_mine": 2,
  "new_last_day": 5,
  "member_count": 4,
  "last_upload": { "…": "UploadOut, or null when the org has none" }
}
```

- Every severity and status key is always present, as 0 when there are none.
- `unresolved_mine` counts the caller's own unresolved findings.
- `new_last_day` counts findings whose `created_at` is within 24 hours of the database's `now()`.
- `last_upload` is the newest upload by `created_at`, whatever its status.

### 4.2 Findings list: new filters and an AI status

- **`status`** may repeat, matching any of its values (`?status=open&status=investigating`). Absent means any, as before. The frontend's "Unresolved" sends both.
- **`assignee`:**
  - `me` means the caller;
  - `none` means unassigned;
  - absent means anyone;
  - any other value is a 422.
- **`since`:** an ISO 8601 timestamp; findings created at or after it. The frontend sends 24 hours before its own time for **New in the last day**.
- **Combining:** filters combine with AND, and every filter works with both sorts and their cursors.
- **`ai_status`** on each `FindingSummaryOut` is the status of the finding's latest analysis (`pending`, `succeeded`, `failed`, `skipped_budget`, `invalid_output`), or null if it has none. "Latest" means latest by `updated_at`, as the finding's own read picks it.

### 4.3 Uploads: their findings

`UploadOut` gains two fields:
- `findings`: the number of findings stored for the upload. Uploads that aren't analyzed report 0.
- `worst_severity`: the most severe of them, or null.

They are computed in the list and single-upload reads, and by the overview's `last_upload`. These are the stored findings; `findings_truncated` still counts those not kept.

### 4.4 Compatibility

- The new fields are additions.
- `status` accepting a list keeps a single value valid.
- No migration: every number is computed by a query over existing tables and indexes. An org holds at most a few thousand findings (§5.7), which Postgres counts in milliseconds.

## 5. Look (Sweep, unchanged rules)

- **Amber** stays the detector's color: the severity bars and ladders, and the logo. The status track, quick views, initials and buttons are ink. The AI status is teal, as the AI's label is.
- **Ladder tiles and cards** use a white plate with a 1px grid border and an 8 to 10px radius. On hover, the border turns ink.
- **Counts** are tabular figures in expanded Archivo.
- **Phone widths:** the panel stacks, the ladder becomes two by two, the strip two columns, and the cards one column.
- **No inline styles:** the status track and anything sized by data is SVG with attribute geometry.

## 6. Accessibility

- **Ladder tiles and card counts** are links named by their meaning, such as "2 unresolved Critical findings".
- **The status track** is hidden from screen readers; its legend links carry the numbers.
- **Quick views** use `aria-current` for the one in use.
- **The summary strip** is a `<dl>`.
- **Initials** are hidden from screen readers next to the name, or are the name's only form with an `aria-label`.
- **Relative times** keep the exact time in `title` and `dateTime`.
- **Headings:** one `h1` per page; the home's sections are `h2`.

## 7. Testing

- **Backend route tests** for the overview's counts, run as different callers so `unresolved_mine` differs, and under another org (404).
- **The list's new filters:** every combination with both sorts, the 422s, and `ai_status` from the latest analysis.
- **Uploads:** the findings count and worst severity.
- **The OpenAPI contract** is regenerated and checked.
- **Frontend tests:**
  - the home: assignments merged across organizations, the "N more" link, the empty state, the cards and their links;
  - the panel: counts, links, quick views and the dimmed zeros;
  - the filters: the Unresolved default, `assignee`, `new`, and values that aren't real;
  - the rows' new details, the strip's verdicts, the uploads column, and `<Ago>`'s wording at each threshold.
- **Screenshots** of every changed page at desktop and phone width before the PR.
