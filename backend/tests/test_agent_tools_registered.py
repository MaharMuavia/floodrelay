"""Every tool handed to an agent must be a real `@tool`.

This is the test the `ndma_situation` bug earned. `ndma.situation` was put in
the triage agent's tool list without the `@tool` decorator; Strands logged
"unrecognized tool specification" and silently dropped it, so an advertised data
source was one the model could never reach -- and nothing failed. A tool list is
exactly the place a plain function slips in unnoticed, because the code around it
looks identical.
"""

from __future__ import annotations

from strands.tools.decorator import DecoratedFunctionTool

from floodrelay.agent.nodes import triage
from floodrelay.agent.tools.geocode import geocode_place
from floodrelay.agent.tools.places import find_places


def _assert_all_tools(tools: list[object], where: str) -> None:
    for t in tools:
        assert isinstance(t, DecoratedFunctionTool), (
            f"{where} contains {t!r}, which is not a @tool. Strands drops it "
            f"silently, so the model can never call it."
        )


def test_the_triage_context_tools_are_all_real_tools() -> None:
    tools = triage._context_tools()
    assert tools, "the triage agent was given no tools at all"
    _assert_all_tools(tools, "triage._context_tools()")


def test_the_geolocate_agent_tools_are_all_real_tools() -> None:
    # The list geolocate.run_with_agent binds. Kept in sync by being the same
    # two imports that function uses.
    _assert_all_tools([geocode_place, find_places], "geolocate")


def test_the_extract_agent_tool_is_a_real_tool() -> None:
    _assert_all_tools([geocode_place], "extract")


def test_the_dispatch_tool_is_never_in_the_triage_list() -> None:
    """An explanation must not be able to reach a tool that dispatches a boat.

    The human gate would refuse it anyway, but the cheapest guarantee is that
    the tool is simply not on the table.
    """
    names = {t.tool_name for t in triage._context_tools()}
    assert "roster_assign" not in names
    assert "notify_responder" not in names
