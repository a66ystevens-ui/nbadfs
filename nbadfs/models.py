from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict, model_validator

SLOTS = ('PG', 'SG', 'SF', 'PF', 'C', 'G', 'F', 'UTIL')
ELIGIBILITY = {s: {s} for s in SLOTS[:5]} | {
    'G': {'PG', 'SG'}, 'F': {'SF', 'PF'}, 'UTIL': set(SLOTS[:5])}


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError('Timestamps must include a timezone')
    return value.astimezone(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Player(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    team: str = Field(min_length=1, max_length=20)
    game: str = Field(min_length=1, max_length=80)
    positions: list[Literal['PG', 'SG', 'SF', 'PF', 'C']]
    salary: int = Field(ge=0, le=50000)
    start: datetime
    minutes: float = Field(gt=0, le=48)
    minutes_sd: float = Field(default=3, ge=0, le=20)
    play_probability: float = Field(default=1, ge=0, le=1)
    fppm: float = Field(gt=0, le=5)
    ownership: float = Field(default=0.1, ge=0, le=1)
    source_published_at: datetime | None = None
    received_at: datetime | None = None
    # Per-minute counting rates. When supplied, every key is required.
    rates: dict[str, float] | None = None

    @model_validator(mode='after')
    def validate_player(self):
        self.start = utc(self.start)
        for name in ('source_published_at', 'received_at'):
            if getattr(self, name) is not None:
                setattr(self, name, utc(getattr(self, name)))
        if self.source_published_at and self.received_at and self.received_at < self.source_published_at:
            raise ValueError('received_at cannot precede source_published_at')
        if not self.positions or len(set(self.positions)) != len(self.positions):
            raise ValueError('Supply unique base positions')
        if self.rates is not None:
            import math
            if set(self.rates) != {'two', 'three', 'ft', 'reb', 'ast', 'stl', 'blk', 'tov'}:
                raise ValueError('rates requires two, three, ft, reb, ast, stl, blk, tov')
            if any(not math.isfinite(v) or v < 0 or v > 5 for v in self.rates.values()):
                raise ValueError('Stat rates must be finite and between 0 and 5')
        return self


class Rules(StrictModel):
    locks: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    min_salary: int = Field(default=0, ge=0, le=50000)
    max_from_team: int = Field(default=8, ge=1, le=8)
    min_unique: int = Field(default=1, ge=1, le=8)
    max_exposure: float = Field(default=1, gt=0, le=1)
    # Exact slot assignments are critical for late swap.
    locked_slots: dict[str, str] = Field(default_factory=dict)


class Contest(StrictModel):
    size: int = Field(default=81, ge=2, le=501)
    entry_fee: float = Field(default=10, gt=0)
    entry_limit: int = Field(default=20, ge=1, le=150)
    # Index zero is rank 1; omitted trailing ranks pay zero.
    payouts: list[float] = Field(default_factory=lambda: [200, 120, 80, 60, 40, 30, 20, 20, 20, 20])
    late_swap: bool = True

    @model_validator(mode='after')
    def validate_ladder(self):
        import math
        if len(self.payouts) > self.size or any(not math.isfinite(x) or x < 0 for x in self.payouts):
            raise ValueError('Invalid payout ladder')
        if any(a < b for a, b in zip(self.payouts, self.payouts[1:])):
            raise ValueError('Payouts must be in descending order')
        if sum(self.payouts) > self.size * self.entry_fee:
            raise ValueError('Payouts exceed entry fees; overlay contests are not supported yet')
        return self


class RunRequest(StrictModel):
    players: list[Player] | None = Field(default=None, max_length=300)
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    entries: int = Field(default=3, ge=1, le=20)
    candidates: int = Field(default=24, ge=1, le=80)
    simulations: int = Field(default=1000, ge=100, le=10000)
    seed: int = Field(default=42, ge=0, le=2**32-1)
    mode: Literal['gpp', 'cash'] = 'gpp'
    rules: Rules = Field(default_factory=Rules)
    contest: Contest = Field(default_factory=Contest)
    current_lineup: dict[str, str] | None = None
    source_snapshot_ids: list[str] = Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def validate_request(self):
        self.as_of = utc(self.as_of)
        if self.entries > min(self.candidates, self.contest.entry_limit, self.contest.size - 1):
            raise ValueError('Entries exceed candidate count, entry limit, or available contest places')
        if self.current_lineup is not None and self.entries != 1:
            raise ValueError('Late swap currently supports one entry at a time')
        if self.current_lineup is not None and set(self.current_lineup) != set(SLOTS):
            raise ValueError('Late swap requires all eight current roster slots')
        return self
