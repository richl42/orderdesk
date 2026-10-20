# Decisions

Assumptions made while building, one line each. Agreed with Rich on 19 Oct 2026 unless marked otherwise.

## Agreed before Milestone 1
- Extra libraries allowed beyond the stack table: uvicorn, python-multipart, itsdangerous, charset-normalizer, reportlab, httpx.
- .env is read by a few lines of our own code (app/config.py), not python-dotenv.
- HTMX and the IBM Plex fonts are bundled in app/static, so nothing loads from the internet. Fonts are under the SIL Open Font Licence.
- Alias phrases remove only the quantity and the unit word; digits inside words and codes are kept ("RH-0042", "2ply", "750ml").
- A line that is only the customer's own code with no description has no candidates, so it is Problem, not Check.
- Exported price is the customer's band price, else the catalogue price; a price written on the order only raises the price flag. All prices exclude VAT.
- Trial differences tagged "Staff error" do not count against exactly right or confidently wrong; the client report says so.
- Orders with no PO number are paired with staff entries by account code plus order date, only when exactly one match exists on each side.
- OrderLine and StaffEntryLine carry client_id too, so every table except Client has it.
- A weekday name ("Thursday") means the next such day strictly after the day the order arrived.
- "Delivery date in the past" compares against the day the order arrived, not today, so results do not drift as time passes.
- Working days are Monday to Friday minus England and Wales bank holidays (list in pipeline/workdays.py runs to the end of 2028).
- Re-uploading the same order text triggers the duplicate check, so it shows "Remembered phrasing" but is Check, not Ready.
- The trial screen works for any client with a trial start date; the demo client's trial starts Mon 12 Oct 2026.
- Home tiles leave out demo clients (as the brief says) and say so under the tiles.
- Client ids are short text ids ("demo", "abc"); Client.short_name is used in export file names.
- The operator name in the top bar comes from the OPERATOR_NAME setting (default Rich).

## Milestone 1
- If SECRET_KEY is not set, one is generated once and kept in data/.secret_key, so logins survive restarts.
- Money and quantities are stored as exact decimal text in SQLite, never as floats; times as UTC ISO text.
- Order also stores sender_email, subject, classification_reason and processing_state (reading, done, failed), needed by the queue and review screens.
- Client.setup_done records which of the five setup sections are saved; a client shows "Setting up" until details, catalogue, customer list and output format are done (rules are optional).
- Client cards show the contact name where the wireframe shows a contact role; there is no separate role field.
- A band price list is imported as its own file (ColumnMapping kind "prices"); the brief has no wizard step for it, so it will sit in the catalogue section of setup.
- Unit synonyms are stored as {canonical unit: [words]}: sell units in capitals (CASE), inner units in lower case (bottle), from the catalogue.
- Spreadsheet reading (header row detection, column guessing, CSV encoding and delimiter) lives in pipeline/tables.py so setup and order intake share it.
- A CSV that is not UTF-8 is read as Windows-1252 (Excel on a UK PC) before any guessing, because short files fool the charset detector.
- The header row is the first of the top 15 rows with several filled cells that are mostly words, so title rows above the headings are skipped.
- The "Needs your attention" list includes demo client orders (only the tiles leave them out), so practice orders can be reviewed.
- Waiting for review counts Ready, Check and Problem orders; Pass on orders appear under Needs your attention instead.
- Demo email addresses use the reserved .example domain so no real company or person can appear.
- The demo catalogue has 81 products; band B prices are list price less 6%, for 20 products.
- The wireframe canvas's five screens are kept in docs/wireframe/ as reference.

## Milestone 2
- The setup screen lives at /clients/<id>/setup; the Settings tab and "Continue setup" open it. A new client gets its id from the short name ("ABC Cleaning Supplies" -> abccleaningsupplies).
- An uploaded setup file is kept as "waiting to be saved" (with a note on disk) until the operator checks the column guesses and saves, so half-finished setup survives a restart.
- When a header says nothing useful, the column is guessed from its values (codes, emails, prices, barcodes, unit words). These guesses are marked "please check". Asking the AI about unclear headers waits until the AI connection is built in Milestone 3.
- Each meaning can be chosen for one column only; a second column given the same meaning is treated as ignored.
- Saving a file replaces that client's previous catalogue, customer list or band prices in full.
- The band price list is its own optional section, "Prices by band", between the catalogue and the customer list.
- The output example must be a CSV (exports are CSV files); an Excel example gets a plain message asking for a CSV.
- Output columns can also be filled with a fixed value. Exports use Windows line endings (CRLF), which Sage and similar systems expect.
- The output preview is a made-up order using the client's first customer and first two products, with that customer's band prices.
- Trial start date and length are set in Client details.
- Duplicate checking can be switched off; the "days to look back" is stored as duplicate_window_days, with 0 meaning off.
- The minimum order rule is on whenever a minimum value is entered; leaving it empty means no minimum.
- "Save client" checks details, catalogue, customer list and output format are done (rules are optional) and lists anything missing.
- Old .xls files are refused with a message to save them as .xlsx or .csv; uploads over 25 MB are refused.

## Milestone 3
- The API key can also be set as ORDERDESK_ANTHROPIC_API_KEY, for cloud build sessions where ANTHROPIC_API_KEY may not get through; ANTHROPIC_API_KEY wins when both are set.
- Claude Sonnet 5.5 refuses a fixed temperature and a forced tool choice, so calls send neither: the prompt asks for the tool, strict mode keeps its answer to the JSON schema, and an answer with no tool call counts as invalid (retried once, then Problem). Thinking effort is medium.
- An AI refusal is treated like an unreadable order (Problem, "Could not read this order"); no other model is tried.
- A line's source_span must be in the order text exactly, or differ only in spacing and line breaks; the text's own version is what gets stored.
- The quantity, code, unit and price read for a line must appear inside its span, or the line is Problem. A PO number, delivery date, address, notes or customer name that is not in the text is left blank with a Check flag.
- The upload box has optional sender email and received time, applied to every file in that upload. An .eml file always keeps its own From and Date unless they are given.
- A demo client gets a "Load the 20 test orders" button, which uploads them with the sender and arrival time from each answer key, so dates resolve as they would have on the day.
- An order with nothing wrong after reading is shown as "Read, matching comes next" with no status or initial_status until Milestone 4 matches it. Problem and Pass on are set straight away, because nothing later can change them.
- An order's raw text is the email body followed by each attachment under a "--- Attached file: name ---" line. Sender and subject are kept apart, so the content hash ignores them.
- Spreadsheets become text with the rows above the table (customer, PO, dates) as plain lines and each table row as "Column: value | Column: value". Short rows under the table (notes, totals) stay plain lines. Every sheet is read.
- Ruled tables in a PDF are written as "Column: value" rows where they sit on the page.
- A PDF counts as scanned when a page has under 20 characters of text and an image on it, or the whole file has under 20 characters. A blank page with no image is skipped.
- Unclear delivery wording gives no date (the raw words are kept). "Next Thursday" is resolved only when it can only mean the following week. A weekday and date that disagree give no date. A day and month with no year take the year closest to arrival.
- Orders are read in the background by 3 worker threads inside the app; orders still "Reading" at a restart are picked up again. If the AI can't be reached the order shows Problem with a "Try again" button and gets no initial_status.
- An order with problem lines gets one order-level flag that sums them up, like the brief's "1 line could not be matched".
- AI answers are kept on disk only when AI_CACHE=on (the accuracy test will always keep them).
- New database columns are added automatically at start-up, and are always nullable; Order.customer_name_raw (the name as written) is the first.
- In setup, the AI is asked only about columns whose headers say nothing; it sees the headers and three example values. Its picks are still marked "please check", and without the AI the header and value guesses stand.
- Answer keys hold the right final order. Their spans are taken from the extracted text, and the bottles-that-don't-divide order has 3 cases (as if the customer confirmed by phone).
- Files that can't be orders (photos, Word, .xls) are refused at upload with the reason, and no order is made.
- Until the review screen (Milestone 5), Review opens a read-only page: the original text with each line highlighted, what was read, and a link to the original file.
- In the orders table, today's orders show "Today, 09:14"; older ones show the day and date.

## Milestone 4
- Matching works on each line as the customer wrote it, not on the AI's summary of it: saved phrases, exact names and candidates all use the line text, and for a spreadsheet row only the product cells (codes, descriptions), never quantity, unit, price, date, reference or note cells.
- An exact name match compares with the catalogue description both with and without its pack wording (", case of 12", ", single roll"). Two products that share a name (blue roll pack and single) are never an exact match, so near twins can't be Ready.
- Candidates are scored with rapidfuzz token_set_ratio against the catalogue names with the pack wording stripped (codes and barcodes left out, as exact codes are matched first). The top 10 scoring 40 or more go to the AI, and the top 5 are kept on the line.
- A product code written on a line is checked against the words beside it: if they score under 50 against that product's name, the line is Check. Two of the client's codes on one line is not an exact match.
- AI call 2: when more than one candidate fits, the AI picks the likeliest and marks it ambiguous. Ambiguous picks are always Check, even when allow_ai_ready is on. With no pick at all, the line is Problem ("Not found in the catalogue", or "Could be more than one product").
- Product words the AI reads for a line must appear in that line, or they are dropped.
- The quantity read for a line must sit where a quantity would: in a quantity column, or in free text next to a unit word, after "x", or at the start or end of the line. Otherwise the line is Check, so a number in a product name ("200 sheet") can't pass as the quantity.
- Lines whose text could not be checked at reading stay Problem and are not matched.
- Customer names are compared with token_set_ratio, so a short name that fits several customers ("Harbour Hotel") matches none of them. An order sent from one customer's email that names a different customer is Check.
- Customer matching also runs on Pass on and unreadable orders, so the queue shows who sent them; it does not change their status.
- A client's orders are matched one at a time in arrival order, and only once every earlier order has been read, so the duplicate check sees the orders that came before. It only looks back: an order uploaded later but dated earlier does not flag the one already matched.
- The duplicate check looks at new orders that arrived in the days before (duplicate_window_days), by customer and PO number, or by identical text.
- The cutoff check flags orders received after cutoff_time and wanted the same day or the next working day.
- The minimum order check runs only once every line has a quantity and a price, so a line still being sorted out can't trigger it.
- The quantity outlier check compares only with this customer's approved or exported orders.
- Delivery wording that could not be turned into a date ("as normal") is a Check, not left silent.
- A product with no band or catalogue price is Check. A price written per inner unit that matches the case price is not a price difference.
- A quantity that is missing, written as a word, zero or not whole, and a unit that is another sell unit ("boxes" for something sold by the case), are Problem.
- Each time a saved phrase matches, its times_used and last_used_at are updated.
- make eval takes options through ARGS (make eval ARGS="--only 05"). AI answers are kept in data/ai_cache/ and results in data/eval/. --client says it needs staff entries, which arrive in Milestone 7.
- The test suite replays the saved AI answers for the 20 demo orders (tests/fixtures/demo_ai_answers/, invented demo data only), so it checks every step after the AI without calling it. After any prompt change: make eval ARGS="--save-test-answers".
- Answer key changes: order 04 now expects delivery wording "as normal" and its date_unclear flag; order 15's unknown product uses the flag code unknown_product.
- A Ready order's reason line says "Everything matched and checked. Approve to add it to the export".

## Milestone 5
- The review screen lives at /clients/<id>/orders/<order id> (replacing the read-only page); "Start reviewing" opens the oldest order needing review.
- Every change saves straight away and re-scores the order in code (prices, checks, statuses), with no AI call. initial_status never changes. Each change is one correction and is logged as an Event.
- A product the operator picks or confirms (including confirming the AI's suggestion) is "Chosen by you" and clears that line's earlier flags. Price and outlier checks still run on it, so a written price that differs stays a Check after confirming.
- The quantity is entered in the product's sell unit or, where it has one, its inner unit; code converts it, and a remainder is refused with the reason, leaving the line as it was.
- A customer chosen by the operator counts as confident. Choosing "no customer" makes the order Problem again.
- Approve is refused, on the server as well as the button, while there is no customer, no lines, or any Problem line. Check orders can be approved: the operator has looked at them.
- Remembered phrases are saved on approval, not on each edit, so abandoned changes teach nothing. One is saved for each line the operator picked or confirmed with "Remember this" ticked (the default). It replaces any earlier phrase this customer used for the same words. The unit is remembered only when the customer wrote none (the unit used, or the inner unit when it was converted).
- Review time is the time the review page was open, measured in the browser and sent with the approval, capped at 2 hours per visit and added up over visits.
- Approving opens the next order needing review in queue order (oldest first, wrapping round), or the queue when none is left.
- "Not an order" marks an order Pass on, and "It is an order" brings it back; a message the AI read as not an order then becomes a new order whose lines are added by hand.
- An order whose text could not be read (a scanned PDF) can have its lines typed in with Add a line; that clears the scanned or unreadable flag, and the order is then checked like any other.
- An order from someone who is not a customer (Brightside Lettings in the demo) can't be approved; it goes to the client contact (Ask client contact, then Not an order). So the demo review ends with 17 approved and 3 passed on.
- Ask client contact copies a ready-written email (To, Subject, the issues in plain words) to the clipboard, and opens it in the mail app if copying isn't possible.
- "Undo approval" reopens an approved order until it is exported.
- Download this order is shown but waits for Milestone 6.
- Catalogue search on the review screen lists products whose description contains every word typed, or whose words are close spellings of the words typed, and codes or barcodes starting with what was typed. Only this client's products.
- Keyboard: A approves, N opens the next order, 1 to 5 picks a product on the open line and saves it, Escape closes the open line. Letters and numbers do nothing while typing in a box.

## Milestone 6
- Export files are made only from the standard order and the client's output template, by the same code as the review screen's export preview. Rows go oldest order first, one per line.
- A batch holds every Approved order not yet exported. Making it marks those orders Exported, records an ExportBatch, and keeps the file in data/clients/<id>/exports/. If anything fails, nothing is marked exported and the file is removed. A lock stops two clicks putting the same orders in two files.
- "Download this order" counts as exporting it (agreed with Rich): it becomes a one-order ExportBatch, so the order can never be in a later batch as well. It is only offered once the order is approved; an exported order links to the file it went in.
- Past exports are downloaded again from the stored file, byte for byte, never rebuilt from today's data.
- File names use London time. A second batch in the same minute gets _2. A PO number already starting with "PO" is not given a second one (PO2291, not POPO2291), and an order with no PO number is named <ShortName>_order<id>.csv.
- After a batch is made, the history page opens and starts the download by itself, so the queue's counts are up to date afterwards.
- No output format set up means no file, with a plain message pointing at Settings.
- Answer key changes: order 10 (the scanned PDF) now holds the three lines typed in from the scan, with their export rows, so first pass exact on the demo reads 90% (nothing can read a scan first time). Order 05's delivery note is the customer's whole sentence, which is what is written and what gets exported.
- make check-export FILE=<file> compares a downloaded demo export with the answer keys' expected rows (79 rows for the 17 approved orders).
