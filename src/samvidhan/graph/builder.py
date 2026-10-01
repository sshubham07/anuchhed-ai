"""Builds the compiled LangGraph pipeline (HLD §8.1.1, ADR-0011). Built once, no checkpointer.

START → load_memory → route ─┬─► retrieve ─────────────┐
                             ├─► hyde ─► retrieve      ├─► generate → validate_citations ─┐
                             ├─► decompose ────────────┘                                  │
                             └─► respond_template ─────────────────────────► save_turn ◄┘ → END
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from samvidhan.graph import nodes
from samvidhan.graph.nodes import GraphDeps, Update
from samvidhan.graph.state import ChatState

NodeFn = Callable[[ChatState, GraphDeps], Awaitable[Update]]


class BoundNode(Protocol):
    """What LangGraph calls: the parameter must be named `state`."""

    def __call__(self, state: ChatState) -> Awaitable[Update]: ...


ChatGraph = CompiledStateGraph[ChatState, Any, ChatState, ChatState]

_NODES: dict[str, NodeFn] = {
    "load_memory": nodes.load_memory,
    "route": nodes.route_message,
    "hyde": nodes.hyde,
    "retrieve": nodes.retrieve,
    "decompose": nodes.decompose,
    "generate": nodes.generate,
    "validate_citations": nodes.check_citations,
    "respond_template": nodes.respond_template,
    "save_turn": nodes.save_turn,
}


def _timed(name: str, fn: NodeFn, deps: GraphDeps) -> BoundNode:
    """Bind deps and record the node's wall time in `latency_ms[name]`."""

    async def node(state: ChatState) -> Update:
        started = time.perf_counter()
        update = await fn(state, deps)
        return {**update, "latency_ms": {name: round((time.perf_counter() - started) * 1000)}}

    node.__name__ = name
    return node


def build_graph(deps: GraphDeps) -> ChatGraph:
    graph = StateGraph(ChatState)
    for name, fn in _NODES.items():
        graph.add_node(name, _timed(name, fn, deps))
    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "route")
    graph.add_conditional_edges("route", nodes.next_after_route, nodes.ROUTE_EDGES)
    graph.add_edge("hyde", "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("decompose", "generate")
    graph.add_edge("generate", "validate_citations")
    graph.add_edge("validate_citations", "save_turn")
    graph.add_edge("respond_template", "save_turn")
    graph.add_edge("save_turn", END)
    return graph.compile(name="samvidhan_chat")
