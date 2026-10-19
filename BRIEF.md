# Order Desk: Claude Code build brief

19 Oct 2026 · @rich

## What you are building

Order Desk is an internal web dashboard that turns wholesale customers' messy orders into exact, ready-to-import sales orders, in each client's own format. One operator (Rich) uses it to run a done-for-you order processing service. Clients never log in.

The app has four jobs:

1. **Store each client's setup:** product catalogue, customer list, output template and rules.
2. **Read orders:** turn an uploaded or pasted order into a structured order, matched to that client's product codes and customer accounts.
3. **Support review:** let the operator check, correct and approve every order, and learn from every correction.
4. **Export and measure:** download approved orders in the client's format, and measure accuracy against what the client's own staff entered during a shadow trial.

The wireframe canvas "Order desk dashboard wireframe" (5 screens) is the visual reference. Where this brief and the wireframe disagree, this brief wins.

**The one rule above all others: the system must never mark an order Ready when it is wrong.** Being unsure and flagging an order for review is fine. Being confidently wrong is the failure that loses clients. Every design choice below serves that rule.

How to use this brief: export this doc as Markdown, save it as BRIEF.md in an empty project folder, open Claude Code in that folder, and paste the first prompt from the last section. Build one milestone at a time, never everything at once.

## Words used in this brief

Use these exact terms in code, database names and the interface, so nobody confuses the client with the client's customer.

| Term | Meaning | Example |
|---|---|---|
| Client | The wholesaler the service works for | ABC Cleaning Supplies |
| Customer | The client's customer who places an order | Riverside Hotel |
| Order | One incoming message or file from a customer: a header plus lines | Email "Order for Thursday" |
| Line | One requested product inside an order | "120 bottles of the antibac spray" |
| Catalogue | The client's product list: codes, descriptions, units, pack sizes, prices | abc_products.xlsx |
| Customer list | The client's customers: account codes, names, emails, addresses, price band | abc_customers.csv |
| Output template | An example file from the client's system showing the exact columns an imported order needs | SalesOrderImport_example.csv |
| Mapping | Which column in a client's file means which internal field | "Stock Code" means product code |
| Alias | A saved link between how one customer phrases something and the correct product code, created when the operator confirms or corrects a match | Riverside Hotel "blue roll 2ply" = TOW-310 |
| Staff entries | An export of what the client's own staff entered, used as the answer key in a shadow trial | staff_orders_12to16oct.csv |
| Shadow trial | The client's team keeps working as normal; orders are processed in parallel and never entered into their system | 10 working days |
| Initial status | The status the pipeline gave an order before any human touched it. Used to measure "confidently wrong" | Ready |

Order statuses, used everywhere:

- **Ready** (green): everything matched by a deterministic method and every check passed. Still needs one click to approve.
- **Check** (amber): probably right, but something needs a human look (an AI suggested match, a price difference, a possible duplicate).
- **Problem** (red): cannot be completed without a human (unknown product, unknown customer, quantity that does not convert).
- **Pass on** (grey): not a new order (a delivery chase, an amendment, a question). Goes to the client contact.
- **Approved** (blue): the operator approved it. Included in the next export.
- **Exported:** included in a downloaded export file.

## Scope

Version one is a local tool for one operator that can run a real shadow trial end to end. Anything not listed under version one is out of scope until asked for.

Version one includes:

- Several clients, each with fully separate data.
- A setup wizard: upload catalogue, customer list and output template, check column guesses, set rules.
- Order inputs: pasted email text, .txt, .eml email files, text-based .pdf, .xlsx and .csv. Several files at once.
- The full processing pipeline: classify, extract, match customer, match products, convert units, run checks, set status.
- A review screen with corrections, learned aliases and approve.
- Export of approved orders in each client's format, one order or a batch.
- Shadow trial comparison against staff entries, plus a printable client report.
- A demo client with 20 test orders and answer keys, and an accuracy test command.
- Logging of AI cost, processing time and review time per order.
- A single password login.

Later, not in version one:

- Auto-forwarding: a mailbox per client polled every few minutes. In version one, the forwarding panel on the queue screen shows "Not connected yet". Design the data model so an order can have source = email_forward later.
- Scanned PDFs, photos, handwriting, voicemail and WhatsApp. A PDF with no text layer is marked Problem: "Scanned PDF, not supported yet".
- Direct connections into a client's system (Sage, Unleashed and so on). Version one only produces files.
- Processing amendments and cancellations. Version one detects them and marks them Pass on.
- Client logins, billing, several operators, hosting on a server.

## Tech stack and project setup

Python throughout, because the parsing and fuzzy matching libraries are strongest there. Pages are rendered on the server, which keeps a one-operator tool simple. It runs on Rich's laptop at http://127.0.0.1:8000.

| Part | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Best libraries for files and matching |
| Web app | FastAPI, Jinja2 templates, HTMX for in-page updates | No separate frontend build to maintain |
| Styling | Plain CSS with variables (palette in the Screens section) | Matches the wireframe exactly |
| Database | SQLite through SQLModel, one file at data/orderdesk.db | Zero setup, easy to back up |
| File storage | data/clients/<client_id>/ keeps every uploaded file as received | Originals are always available |
| PDF text | pdfplumber (text and tables) | Handles purchase order layouts |
| Excel and CSV | pandas with openpyxl; charset detection for CSV | Messy encodings are common |
| Email files | Python's built-in email package | Reads .eml body and attachments |
| Fuzzy matching | rapidfuzz | Fast candidate search over catalogues |
| AI | Official Anthropic Python SDK. Model from the ANTHROPIC_MODEL setting (default claude-sonnet-5-5). Structured output by tool use with a JSON schema, temperature 0 | Reliable structured output |
| Client report | A print-friendly HTML page; the browser saves it as PDF | No PDF library needed in version one |
| Tests | pytest | Standard |

Settings live in .env (never committed): ANTHROPIC_API_KEY, ANTHROPIC_MODEL, APP_PASSWORD, SECRET_KEY, DATA_DIR, AI_PRICE_INPUT_PER_MTOK, AI_PRICE_OUTPUT_PER_MTOK (prices in pounds per million tokens, used for cost logging; Rich fills these in from the current price list).

Project layout:

```
orderdesk/
  BRIEF.md              this document
  DECISIONS.md          assumptions Claude Code makes, one line each
  Makefile              run, test, eval, seed
  requirements.txt
  .env.example
  app/
    main.py             FastAPI app, routes, login
    models.py           database tables
    config.py
    web/                routes and Jinja2 templates per screen
    static/             css, htmx, small js
  pipeline/             pure functions, no web code
    ingest.py           file type detection and text extraction
    classify_extract.py AI call 1: classify and extract
    customers.py        customer matching
    products.py         aliases, exact matching, candidates, AI call 2
    units.py            unit conversion
    checks.py           price, duplicate, cutoff, minimum order
    status.py           status rules
    export.py           output template rendering
    compare.py          trial comparison
    ai.py               the only file that talks to the AI API
  eval/
    run.py              accuracy test command
  seed/
    demo_data.py        builds the demo client and its 20 test orders
  tests/
  data/                 git-ignored: database, client files, eval results
```

Commands: `make run` starts the app, `make test` runs pytest, `make seed` rebuilds the demo client, `make eval` runs the accuracy test.

## Data model

Every table except Client carries client_id, and every query filters by it, so one client's data can never appear in another client's screens or exports. Money is stored as decimals in pounds, dates as ISO dates, times in UTC and shown in Europe/London time.

```
Client
  id, name, erp_system, mode (demo | trial | live)
  contact_name, contact_email
  inbound_address (nullable, for later auto-forwarding)
  trial_start_date, trial_working_days (default 10)
  minutes_per_order (default 5, used for time-saved estimates)
  rules (JSON, see Rules below)
  unit_synonyms (JSON, see Processing pipeline step 6)
  created_at, archived_at

CatalogueItem
  client_id, product_code (unique per client), description
  sell_unit (e.g. CASE, BOX, EACH), pack_size (number of inner units, nullable)
  inner_unit (e.g. bottle, roll, nullable), price (nullable), barcode (nullable)
  extra (JSON: any other columns kept as uploaded)
  search_text (normalised description, code and barcode, used for matching)

PriceListEntry   (optional, when prices differ by customer band)
  client_id, price_band, product_code, price

Customer
  client_id, account_code (unique per client), name
  emails (list), email_domains (list), delivery_address, price_band
  extra (JSON)

ColumnMapping
  client_id, kind (catalogue | customers | output | staff_entries)
  source_filename, source_columns (list), mapping (JSON: their column to internal field)

OutputTemplate
  client_id, columns (ordered list of their column names)
  fill (JSON: their column to an internal field, a fixed value, or blank)
  date_format (e.g. %d/%m/%Y), decimal_places, delimiter, encoding, include_header
  layout: one row per line, header fields repeated on every row

Order
  id, client_id, source_type (paste | txt | eml | pdf | xlsx | csv | email_forward)
  original_filename, stored_path, raw_text, content_hash, received_at
  classification (new_order | amendment | cancellation | chase | quote_request | other)
  customer_id (nullable), customer_match_method (email | domain | name | operator)
  po_number, requested_delivery_date, requested_delivery_raw, delivery_address, delivery_notes
  initial_status (set once by the pipeline, never changed)
  status (current), flags (list of {code, message, severity})
  ai_input_tokens, ai_output_tokens, ai_cost_gbp, processing_ms
  review_started_at, approved_at, review_seconds, corrections_count
  export_batch_id (nullable), exported_at

OrderLine
  order_id, line_no
  raw_text, source_span (exact substring of raw_text this line came from)
  requested_code, requested_description, requested_qty, requested_unit, requested_price
  product_code (nullable), match_method (alias | exact_code | exact_name | ai_suggested | operator)
  candidates (list of up to 5 {product_code, description, score})
  qty, unit (final, in the client's sell unit), conversion_note (e.g. 120 bottles / 12 = 10 cases)
  unit_price, status, flags, corrected_by_operator

Alias
  client_id, customer_id (nullable means any customer of this client, never auto-applied)
  phrase (normalised), product_code, unit_hint (nullable)
  created_from_order_id, times_used, last_used_at

ExportBatch
  id, client_id, created_at, file_path, order_ids, row_count

StaffEntryUpload
  id, client_id, filename, uploaded_at, mapping_id, row_count
StaffEntryLine
  upload_id, po_number, account_code, product_code, qty, unit, unit_price, delivery_date

Comparison
  client_id, order_id (nullable), staff_po_number
  result (exact | different | only_ours | only_theirs)
  differences (list of {field, ours, theirs})
  cause_note, fix_tag (rule_added | remembered | ask_client | our_error | staff_error)

Event
  ts, client_id, order_id (nullable), type, details (JSON)
  (every upload, AI call, correction, approval and export is logged here)
```

Rules (stored on Client.rules): cutoff_time (e.g. 15:00), min_order_value, flag_price_differences (default true, tolerance 1p), duplicate_window_days (default 7), convert_units (default true), allow_substitutions (default false), require_po_number (default false), allow_ai_ready (default false, explained in the pipeline).

## The standard internal order

Every order, from every client and every file type, becomes this one shape before anything else happens. The export step is the only code that knows about a client's own format. This is what lets one engine serve any number of clients.

```json
{
  "client_id": "abc",
  "order_id": 1047,
  "classification": "new_order",
  "customer": {
    "account_code": "CUST-018",
    "name": "Riverside Hotel",
    "match_method": "email"
  },
  "po_number": "2291",
  "received_at": "2026-10-19T14:42:00Z",
  "requested_delivery_date": "2026-10-22",
  "requested_delivery_raw": "Thursday",
  "delivery_address": "14 River Walk",
  "delivery_notes": "Kitchen entrance",
  "lines": [
    {
      "line_no": 1,
      "source_span": "120 bottles of the antibac spray",
      "product_code": "SPR-12",
      "description": "Antibacterial spray 750ml, case of 12",
      "qty": 10,
      "unit": "CASE",
      "conversion_note": "120 bottles / 12 = 10 cases",
      "unit_price": "28.00",
      "match_method": "alias",
      "status": "ready"
    }
  ],
  "status": "problem",
  "flags": [
    {"code": "line_unmatched", "message": "1 line could not be matched", "severity": "problem"}
  ]
}
```

Rules for this shape:

- Anything not found is null. Never fill a gap with a guess.
- Quantities are numbers in the client's sell unit after conversion. The customer's original wording is always kept in source_span.
- Money is a decimal string in pounds. Dates are ISO dates. The customer's raw wording for a date is kept beside the resolved date.
- Demo answer keys (see the demo section) are written in exactly this shape, so the accuracy test can compare like with like.

## Processing pipeline

Each order runs through nine steps in order. The AI does reading and choosing from a short list; code does everything that must be exact: matching known phrases, arithmetic, checks and status. Each step is a pure function in pipeline/ with its own tests.

1. **Ingest.** Detect the file type and extract plain text.
   - Pasted text and .txt: as is.
   - .eml: the plain text body (or HTML converted to text), plus any PDF, Excel or CSV attachments processed as part of the same order.
   - .pdf: text and tables with pdfplumber. If a page yields under 20 characters of text, mark the order Problem: "Scanned PDF, not supported yet".
   - .xlsx and .csv: read every row, detect the header row, and turn the sheet into readable text rows ("Column: value" per cell) so the AI sees the structure.
   - Save the original file, the extracted raw_text and a content_hash (SHA-256 of normalised text).
2. **Classify and extract (AI call 1).** One call that returns both the classification and the extracted order, using tool use with a strict JSON schema. The prompt must say:
   - Return only what is written. Never invent product codes, prices or quantities.
   - For every line, return source_span: the exact substring of the text it came from. Code checks that each span really appears in raw_text; if not, the line is Problem.
   - Return numbers and units as written ("120", "bottles"). Do not convert.
   - Return relative dates as written ("Thursday"). Code resolves them against received_at in Europe/London time.
   - Classification is one of: new_order, amendment, cancellation, chase, quote_request, other, with a one-line reason.
   - If one message holds orders for several delivery addresses or several POs, say so with a flag; version one marks it Problem.
   - Anything other than new_order becomes Pass on, with the reason shown to the operator.
3. **Match the customer.** Deterministic first, in this order:
   - Sender email exactly in a customer's emails: confident.
   - Sender domain matches exactly one customer's email_domains: confident.
   - Customer name in the text matches one customer name with rapidfuzz score of 92 or more, and no other above 85: Check.
   - Anything else: Problem, "Unknown customer", with the top 3 name matches offered in a dropdown.
4. **Match products, line by line.** Stop at the first rule that succeeds:
   1. Alias for this customer: the normalised line text (lowercase, plurals and punctuation stripped, numbers removed) equals a saved alias for this customer. Method alias.
   2. Exact code: a product code or barcode written in the line exists in the catalogue. Method exact_code.
   3. Exact name: the normalised description equals one catalogue item's normalised description. Method exact_name.
   4. Candidates: rapidfuzz token_set_ratio over search_text gives the top 10 catalogue items. Aliases from other customers of the same client are added as extra candidates, never applied automatically.
   5. AI choice (AI call 2, one call per order for all remaining lines): send each line's text, the customer name and its 10 candidates (code, description, unit, pack size only). The AI returns one code from the list or null, plus a one-line reason and whether it saw real ambiguity. Code rejects any code not in that line's candidate list. Method ai_suggested.
   - Never send the whole catalogue or customer list to the AI. Only the candidates.
   - Save the top 5 candidates on the line, for the review dropdown.
5. **The allow_ai_ready setting.** By default, a line matched by ai_suggested is Check, never Ready, even when the AI is sure. Ready is reserved for alias, exact code and exact name. Once the accuracy test shows zero confidently wrong orders over enough real orders, the operator can turn this setting on for a client, and then an AI choice with fuzzy score of 90 or more and no ambiguity may be Ready.
6. **Convert units (code only, never the AI).** Each client has a unit synonyms table, editable in settings (defaults: case = cs = cases; box = bx = boxes; pack = pk = packs; each = ea = single = unit; plus the inner unit words found in the catalogue such as bottle and roll).
   - Requested unit equals the sell unit (or a synonym): quantity unchanged.
   - Requested unit equals the item's inner unit and the quantity divides exactly by pack_size: divide, and write conversion_note.
   - Divides with a remainder: Problem, "120 bottles is not a whole number of cases of 12".
   - No unit written: use the alias unit_hint if one exists, otherwise assume the sell unit and mark Check.
   - Unknown unit word: Problem.
7. **Run checks.** Each failed check adds a flag with a severity:

   | Check | Rule | Severity |
   |---|---|---|
   | Price | A price written on the order differs from the customer's price band (or the catalogue price) by more than 1p | Check |
   | Duplicate | Same customer and PO number within duplicate_window_days, or the same content_hash | Check |
   | Cutoff | Received after cutoff_time and the requested delivery is the next working day | Check |
   | Minimum order | Order value below min_order_value | Check |
   | PO number | Missing, when require_po_number is on | Check |
   | Delivery date | Resolved date is in the past | Problem |
   | Quantity outlier | More than 5 times this customer's previous largest quantity of this product | Check |

8. **Set status (code only).** The order's status is the worst of its parts, in the order Problem, Check, Ready. An order is Ready only if every one of these is true: classified new_order, customer matched confidently, every line Ready, and no flag at Check or above. Save the result as both initial_status and status.
9. **Log.** Write an Event with tokens, cost (using the price settings), processing time and the resulting status.

AI call rules: at most two calls per order. On an invalid response, retry once, then mark the order Problem: "Could not read this order". Every call goes through pipeline/ai.py, which logs tokens and cost and can cache responses by input hash so the accuracy test gives repeatable results.

**Learning from the operator:** whenever the operator picks or changes a product on the review screen and "Remember this" is ticked (ticked by default), save an Alias for that customer: the line's normalised text, the chosen product code and the unit used. Next time the same customer writes the same thing, step 4.1 matches it instantly and it shows as "Remembered phrasing".

## Screens

Five screens, matching the wireframe. Every page has the same top bar: "Order Desk" mark with an "Internal" tag, navigation (Clients, Orders, Trials, Add client), and the operator name. Every button and link is at least 44 px tall. Layouts wrap cleanly at narrow widths, and wide tables scroll inside their own box.

Palette and type (from the wireframe):

| Use | Value |
|---|---|
| Page background | #F6F6F4 |
| Cards | #FFFFFF, border #DADAD6, radius 12 px |
| Text / secondary text | #1C1C1A / #5C5C57 |
| Primary button | #23408E with white text |
| Ready | background #E3F1E7, text #1E6B3A |
| Check | background #FBEFD9, text #7A4A00 |
| Problem | background #F9E3E3, text #9B2525 |
| Pass on | background #EDEDEA, text #45453F |
| Approved | background #E6ECF8, text #23408E |
| Fonts | IBM Plex Sans for text, IBM Plex Mono for codes, PO numbers, file names and output previews |

Status is always shown as a word in a pill, never by colour alone.

### 1. Home: clients

- Four summary tiles: orders in today, waiting for your review (with the age of the oldest), exactly right over the last 7 days, and average review time per order. Demo clients are excluded from the totals.
- "Needs your attention": every Check, Problem and Pass on order across all clients, oldest first, each with its status, the issue in plain words, client, customer and PO, and a Review button that opens screen 4.
- Client cards: name, system, contact role, mode pill (Demo, Shadow trial, Live, Setting up), orders today, to review, exactly right, a footer line (trial day "6 of 10", or setup progress "3 of 5 steps done"), and a button (Open orders, or Continue setup).
- "Add client" button, top right.

### 2. Add a client (one time setup)

One page with five numbered sections and a step strip at the top showing which are done. Each section saves on its own, so setup can be finished over several visits.

1. **Client details:** name, their system (Sage 200, Sage 50, Unleashed, Pegasus Opera, Other), mode, main contact, contact email for problems, and the inbound address (shown read-only, "Not connected yet" in version one).
2. **Product catalogue:** upload Excel or CSV. Show the file name and row count, then a table of their columns with an example value and a dropdown for what each means (product code, description, unit, pack size, inner unit, price, barcode, ignore). Pre-select guesses using header names first ("Stock Code", "SKU", "Item" mean product code, and so on), and only ask the AI when headers are unclear. Block saving until product code and description are mapped. Warn about duplicate product codes and show them.
3. **Customer list:** the same pattern, for account code, name, order email, delivery address and price band. Split multiple emails in one cell. Derive email domains, ignoring public domains like gmail.com and outlook.com.
4. **Output format:** upload the example file. Show their columns in order, and for each a dropdown of what fills it (account code, PO number, date received, product code, quantity, unit, price, delivery date, delivery notes, a fixed value, or leave blank). Detect delimiter, date format and decimal style from the example and let the operator change them. Show a live preview of the demo order's rows in their format.
5. **Rules (optional):** cutoff time, minimum order value, and checkboxes for each rule in the data model. Also the unit synonyms table and minutes per order.

Bottom buttons: "Test with a past order" (opens the queue with a paste box) and "Save client".

### 3. Order queue for one client

- Header: breadcrumb, client name, mode pill with trial day, one line of setup facts, and tabs (Orders, Trial results, Settings).
- Step strip: three cards. 1 Upload orders (received today, how many forwarded and uploaded). 2 Review and approve (waiting, how many need a fix, "Start reviewing" opens the oldest unapproved order). 3 Export in the client's format (approved and ready, the file name, "Go to export").
- Step 1, upload box: "Upload customer orders". Drag and drop several files, a "Choose files" button, and "Paste an email" which opens a text box plus optional sender email and received time fields. Each file is processed in the background; its row appears straight away with a "Reading" state, then updates to its status without a page reload.
- Forwarding panel: in version one, "Auto forwarding not connected yet".
- Step 2, the table: arrived time, customer, PO number, came as, lines, status pill with a one-line reason, and a Review or Open button. Filter pills with counts: All, Ready, Check, Problem, Pass on, Approved. Default sort oldest first.
- Step 3, export bar: "Export: N approved orders ready", with a short explanation and "Download" which builds one file of every approved, not yet exported order, marks them Exported and records an ExportBatch. A link lists past export batches so any file can be downloaded again.

### 4. Review one order

- Header: breadcrumb, "PO 2291 · Riverside Hotel", status pill ("1 line needs you"), facts line (arrived time and type, read time, AI cost, position in queue), Back to queue, Next order.
- Left panel, what they sent: sender, subject, received time, and the original text. Each line's source_span is highlighted in its status colour. For PDFs and spreadsheets, show the extracted text with a link to open the original file.
- Right panel, what the system made of it: customer (with how it was matched, and a dropdown to change it), PO number, delivery date (resolved date plus raw words), delivery address and notes. All editable.
- Lines table: what they wrote, matched product (code and description), quantity with conversion note, price, status pill with reason. A Ready line shows how it matched ("Remembered phrasing", "Exact name match"). A Check or Problem line opens in place with a product dropdown (top 5 candidates first, then type to search the whole catalogue), quantity, unit, and "Remember this for <customer> next time" ticked by default. Buttons to add or delete a line.
- Checks panel: each check with a tick or warning and plain words.
- Export preview: the exact rows this order will produce in the client's format, updating live as edits are made.
- Bottom bar: your review time on this order (counts from opening the page), Not an order (marks Pass on), Ask client contact (copies a ready-written question email to the clipboard), Download this order (that order alone, in the client's format), and "Approve and add to export", which is disabled while any line is Problem.
- Keyboard: A approve, N next order, 1 to 5 picks a candidate on the focused line, Escape closes an open line.
- Approving saves approved_at, review_seconds, corrections_count, creates aliases, and opens the next order needing review.

### 5. Shadow trial results

- Header: breadcrumb, trial start date, day X of Y, the date range the results cover, "Upload staff entries" and "Export report for client".
- Staff file card: file name, what it is, how many of their orders matched ours by PO number, and an "Answer key loaded" pill. Uploading a staff file reuses the column mapping pattern from setup.
- Six tiles: orders compared, exactly right (percent and count), confidently wrong (shown green when 0, red otherwise), flagged for review (count and percent), average review time, staff time it would save.
- Day by day tiles for every working day of the trial: percent exactly right and "21 of 22" for finished days, "Today" for the current day, "Upcoming" after.
- "Every difference from their team": order, field, we entered, staff entered, why (editable note), and a fix tag (Rule added, Remembered, Ask client, Our error, Staff error).
- "Summary for the client": a generated paragraph using the real numbers, editable before export. The export is a clean printable page with the tiles, day by day results, differences and summary.

## Exports and the trial comparison

Exports turn approved standard orders into the client's file; the comparison checks those files against what the client's staff really entered.

Export rules:

- Built only from the standard internal order plus the client's OutputTemplate. One row per order line, header fields (account code, PO number, dates, notes) repeated on every row.
- Use the template's column order, delimiter, encoding, date format and decimal places exactly. Include the header row only if the example had one.
- File names: `<ClientShortName>_orders_<YYYY-MM-DD>_<HHMM>.csv` for a batch, `<ClientShortName>_PO<number>.csv` for one order.
- A batch export includes every Approved order not yet exported, then marks them Exported. Past batches can be downloaded again but never silently rebuilt.
- Never export an order that is not Approved.

Trial comparison:

1. The operator uploads the staff export and maps its columns (PO number, account code, product code, quantity, unit, price, delivery date) with the same mapping screen as setup.
2. Orders are paired by PO number and account code. Unpaired orders are listed as "only ours" or "only theirs", never dropped.
3. Within a pair, lines are paired by product code. Quantities are compared after converting both sides to the sell unit. Prices are equal within 1p.
4. Each difference is stored with field, our value and their value. The operator adds a cause note and a fix tag.

Definitions (use these exact definitions in code, tiles and the report):

| Measure | Definition |
|---|---|
| Orders compared | Paired orders in the date range |
| Exactly right | Paired orders where our approved output matches the staff entry on customer, every line's product code, quantity and unit, price (within 1p) and delivery date when both sides have one |
| Confidently wrong | Orders whose initial_status was Ready and which are not exactly right. Target: always 0 |
| Flagged for review | Orders whose initial_status was Check or Problem |
| First pass exact (internal only) | Orders where the pipeline's output before any operator correction would have been exactly right. Shown on the trial page under a "Details for you" toggle, not in the client report |
| Review time | Average of review_seconds over approved orders |
| Staff time it would save | Orders compared multiplied by the client's minutes_per_order, shown in hours to one decimal place |

The client report shows exactly right, confidently wrong, flagged, time saved, the day by day table, the differences with causes, and the summary paragraph. It never shows AI cost or internal measures.

## Demo client and test data

`make seed` builds a fictional client, "Demo Cleaning Supplies", from raw files, exactly as a real client would be set up. It is used for pitching, for development and for the accuracy test. All of it is invented; no real company or person appears.

Files the seed script writes (into seed/demo/, deterministic, the same every run):

- **demo_products.xlsx:** about 80 products across cleaning chemicals, gloves, paper, bags, dispensers and cloths. Realistic codes (CLN-101, GLV-202, TOW-310), sell units (CASE, BOX, PACK, EACH), pack sizes, inner units (bottle, roll, pair) and prices. Include near twins on purpose, such as TOW-310 blue roll pack of 6 and TOW-311 blue roll single.
- **demo_customers.csv:** 15 customers (hotels, cafés, schools, gyms, offices, a pub), with account codes, one or two order emails each, delivery addresses and price bands A or B. Two customers share an email domain.
- **demo_prices.csv:** band B prices for 20 products.
- **SalesOrderImport_example.csv:** the output template from the wireframe: AccountRef, CustOrderNo, OrderDate, StockCode, Qty, UnitPrice, DeliveryDate, Notes, dates as dd/mm/yyyy.
- **orders/:** 20 test orders. 8 email texts (.eml), 5 PDFs (generated with reportlab, including one table-style purchase order and one letter-style), 4 Excel files and 3 CSV files.
- **answers/:** one JSON file per test order in the standard internal order shape, plus the expected initial_status, and the expected export rows.
- **staff_entries_demo.csv:** a fake staff export for the 20 orders with 3 planted differences, used to test the trial screen.

Hard cases the 20 orders must cover, each at least once:

| Case | Expected result |
|---|---|
| Bottles that divide exactly into cases | Converted, Ready if matched by alias or name |
| Bottles that do not divide into cases | Problem, conversion message |
| Misspelt product ("disinfectent spary") | Check, correct product as the top candidate |
| Customer uses their own product codes | Check first time; Ready after an alias is saved |
| Manufacturer barcode instead of product code | Ready by exact code |
| Product not in the catalogue | Problem, unknown product |
| Near twins (blue roll single vs pack) with no unit | Check, never Ready |
| Price written differs from price band | Check, price flag |
| Same PO number sent twice | Check, duplicate flag |
| No PO number | Ready unless the rule requires one |
| "Deliver Thursday" | Resolved to the correct date |
| Sent after the cutoff for next day delivery | Check, cutoff flag |
| Order below minimum value | Check, minimum flag |
| Unknown sender, customer named in signature | Check, name match |
| Completely unknown sender | Problem, unknown customer |
| "Can you add 2 boxes to yesterday's order" | Pass on, amendment |
| "Where is my delivery?" | Pass on, chase |
| A 40 line Excel order | All lines extracted, none dropped |
| A simple, clean order | Ready |
| A scanned PDF (image only) | Problem, scanned PDF |

The demo client also needs at least 3 pre-saved aliases so the "Remembered phrasing" path is exercised from the first run.

## Accuracy testing harness

`make eval` runs every demo test order through the pipeline without the interface and compares the result with its answer key. It is the safety net: run it after every change to anything in pipeline/.

What it does:

1. Rebuilds a fresh copy of the demo client in a temporary database, so runs never affect real data.
2. Processes all 20 orders.
3. Compares each result with answers/<order>.json on customer, PO number, delivery date, and every line's product code, quantity, unit and price. Compares the order's initial_status with the expected status.
4. Prints a table: order, expected status, actual status, exact or not, and the differences.
5. Prints a summary: first pass exact (percent), confidently wrong (count), flagged (percent), wrong status (count), average AI cost per order, average processing time.
6. Saves the full results to data/eval/<timestamp>.json and shows the change against the previous run.

Pass rules:

- Confidently wrong must be 0. If it is not, the command exits with an error and the change is not finished.
- Every hard case must reach its expected status.
- First pass exact is reported, not enforced. The starting target is 80% on the demo set; it should rise over time, never fall without a stated reason.

Options: `--client <id>` runs the same comparison for a real trial client using its uploaded staff entries; `--no-cache` forces fresh AI calls (by default responses are cached by input hash, so runs are repeatable and cheap); `--only <order>` runs one order with full detail.

Also write ordinary pytest tests for the deterministic parts: file parsing per type, normalisation, alias matching, unit conversion (every rule in pipeline step 6), each check, status rules, and export rendering against the expected rows.

## Security and data handling

Client data includes their customers' names and emails, so treat it as confidential personal data from day one.

- **Local only:** the app listens on 127.0.0.1, never on a public address.
- **Login:** one password from APP_PASSWORD, a signed session cookie, logout after 8 hours idle.
- **Separation:** every table query filters by client_id; files live in data/clients/<client_id>/. Add a test that a second client's orders never appear in the first client's queue or exports.
- **Nothing in git:** data/, .env and seed/demo/ outputs are git-ignored. Only code, templates and the seed script are committed.
- **What goes to the AI:** only an order's text, the customer name and a short candidate list per line. Never a whole catalogue, customer list, price list or staff export.
- **No client data in logs** printed to the terminal; detail goes to the Event table only.
- **Delete a client:** a button in Settings, with typed confirmation, that removes every row and file for that client. Trial clients that do not continue are deleted within 30 days of the trial ending.
- **Backups:** `make backup` copies data/ into a dated, zipped folder. Rich keeps full disk encryption switched on for his laptop.

Before the first real client data goes in, Rich should read the AI provider's current data usage terms, and have a short data processing clause in the trial agreement (UK GDPR). This brief is not legal advice.

## Build milestones

Eight milestones, built in order. Each one ends with something Rich can click through, and its acceptance check must pass before the next one starts. Commit at the end of each milestone.

| # | Milestone | What gets built | Done when |
|---|---|---|---|
| 1 | Skeleton | Project layout, settings, login, all data model tables, top bar, Home screen reading real data, make seed writing the demo files and loading the demo client | Rich logs in and sees the demo client card with its product and customer counts |
| 2 | Setup wizard | Screen 2: uploads, column guessing, mapping dropdowns, output template detection and live preview, rules and unit synonyms | A second client is created from the demo files through the interface alone, and its mappings survive a restart |
| 3 | Ingest and extract | Pipeline steps 1 and 2 for all six input types, the upload box, paste box and background processing on screen 3 | All 20 demo orders show extracted lines with correct source spans; the scanned PDF and the two Pass on emails get the right status |
| 4 | Matching, checks, status and eval | Pipeline steps 3 to 9, aliases, AI call 2, unit conversion, every check, the status rules, make eval and the pytest suite | make eval passes: 0 confidently wrong, every hard case at its expected status, first pass exact reported |
| 5 | Review screen | Screen 4 in full: split view with highlights, editable header, line editing with candidates and search, remember this, checks panel, export preview, timers, keyboard shortcuts | All 20 demo orders reviewed and approved in the interface; re-uploading a corrected order now matches by alias and shows "Remembered phrasing" |
| 6 | Export | Template rendering, single order download, batch export, export history | The batch file for the 20 approved demo orders matches the expected export rows exactly |
| 7 | Trial comparison | Staff entry upload and mapping, pairing, differences, the six measures, day by day tiles, summary paragraph, printable client report | Uploading staff_entries_demo.csv shows exactly the 3 planted differences and correct tiles |
| 8 | Polish | Needs your attention list, filters and counts, cost and time figures, delete client, make backup, error messages in plain words, empty states | Rich runs a complete fake trial on the demo client from setup to report without touching the terminal |

After version one, the next milestone is auto-forwarding: a dedicated mailbox per client, polled every few minutes, feeding the same pipeline.

## Working rules for Claude Code

These rules keep the build on track. Claude Code should treat them as fixed.

1. Read all of BRIEF.md before writing any code.
2. Work on one milestone at a time. Before coding a milestone, show a short plan (files to create or change, tests to write) and wait for Rich to say go.
3. Never build anything from the "Later" list unless Rich asks.
4. Keep pipeline/ free of web code. Every pipeline step is a function that can be tested on its own.
5. The AI only reads text and chooses from lists. Code does all arithmetic, unit conversion, checks and statuses. Every AI answer is validated: codes must exist in the candidate list, spans must exist in the text.
6. Never let a guess reach Ready. When unsure, flag it.
7. After any change to pipeline/, run make test and make eval and report the summary. If confidently wrong is above 0, fix that before anything else.
8. Do not add libraries beyond the stack table without asking.
9. When this brief is unclear, ask. If a small assumption is needed to keep moving, make it, and record it as one line in DECISIONS.md.
10. Match the wireframe's layout, palette and wording. Plain, specific words in the interface; no technical jargon on screen.
11. Commit at the end of each milestone with a message naming it.

### First prompt to paste into Claude Code

```
Read BRIEF.md in this folder fully. It describes Order Desk, an internal dashboard for running a done-for-you order processing service for UK wholesalers.

Then do three things, and stop:
1. Summarise in 10 lines what you will build and the one rule that matters most.
2. List any questions where the brief is unclear or contradicts itself.
3. Show your plan for Milestone 1 (Skeleton): the files you will create, the tables, and how you will check it is done.

Do not write any code until I reply "go".
```

### Prompt for each later milestone

```
Milestone N is next (see the Build milestones table in BRIEF.md). Show your plan for it, then wait for "go". When finished, run make test and make eval, show me the results, and tell me exactly what to click to check the "Done when" condition myself.
```
