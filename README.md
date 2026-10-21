# Order Desk

An internal dashboard that turns wholesale customers' messy orders into exact, ready-to-import sales
orders in each client's own format. See BRIEF.md for the full description and DECISIONS.md for the
assumptions made while building.

## Run it on your laptop

You need Python 3.12 and `make`.

1. Download the code: `git clone https://github.com/richl42/orderdesk.git` then `cd orderdesk`
2. Install the libraries (once): `make install`
3. Copy `.env.example` to `.env` and fill in at least `APP_PASSWORD` (the password you will log in with).
4. Start the app: `make run`, then open http://127.0.0.1:8000 and log in.
5. On the Home screen, use **Set up the demo client** (or run `make seed`).

## Using it without the terminal

Once the app is running, everything else is done on screen:

- **Start the demo again** (on the demo card, or the demo client's Settings, type DEMO to confirm) rebuilds
  the demo client exactly like `make seed`. Then **Load the 20 test orders** on its Orders tab.
- **Download a backup** (bottom of Home) gives you the same zip as `make backup`, and keeps a copy in `backups/`.
- **Delete this client** (bottom of a client's Settings, type their name to confirm) removes every row and
  file for that client. Home and Trials remind you to delete trial clients within 30 days of their trial ending.
- Set `AI_PRICE_INPUT_PER_MTOK` and `AI_PRICE_OUTPUT_PER_MTOK` in `.env` to see AI cost in pence; without
  them the screens show tokens.
- If something goes wrong, the screen shows a plain message and the details go to `data/logs/errors.log`
  (and the client's event log).

## Run it on a server

See [docs/DEPLOY.md](docs/DEPLOY.md) for Render (Blueprint in `render.yaml`) or Railway. In short: a
persistent disk for the data folder, `APP_ENV=production`, the secrets as environment variables, and
the start command `python -m app.serve`.

## Commands

| Command | What it does |
|---|---|
| `make run` | Starts the app at http://127.0.0.1:8000 (your laptop only) |
| `make test` | Runs the automated tests |
| `make seed` | Rebuilds the demo client from its files |
| `make eval` | Accuracy test: runs the 20 demo orders and compares them with their answer keys. Options: `make eval ARGS="--only 05"`, `ARGS="--no-cache"` |
| `make check-export FILE=...` | Checks a downloaded demo export file against the demo answer keys |
| `make backup` | Zips the data folder into `backups/` |

Client data lives in `data/` and is never committed to git.
