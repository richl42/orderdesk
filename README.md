# Order Desk

An internal dashboard that turns wholesale customers' messy orders into exact, ready-to-import sales
orders in each client's own format. See BRIEF.md for the full description and DECISIONS.md for the
assumptions made while building.

## Run it on your laptop

You need Python 3.12 and `make`.

1. Download the code: `git clone https://github.com/richl42/orderdesk.git` then `cd orderdesk`
2. Install the libraries (once): `make install`
3. Copy `.env.example` to `.env` and fill in at least `APP_PASSWORD` (the password you will log in with).
4. Load the demo client: `make seed`
5. Start the app: `make run`, then open http://127.0.0.1:8000 and log in.

## Commands

| Command | What it does |
|---|---|
| `make run` | Starts the app at http://127.0.0.1:8000 (your laptop only) |
| `make test` | Runs the automated tests |
| `make seed` | Rebuilds the demo client from its files |
| `make eval` | Accuracy test (from Milestone 4) |
| `make backup` | Zips the data folder into `backups/` |

Client data lives in `data/` and is never committed to git.
