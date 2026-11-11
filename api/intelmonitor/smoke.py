"""Connectivity check for the Claude path: one Haiku call and one call on the default model."""

from pydantic import BaseModel

from intelmonitor.config import get_settings
from intelmonitor.pipeline.claude import ask_claude
from intelmonitor.pipeline.params import HAIKU_MODEL

SAMPLE = (
    "The Monetary Authority of Singapore imposed a composition penalty of S$2.4 million on a "
    "local bank for breaches of anti-money laundering requirements."
)


class SmokeFinding(BaseModel):
    statement: str
    subjects: list[str]
    category: str


async def run_smoke() -> None:
    for model in (HAIKU_MODEL, get_settings().anthropic_model):
        reply = await ask_claude(
            f"Extract one finding from this text:\n\n{SAMPLE}",
            SmokeFinding,
            system="You extract findings for a financial supervisor's monitoring dashboard.",
            model=model,
        )
        print(f"{model}:", reply.output.model_dump_json(), reply.model_version, reply.cost_usd)
