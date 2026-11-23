# Data Quality Inspector

A local tool for checking AI-evaluation datasets, with a browser interface and a command-line interface. Upload CSV, TSV, JSON arrays, JSONL/NDJSON, or gzip-compressed versions of these formats; choose checks for multiple rating attributes; and review readable summaries with detailed JSON and CSV exports. Optional AI answer review compares the content of selected answers with explicit rating criteria using the OpenAI API.

The inspector checks explicit rules. It does not decide whether an answer is factually correct or deserves its rating.

## Open the app without a terminal

Open the downloaded project folder in Finder and double-click **Data Quality Inspector.app**. Python 3.11 or newer is required. The app starts the Inspector and opens it in your default browser. Finder may display the app as **Data Quality Inspector**. Keep it in the project folder alongside `launch_gui.py` and `dqi/`.

If you opened the HTML preview, its **Open inspector** button uses the app’s most recently recorded local address. If the Inspector is closed, double-click the app in Finder to start it.

For easy access next time, click **Add desktop shortcut** in the app's top bar. It creates **Data Quality Inspector** on your Mac's Desktop. Double-click that shortcut to start or reopen the inspector, even when its local service is stopped. The button changes to **Desktop shortcut added** after creation and stays that way when you reopen the app. Existing unrelated Desktop items are never replaced. Keep the original project folder in place; the shortcut points to its app.

1. Choose a dataset or drag it onto the upload area. CSV, TSV, JSON, JSONL, NDJSON, and their `.gz` versions are supported. Files can be up to 25 MB, with a 100 MB limit after decompression. To explore first, click **Load demo data** for six synthetic records with deliberate issues.
2. Preview the records and choose a preset. **HelpSteer2 · five attributes** checks helpfulness, correctness, coherence, complexity, and verbosity together. You can also upload your own rubric and match its criteria to your score columns. Adjust the source fields, required values, ID uniqueness, numeric rules, whole-number rules, and minimum/maximum ratings as needed. Use **Add rating field** to inspect another attribute.
3. Keep **Include audit** selected to inspect exact repeated question–answer pairs, different ratings for identical pairs, and source ratings meeting your review thresholds. Select a question column for pair comparisons. Category coverage is optional and requires a category column and your own minimum/expected labels.
4. Click **Run inspection**. Under **Report view**, **Simple** shows the outcome and highlights. Choose **Detailed** for checks, score distributions, and individual records. In **Records to review**, filter by **Reason**, **Attribute**, or both. Use **Next** and **Previous** to browse 50 matches at a time. Expand a record to see its original question, answer, ratings, and suggested next steps.
5. With a filter selected, **Download matching records (CSV)** saves every match across all pages, including each record’s reasons for review. **Download full review queue** saves every flagged record. Use **Edit checks** to adjust the inspection rules and run again.
6. Click **Download report bundle** to keep the complete report as a ZIP. It contains `summary.html`, `summary.txt`, `report.json`, `findings.csv`, and `skipped-checks.csv`, plus `review-queue.csv` when the deeper audit is included. Extract the ZIP and open `summary.html` whenever you want to review the report again.

Data validation happens on this Mac through a local address, usually `http://127.0.0.1:8765`. Original files stay unchanged. Uploaded copies and generated reports are held in a temporary local folder, not saved to the project's `reports/` directory. Rerunning replaces the previous report for that upload; the session retains up to eight uploads and removes older ones. Download reports you want to keep.

Use **Close inspector** to stop the local service and clear its temporary session after downloading any reports you want to keep. Closing only the browser tab leaves the local service running. Double-click the app again to reopen it. The launcher reuses an existing inspector or chooses another local port if needed. Startup details are recorded in `runtime/server.log` if the browser does not open.

## Deeper dataset audit

The dataset supplies records and existing ratings. The audit checks those records against the configured rules. A custom rubric can supply the numeric scales for validation and the criteria for an optional AI review of the answers.

- **Exact repeated pairs:** compares the full, unchanged question and response together. A repeated question with a different response is not a duplicate. Identical pairs are review candidates because separate annotations may be intentional.
- **Conflicting source ratings:** finds different valid scores for the same exact pair and attribute. The report links the related records and suggests checking rubric versions and annotation provenance. It does not decide which score is correct or calculate reviewer agreement.
- **Review thresholds:** queues a record when a valid source score is at or below the user-selected threshold. HelpSteer2 starts with `≤ 1` for helpfulness, correctness, and coherence. These are editable inspector suggestions, not NVIDIA pass/fail rules. Complexity and verbosity have no automatic review thresholds; higher values are not universally better. Unfamiliar rating fields require the user to select a threshold explicitly.
- **Category coverage:** counts exact text labels against a selected minimum. Expected labels identify categories entirely absent from the file. Without expected labels, only observed categories are assessed. The suggested minimum of 20 is editable and is not a statistical adequacy standard. Missing and non-text category values are reported separately; topics and demographic coverage are not inferred from answers.

**Data checks passed** means the selected structural rules passed. A report may still contain records to review or category gaps. The review queue combines validation errors, skipped checks, and review candidates into one entry per record; multiple reasons do not inflate the record count. Higher source-score examples show existing labels, not verified good answers. Checks lacking required evidence are marked as unavailable or not selected. The data audit does not perform semantic answer grading, inter-annotator agreement, train/test overlap analysis, or near-duplicate matching. Optional model-assisted answer review is described below.

The in-app queue shows up to 50 matching records and coverage shows up to 100 categories. The standalone HTML/text summary previews 20 review records; the review CSV and JSON retain the complete queue and original context. Source files are never rewritten. The deeper audit is configured in the browser interface; existing CLI validation behavior is unchanged.

## Settings and walkthrough

Under **Settings → Appearance**, choose **Dark**, **Light**, or **Follow my Mac**. The Inspector starts with your Mac’s appearance and remembers your choice in this browser when you reopen the app. HTML reports opened from the Inspector use the same theme. HTML files in downloaded bundles follow the reader’s system appearance.

To reuse an OpenAI API key on this Mac, open **Settings**, enter the key, and click **Save key**. The Inspector stores it in macOS Keychain and selects it by default for dataset answer reviews, single-task evaluations, and second opinions. You can use a one-time key for a review, replace the saved key, or remove it from this Mac. Closing the Inspector clears temporary reports. The saved key stays in Keychain for your next session.

Open **Walkthrough** in the header for examples and instructions on pasting one task, uploading a dataset, matching a rubric, choosing checks, reviewing individual findings, and downloading results. **Back to my work** returns to the page you were using.

For an interactive introduction, choose **Start guided tour** on the upload screen or the Walkthrough page. A spotlight follows the controls from file upload through an example inspection report, a single-task evaluation, and Settings. Choose **Next** or **Back**, or use the right and left arrow keys. **Skip tour**, **Escape**, and **Finish tour** return you to your starting point. The tour runs locally with sample records and uses no API credits.

## Review one task

Open **Review one task** from the upload screen. Paste the original prompt and the AI response. If a criterion depends on a policy, reference answer, or source passage, add that text as the optional reference.

Upload a scoring rubric in CSV or JSON for several criteria, or TXT or Markdown for one criterion. The reviewer uses the names and score meanings in the rubric directly. Read the criteria and confirm the scale for each one, then use the local preview. The [CSV template](dqi/static/rubric-template.csv) gives an example with separate resolution and tone scores.

To run the evaluation, choose your reviewer, use your saved key or enter a one-time key, and confirm sending this task to OpenAI. The results show a model score, its reasoning, and supporting excerpts per criterion. The reviewer can flag insufficient evidence. You can read the results on the same screen or download them.

## Upload a custom rubric

1. Choose your response dataset and its rubric. You can pick the rubric first on **Upload file** or add it after the dataset under **Choose checks**. For example, a customer support file might have `customer_question`, `agent_reply`, `resolution_score`, and `tone_score` columns. Select the question and reply columns for their usual roles.
2. Each criterion should explain what the field measures and what each score means. For a multi-field example, use the [CSV template](dqi/static/rubric-template.csv) or [JSON template](dqi/static/rubric-template.json).
3. Review the suggested column for each criterion. If your dataset calls its tone column `courtesy_rating`, choose that column for the template’s `tone_score` criterion. Check the scale for each match, apply the mapping, then run the inspection.
4. To have a model read selected answers, open **Does the rating fit the answer?** in the report. The matched criteria are filled in, and you can edit the wording before starting the review.

| Rubric file | Contents |
| --- | --- |
| CSV | One row per criterion, with `field` and `description` columns. Quote descriptions that contain commas or line breaks. |
| JSON | An object with a `rubrics` array. Each entry has a `field` and a `description`. |
| TXT or Markdown | The entire file describes one criterion. The app uses the filename to suggest a score column. You can change that match. |

For CSV and JSON, each criterion can also supply `min`, `max`, and `integer` together. Set `integer` to `true` for whole numbers or `false` to allow fractional scores. When the rubric omits the scale, the app previews the column's configured range or proposes 1–5 for a column without a range. Rubric files are read locally, can contain up to 12 criteria, and have a 256 KiB upload limit.

## Optional AI answer review

After running a data inspection, open **Detailed → Does the rating fit the answer?** in the live Inspector.

1. Select the question and answer columns, and optionally a column containing trusted reference answers or evidence.
2. Choose **Whole dataset** to include every record, or **Selected records** for a batch of 1–20. Selected mode starts with the first five records. You can also choose records from the audit queue or enter specific record numbers.
3. Review the rating criteria for each selected field. A matched custom rubric supplies its descriptions. You can also enter your own wording or use the HelpSteer2 preset’s adaptations of its published guidelines for the five 0–4 attributes. Change numeric scales and whole-number settings through **Edit checks** before running the review. [HelpSteer2 annotation guidelines](https://arxiv.org/html/2406.08673v1#A7.SS3.SSS1)
4. Choose the reviewer and click **Estimate review cost**. This runs locally without an API key. The preview shows the selected criteria, eligible API requests, skipped records, estimated USD cost, and a second estimate assuming maximum output. Open **How the cost was estimated** for rates and token assumptions. The built-in Sol and Astra prices use OpenAI Standard rates, dated in the preview. For another model ID, enter its input and output prices per million tokens. Actual charges depend on usage. [OpenAI pricing](https://developers.openai.com/api/docs/pricing)
5. To proceed, select your saved key or enter a one-time key, check the authorization box and click **Confirm and start review**. Changing the scope, selection, criteria, model, or prices requires another estimate and confirmation. The Inspector sends one request per eligible record. Records missing the required text or exceeding 40,000 combined question, answer and reference characters are skipped.
6. Review the explanation, exact source excerpts, original rating, and the selected model’s suggested score. Download the answer-review bundle to keep HTML, JSON, and CSV copies.

For a second opinion, expand an assessed record in the running app. Choose a different reviewer and confirm the single-record review with your saved or one-time key. The second reviewer receives the same source text and rating criteria. The comparison links each model’s name to its scores and reasons. You can download it separately and return to the primary report.

The model receives the selected questions, answers, optional references, and rubric. Existing rating fields are withheld from the model and compared locally afterward. A matching suggested score is **Supported by this reviewer**, a difference within your chosen tolerance is **Close**, and a larger difference is **Questionable**. Unsupported scores remain **Insufficient evidence**; invalid original ratings are marked as not comparable. Quote validation checks that an excerpt occurs in the source, not that the model's interpretation is correct. No automatic label changes occur.

There is no web search or code execution in this review. Factual claims that need unavailable evidence should remain undecided. Human adjudication and calibration against reviewed examples are still needed before relying on the judgments for important decisions. Complexity and verbosity measure style; higher values are not inherently better.

Reviewer rules version 2 judge only the delivered answer in its current conversation context. Promised code or explanations that are missing do not count as completed work. Each score must connect a specific excerpt to its rating anchor, with uncertainty explained when adjacent anchors are difficult to distinguish. The complexity criteria distinguish language sophistication from topic difficulty: a few product names or technical terms do not by themselves establish advanced language, and a short response can still be complex when its actual concepts and wording require advanced knowledge. No length-based or missing-code score cap is applied. These are model instructions, not a guarantee that the model will obey them.

Review exports retain the exact reviewer instructions and their SHA-256 fingerprint, as well as the rubric version and score criteria. The worker uses the instructions frozen when the review plan was created. Reopen the app launcher to use version 0.15.0 if an older local server is still running.

API calls use a fixed OpenAI endpoint, `store: false`, no automatic retries, and at most 8,000 output tokens per request (including reasoning). Actual credit use depends on content and model pricing. One-time keys are held in memory for the review. Saved keys are retrieved by the local service from macOS Keychain when you authorize a review. The browser receives only the saved-key status. Reports and application logs omit credentials.

One answer-review batch runs at a time. **Stop after current answer** prevents the next request; an in-flight request may finish and incur charges. Credit, authentication, or provider errors stop further requests and retain completed results. Session request identifiers prevent duplicate batches after an interrupted start response. Reloading the browser restores the current or latest review while the local service remains running. Token totals are explicitly marked incomplete when the provider did not report all usage.

Verification includes mocked provider tests, server tests, and browser checks with clearly labeled synthetic responses. No live model-quality claim follows from those tests; a real API key and a calibrated sample are needed for live evaluation.

## Command-line use

Python 3.11+ is required. There are no third-party dependencies. Run commands from this project directory.

```sh
python3 -m dqi samples/evaluations-with-issues.csv --output-dir reports/my-first-run
python3 -m dqi samples/evaluations-clean.csv
python3 -m dqi samples/mapped-columns.csv --config samples/custom-rules.json
python3 -m dqi samples/evaluations-with-issues.jsonl --output-dir reports/jsonl-demo
python3 -m dqi samples/evaluations-clean.json
python3 -m unittest discover -s tests -v
```

The first command reports **6 records, 5 affected records, 5 findings, and 1 skipped range check**. The two occurrences of `A01` each receive a uniqueness finding. The text `good` receives a numeric finding, and its range check is explicitly recorded as skipped. The clean and mapped examples pass.

A generated example report is included in `reports/example/`.

The evaluation files under `samples/` are synthetic fixtures, not human-reviewed research data. `samples/helpsteer2.json` and `samples/helpsteer2-helpfulness.json` are rules configurations, not datasets.

Exit codes: `0` = selected checks passed; `1` = findings present; `2` = input, configuration, or output error; `3` = empty dataset, no checks selected, or incomplete inspection.

## Input formats

| Extension | Expected contents |
| --- | --- |
| `.csv` | Comma-separated records with a header row |
| `.tsv` | Tab-separated records with a header row |
| `.jsonl`, `.ndjson` | One JSON object per nonblank line |
| `.json` | A top-level array of record objects |
| Any of the above plus `.gz` | The same format, gzip-compressed |

The filename determines the format, ignoring capitalization. Gzip files are read directly; no extraction or CSV conversion is needed. Use `--format jsonl` (or `csv`, `tsv`, `json`, `ndjson`) when a download has an unfamiliar extension. Gzip decompression still requires a final `.gz` extension.

JSON preserves strings, numbers, booleans, nulls, and extra structured fields. Active mapped fields must be scalar values; nested objects or arrays in a mapped field produce a clear error rather than being silently flattened. Wrapped JSON such as `{"records": [...]}` is not supported; supply the record array itself. JSON fractional numbers are parsed and reported with decimal precision intact.

JSON duplicate keys, nonstandard `NaN`/`Infinity` values, malformed records, invalid UTF-8, and incomplete gzip files produce input errors before any report is exported. JSONL blank lines are ignored. An empty JSON array or JSONL file is reported as `empty_dataset`.

## Run HelpSteer2 directly

Download the original root-level `validation.jsonl.gz` from [NVIDIA's HelpSteer2 dataset](https://huggingface.co/datasets/nvidia/HelpSteer2/blob/main/validation.jsonl.gz). If it is in your Downloads folder, run this from the project directory:

```sh
python3 -m dqi "$HOME/Downloads/validation.jsonl.gz" --preset helpsteer2 --output-dir reports/helpsteer2-all-attributes
```

If your browser decompressed it, use `validation.jsonl` in the same command.

Open `reports/helpsteer2-all-attributes/summary.html` in a browser, or read `summary.txt` in a text editor. If that directory already contains reports, add `--overwrite` to regenerate them. Input files remain protected.

The preset requires a response and checks **helpfulness, correctness, coherence, complexity, and verbosity** together. Each rating must be present, finite, a whole number, and within 0–4. ID uniqueness is disabled because this source has no evaluation-ID column. Shared response checks run once per record. The summary distinguishes records from rating values and reports score statistics and distributions separately for each attribute.

To customize the same rules, use `--config samples/helpsteer2.json` instead of `--preset helpsteer2`. These options are mutually exclusive. To reproduce the earlier single-attribute run, use `--config samples/helpsteer2-helpfulness.json` with a separate output directory.

These checks do not judge the correctness of the responses or human evaluations. Higher complexity or verbosity is not inherently better. The dataset itself is not bundled. See the [official dataset documentation](https://huggingface.co/datasets/nvidia/HelpSteer2/blob/main/README.md) for its annotation methods and CC-BY-4.0 attribution requirements.

## Preset and configuration

The default preset requires `evaluation_id`, `response`, and `rating`. IDs must be unique. Ratings must be finite numbers within the inclusive range 1–5. Decimal ratings are permitted. `explanation` is optional and its column may be absent.

Numeric values accept ASCII decimal or scientific notation with optional surrounding whitespace. Group separators, underscores, NaN, and infinity are not accepted.

`--config` accepts the JSON shape shown in `samples/custom-rules.json`. Omitted settings use defaults. Map semantic field names to existing headers or JSON property names; source fields are not renamed. Set `unique_id` or `numeric_rating` to `false` to disable those checks, set `rating_range` to `null` to disable the range check, or change the `required` list. Add `explanation` to that list to require it. Unknown settings and ambiguous active mappings are rejected.

Disabling numeric validation does not make nonnumeric values pass a range check: they appear under skipped checks, and an otherwise clean inspection is marked `incomplete`.

### Multiple rating fields

Use `ratings` to define rules directly against any number of source columns or JSON properties:

```json
{
  "required": ["response"],
  "unique_id": false,
  "ratings": {
    "helpfulness": {"required": true, "numeric": true, "integer": true, "range": [0, 4]},
    "correctness": {"required": true, "numeric": true, "integer": true, "range": [0, 4]}
  }
}
```

This replaces the legacy single-rating rules. Do not combine it with `numeric_rating`, `rating_range`, or semantic `rating` in the shared `required` list; configure requiredness on each rating instead. Other `columns` mappings still apply to shared fields such as `response`.

Each rating defaults to required, numeric, no integer restriction, and range 1–5 unless overridden. Set its `range` to `null` to disable that check. Fully disabled rating definitions are retained in configuration but are not inspected or summarized. Legacy single-rating configurations remain supported unchanged.

### Statistics and distributions

Statistics include only finite numeric scores satisfying all enabled rules for that attribute. A valid score still counts if another attribute or a shared response check fails on the same record. Missing and unusable values are excluded; optional missing values can be excluded without being findings. Failure categories may overlap, while each excluded score and affected record is counted once.

JSON contains per-attribute counts, valid-score minimum/maximum/mean, and exact score-frequency bins. Small bounded integer scales include zero-count bins. Means are rounded to six decimal places using half-up rounding; readable reports display two decimals. Extremely large numeric scales may yield an explicitly unavailable mean rather than a fabricated value. No combined quality score is calculated.

## Report behavior

- `summary.html` opens in **Simple** view with the inspection outcome and main findings. Select **Detailed** at the top of the file for per-attribute checks, score distributions, and record evidence. The switch works offline. `summary.txt` is the plain-text report.
- `report.json` includes format, status, counts, applied rules, per-attribute summaries, findings, and skipped checks, with original values and JSON types preserved.
- `findings.csv` and `skipped-checks.csv` contain record number, source line, field, value, value type, field-presence flag, rule, reason, and related records. They remain CSV regardless of the input format.
- Record numbers are one-based data-record positions. CSV/TSV exclude the header and entirely blank rows; JSONL excludes blank lines. Source lines refer to the physical start of delimited records or the original JSONL line. For JSON arrays, the record number is authoritative and source line is null (blank in CSV reports).
- Missing JSON properties, explicit nulls, and whitespace-only strings fail required checks. `field_present` distinguishes an absent property from a present null. Numeric zero and boolean false are present values, but booleans fail numeric validation. Missing values get no additional numeric or range findings; optional blanks/nulls are allowed.
- JSON headers are the union of observed property names. A mapped field absent from every record is a mapping error; an individual record missing an otherwise available required field gets a finding. Empty datasets have an explicit status.
- String IDs use exact matching; they are not silently trimmed or case-normalized. JSON number `1`, string `"1"`, and boolean `true` are distinct IDs; numeric `1` and `1.0` are the same ID. Missing or blank IDs are excluded from duplicate groups.
- CSV/TSV parsing accepts UTF-8 with or without a BOM. Duplicate or blank headers, unequal row widths, invalid encoding, and invalid quoting produce input errors before inspection.
- The source file is read-only. Existing reports are replaced only with `--overwrite`; neither the input dataset nor configuration can be used as an export destination, including through file aliases.
- CSV export prefixes potentially executable spreadsheet formulas with an apostrophe and displays unpaired Unicode surrogates as backslash escapes. JSON preserves the exact original string values using JSON escapes.

## Scope and limits

The browser interface and CLI use the same validation engine. The current tool reads one file into memory. XLSX, Parquet, arbitrary nested-field mapping, streaming large-file processing, and a persistent report history are outside this version.
