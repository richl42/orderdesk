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
