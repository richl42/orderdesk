"""The shared Jinja2 environment."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.web import format as fmt
from app.web.flash import take_flashes

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters.update(
    london=fmt.london,
    long_date=fmt.long_date,
    moment=fmt.moment,
    money=fmt.money,
    pence=fmt.pence,
    qty=fmt.qty,
    duration=fmt.duration,
    ago=fmt.ago,
)
templates.env.globals.update(
    status_label=fmt.status_label,
    status_class=fmt.status_class,
    operator_name=lambda: get_settings().operator_name,
    take_flashes=take_flashes,
    ai_prices_set=lambda: get_settings().ai_prices_set,
)
