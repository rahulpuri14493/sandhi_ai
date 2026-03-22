# Output contracts — guide for everyone

This guide explains **output contracts** in Sandhi AI: what they are, when you use them, and how to read the settings without being a developer.

---

## What is an “output contract”?

When a workflow **step** finishes, the AI produces a **result** (text, structured data, tables, etc.). An **output contract** is a **written agreement** you attach to a job that answers:

- **Where** should a **copy** of that result be sent automatically (for example: a database table, a folder in cloud storage)?
- **How strict** should the system be if one of those destinations fails?

Think of it as **delivery instructions** for the platform: “After each step completes, send the output **here** and **here**, and follow these rules if something goes wrong.”

The contract is stored as **JSON** (a structured text format). You can paste it in the job’s **output contract** field or get a starting template from the API.

---

## Who this is for

| If you want… | What to focus on |
|--------------|------------------|
| Results **only on screen** in the app | [Write execution mode: UI only](#write-execution-mode-how-results-leave-the-step) — no contract destinations required. |
| Results **saved to your database** or **files in storage** | [Write execution mode: Platform](#write-execution-mode-how-results-leave-the-step) + [Write targets](#write-targets-where-copies-go). |
| The **AI** to call tools itself to save data | [Write execution mode: Agent](#write-execution-mode-how-results-leave-the-step). |

---

## Write execution mode — how results “leave” the step

This setting is **not** inside the JSON contract; it is a **job setting** (often shown as **Write execution mode** in the workflow builder). It controls **who** saves the step output and **whether** automatic delivery runs.

### Platform

- The **Sandhi platform** takes the step’s output and, if your contract lists **write targets**, **automatically** sends copies to those places (database, MinIO/S3, etc.).
- Best when you want **predictable, repeatable** delivery every time a step completes.

### Agent

- The platform still **stores** the step result for the app, but **does not** automatically run the contract’s “send to database / storage” actions in the same way as Platform mode.
- The **AI agent** is expected to use **tools** (e.g. run SQL or upload files) when **it** decides to—suited for more **interactive** or **manual** tool use.

### UI only

- The result appears **in the application** (on the job / step screen) and is stored **inside the product database** for display.
- The platform **does not** write a separate **artifact file** to cloud/local file storage for that step, and **does not** run **write targets** from the contract.
- Best when you **only need to see the answer** in the UI and **do not** need automatic copies elsewhere.

> **Important:** For **UI only**, set the job to **UI only**. Having a contract with empty targets is optional documentation; it does not by itself turn off file or database delivery unless the mode is **UI only**.

---

## Parts of the contract (the JSON)

A typical contract has these sections:

### `version`

A simple version label (usually `"1.0"`). It helps the product know which format you are using.

### `record_schema`

A **human-readable description** of what each row or record is supposed to contain (field names and types in plain language).

- This helps **you** and **the AI** agree on the shape of the data.
- It is **documentation**; the system still uses the **actual keys** the model returns when inserting into databases or files.

### `write_policy`

Rules for **automatic** delivery when **Platform** mode runs **write targets**:

| Field | Plain meaning |
|-------|----------------|
| **`on_write_error`** | **`fail_job`**: If one destination fails, treat the step as failed (strict). **`continue`**: Try other destinations even if one fails (more forgiving). |
| **`min_successful_targets`** | The **minimum number of destinations** that must succeed. For example, if you have two targets and set this to `1`, one successful write may be enough. If you require **all** destinations to succeed, this number should match how many targets you listed. |

If you have **no** write targets (empty list), these rules mainly apply only when you add targets later—there is nothing to “succeed” or “fail” for delivery.

### `write_targets`

A **list of destinations**. Each item says:

- **Which connected tool** to use (by its platform name, e.g. `platform_2_local_postgres`).
- **What operation** to perform (for example append rows or upsert).
- **Where** exactly to send data (table name, bucket, folder prefix, etc.).

You can have **one** destination or **several** (e.g. copy to a database **and** to a folder in MinIO).

---

## Write targets — where copies go

Each target is one **destination**. Common types:

### Databases (PostgreSQL, MySQL, Snowflake, etc.)

- You specify **schema**, **table**, and sometimes **database** name.
- Rows are built from the step’s structured output (usually a list of **records**).
- **Upsert** means “insert new rows, or update if a key already exists” (you define which columns are the **keys**).

**Optional (PostgreSQL, advanced):**

- **`bootstrap_sql`**: SQL run **once before** inserting rows—often `CREATE TABLE IF NOT EXISTS …` so the table exists the first time.
- **`column_mapping`**: Maps names from the AI output to real column names—for example, the model sends `content` but your table column is `result_json`.
- **`jsonb_columns`** (PostgreSQL): List column names that are **`JSON` / `JSONB`**. If the model sends **plain text** (a sentence) into that column, the platform wraps it so Postgres accepts it as a JSON string. Without this, a long text value can error with “invalid input syntax for type json.”

**`records` wrapper:** If the step output is a single JSON object like `{"records": [ {...}, {...} ]}` (common when the agent returns structured rows), the platform unwraps that list when loading the artifact so **`merge_keys`** match the **inner** row fields (e.g. `job_creation_date`). This applies especially when **`output_artifact_format`** is **`json`** (one file) or a single JSONL line containing `records`.

**OpenAI / A2A adapter path:** The executor often receives only `{"content": "<assistant message>"}`. If that message is JSON with a **`records`** array, the backend **normalizes** it to a top-level `{"records":[...]}` before writing the artifact file, so contract loads and the UI stay aligned. Models often wrap that JSON in a **markdown code fence** (json code block); both the backend and the platform MCP parser strip the fence before reading `records`.

### Object storage (MinIO, S3, etc.)

- You specify **bucket** and often a **prefix** (folder path, e.g. `reports/sales`).
- The platform writes a file derived from the step output into that location.

### “UI only” jobs

- You can use a **minimal contract** with an **empty** `write_targets` list, or leave the contract empty, depending on your workflow screen.
- **Delivery** is still governed by **UI only** mode; the contract then acts as **notes** or **record_schema** only.

---

## Example: UI only (show results in the app only)

Set the job’s **write execution mode** to **UI only**. You can use a simple contract like this to document what you expect:

```json
{
  "version": "1.0",
  "description": "Results appear in the app only; no automatic copies to database or storage from this contract.",
  "record_schema": {
    "summary": "string",
    "details": "object",
    "completed_at": "iso_datetime"
  },
  "write_policy": {
    "on_write_error": "continue",
    "min_successful_targets": 0
  },
  "write_targets": []
}
```

Adjust `record_schema` to match what you want the AI to return.

---

## Example: Platform — copy to a PostgreSQL table

Use **Platform** mode and replace the tool name with **your** tool’s name from the MCP tools list.

```json
{
  "version": "1.0",
  "record_schema": {
    "run_id": "uuid",
    "result_json": "object",
    "computed_at": "iso_datetime"
  },
  "write_policy": {
    "on_write_error": "fail_job",
    "min_successful_targets": 1
  },
  "write_targets": [
    {
      "tool_name": "platform_2_local_postgres",
      "operation_type": "append",
      "write_mode": "append",
      "target": {
        "schema": "public",
        "table": "job_pipeline_output",
        "bootstrap_sql": "CREATE TABLE IF NOT EXISTS public.job_pipeline_output ( run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), result_json JSONB NOT NULL, computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW() );",
        "column_mapping": {
          "content": "result_json"
        },
        "jsonb_columns": ["result_json"]
      }
    }
  ]
}
```

- **`bootstrap_sql`**: runs before inserts so the table exists (safe to run again with `IF NOT EXISTS`).
- **`column_mapping`**: use when the AI returns a field named `content` but the column is `result_json`.
- **`jsonb_columns`**: required when that column is `JSONB` and the model may send **plain text** instead of already-valid JSON.

### Blocking ad-hoc SQL writes (interactive Postgres tool)

The **interactive** Postgres tool (free-form `query`) can run any SQL the model sends. To allow **only reads** (`SELECT` / read-only `WITH`) and force **writes** to go through the **output contract** instead, set on the **platform MCP server**:

- Environment variable: `MCP_POSTGRES_INTERACTIVE_READONLY=1` (or `true`), **or**
- On the tool’s JSON config: `"interactive_readonly": true`

Then `INSERT` / `UPDATE` / `DELETE` / DDL from the interactive tool return an error pointing users to **output_contract** writes.

---

## Example: Platform — copy to MinIO / S3 folder

```json
{
  "version": "1.0",
  "record_schema": {
    "job_revenue": "number",
    "queried_at": "iso_datetime"
  },
  "write_policy": {
    "on_write_error": "fail_job",
    "min_successful_targets": 1
  },
  "write_targets": [
    {
      "tool_name": "platform_1_local_minio",
      "operation_type": "upsert",
      "write_mode": "overwrite",
      "target": {
        "bucket": "sandhi-brd-docs",
        "prefix": "reports/job-revenue"
      }
    }
  ]
}
```

---

## Glossary

| Term | Simple meaning |
|------|----------------|
| **Artifact** | A **saved** copy of the step’s output (often a file) used when the platform sends data to tools. **UI only** skips creating that file. |
| **Write target** | One **destination** (one tool + one place) in the contract. |
| **Upsert** | Insert new data, or **update** if a matching key already exists. |
| **Append** | Add new rows without requiring a uniqueness rule (depends on table design). |
| **Merge keys** | Column names used to match existing rows for upserts (like an ID or natural key). |

---

## Getting a template from the product

Your deployment may expose:

- **`GET /api/output-contract/template`**

That returns a **starter** JSON (often with a Snowflake-style example). Replace names, tools, and tables with **your** resources.

---

## Summary

1. **Output contract** = optional delivery instructions + rules + optional documentation of record shape.
2. **Write execution mode** = **Platform** (automatic delivery), **Agent** (agent-driven tools), or **UI only** (show in app, no automatic file/contract delivery).
3. **Write policy** = how to behave when **multiple destinations** fail or partially succeed (**Platform** mode with write targets).
4. **Write targets** = list of where to send copies; each entry uses one **connected** tool and a **target** (table, bucket, prefix).

If you are unsure, start with **UI only** and no destinations until you need automatic copies to a database or storage—then switch to **Platform** and add **write targets** one at a time.
