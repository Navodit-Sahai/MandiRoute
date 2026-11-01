"""
LangGraph execution tracer and topology inspector for MandiGuard.
Provides real-time, un-simulated execution traces of nodes, tools, and conditional routing decisions.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Callable, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# Terminal styling helpers
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
BLUE = "\033[34m"
RESET = "\033[0m"


class ExecutionTracer:
    """
    Instruments actual LangGraph workflow executions, tool calls, and conditional branches.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.nodes_executed: list[str] = []
        self.tools_called: list[dict[str, Any]] = []
        self.routing_decisions: list[dict[str, Any]] = []
        self.current_node: str = "main"

    def reset_run(self) -> None:
        self.nodes_executed = []
        self.tools_called = []
        self.routing_decisions = []

    def on_node_start(self, node_name: str, input_state: dict[str, Any]) -> float:
        self.current_node = node_name
        self.nodes_executed.append(node_name)
        start_time = time.time()
        if not self.enabled:
            return start_time

        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"\n{CYAN}{BOLD}[NODE START]{RESET} {BOLD}{node_name}{RESET}")
        print(f"  {DIM}Timestamp:{RESET} {ts}")

        # Extract only relevant input fields
        relevant_inputs = {}
        for k in ("plan_id", "mode", "status", "active_version", "step_num"):
            if k in input_state:
                relevant_inputs[k] = input_state[k]

        if "request" in input_state and input_state["request"]:
            req = input_state["request"]
            relevant_inputs["request"] = {
                "crop": req.crop,
                "origin": req.origin_label,
                "quantity_qtl": req.quantity_qtl,
                "risk_mode": req.risk_mode,
            }
        if "progress" in input_state and input_state["progress"]:
            prog = input_state["progress"]
            relevant_inputs["progress"] = {
                "delay_min": prog.reported_delay_min,
                "last_waypoint": prog.last_waypoint_idx,
                "source": prog.source,
            }
        if "trigger_decision" in input_state and input_state["trigger_decision"]:
            dec = input_state["trigger_decision"]
            relevant_inputs["decision"] = {
                "fired": dec.fired,
                "persisted": dec.persisted,
                "propose_replan": dec.propose_replan,
            }

        print(f"  {DIM}Input State:{RESET} {json.dumps(relevant_inputs, default=str)}")
        return start_time

    def on_node_end(
        self,
        node_name: str,
        start_time: float,
        output_state: dict[str, Any],
        status: str = "SUCCESS",
    ) -> None:
        if not self.enabled:
            return

        duration = time.time() - start_time
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        status_color = GREEN if status == "SUCCESS" else RED

        print(f"{CYAN}{BOLD}[NODE END]{RESET} {BOLD}{node_name}{RESET}")
        print(f"  {DIM}Timestamp:{RESET} {ts} ({duration:.3f}s) | Status: {status_color}{status}{RESET}")

        # Summarize state updates
        updates = {}
        for k in ("status", "active_version", "next_action", "critic_notes", "error"):
            if k in output_state:
                updates[k] = output_state[k]

        if "planset" in output_state and output_state["planset"]:
            ps = output_state["planset"]
            updates["planset"] = {
                "plan_id": ps.plan_id,
                "version": ps.version,
                "plan_count": len(ps.plans),
                "plan_a_dest": ps.plans[0].option.mandi_name if ps.plans else "N/A",
                "coverage_grade": ps.coverage.grade,
            }
        if "assembly" in output_state and output_state["assembly"]:
            ass = output_state["assembly"]
            updates["assembly"] = {
                "candidates_count": len(ass.candidates),
                "evidence_count": len(ass.evidence),
                "warnings_count": len(ass.warnings),
            }
        if "diff" in output_state and output_state["diff"]:
            diff = output_state["diff"]
            updates["diff"] = {
                "versions": f"v{diff.old_version} -> v{diff.new_version}",
                "primary_reason": diff.primary_reason,
                "recommendation_changed": diff.recommendation_changed,
            }

        print(f"  {DIM}State Updates:{RESET} {json.dumps(updates, default=str)}")

    def on_tool_call(
        self,
        tool_name: str,
        source: str,
        args: dict[str, Any],
        provenance: str = "LIVE",
    ) -> float:
        start_time = time.time()
        # Mask secrets
        masked_args = {}
        for k, v in args.items():
            if any(s in k.lower() for s in ("key", "token", "secret", "auth", "pass")):
                masked_args[k] = "***"
            else:
                masked_args[k] = v

        tool_record = {
            "node": self.current_node,
            "tool": tool_name,
            "source": source,
            "args": masked_args,
            "provenance": provenance,
            "start_time": start_time,
        }
        self.tools_called.append(tool_record)

        if not self.enabled:
            return start_time

        print(f"\n  {YELLOW}{BOLD}[TOOL CALL]{RESET}")
        print(f"    {DIM}Calling Node:{RESET} {self.current_node}")
        print(f"    {DIM}Tool Name:{RESET}    {BOLD}{tool_name}{RESET}")
        print(f"    {DIM}Data Source:{RESET}  {source}")
        print(f"    {DIM}Arguments:{RESET}    {json.dumps(masked_args, default=str)}")
        print(f"    {DIM}Provenance:{RESET}   {provenance}")
        return start_time

    def on_tool_result(
        self,
        tool_name: str,
        start_time: float,
        result_summary: str,
        status: str = "SUCCESS",
        provenance: Optional[str] = None,
    ) -> None:
        duration = time.time() - start_time
        if self.tools_called:
            self.tools_called[-1]["duration"] = duration
            self.tools_called[-1]["status"] = status
            self.tools_called[-1]["summary"] = result_summary
            if provenance:
                self.tools_called[-1]["provenance"] = provenance

        if not self.enabled:
            return

        status_color = GREEN if status == "SUCCESS" else RED
        prov_label = f" | Provenance: {provenance}" if provenance else ""
        print(f"  {YELLOW}{BOLD}[TOOL RESULT]{RESET}")
        print(f"    {DIM}Tool:{RESET}          {tool_name}")
        print(f"    {DIM}Status:{RESET}        {status_color}{status}{RESET} ({duration:.3f}s){prov_label}")
        print(f"    {DIM}Summary:{RESET}       {result_summary}")

    def on_conditional_route(
        self,
        source_node: str,
        router_fn_name: str,
        evaluated_condition: str,
        selected_dest: str,
        alternatives: list[tuple[str, str]],
    ) -> None:
        record = {
            "source_node": source_node,
            "router_fn": router_fn_name,
            "condition": evaluated_condition,
            "selected_dest": selected_dest,
            "alternatives": alternatives,
        }
        self.routing_decisions.append(record)

        if not self.enabled:
            return

        print(f"\n{MAGENTA}{BOLD}[GRAPH CONDITIONAL ROUTING]{RESET}")
        print(f"  {DIM}Source Node:{RESET}           {BOLD}{source_node}{RESET}")
        print(f"  {DIM}Router Function:{RESET}       {router_fn_name}")
        print(f"  {DIM}Condition Evaluated:{RESET}   {evaluated_condition}")
        print(f"  {DIM}Selected Destination:{RESET}  {GREEN}{BOLD}{selected_dest}{RESET}")
        for alt_dest, reason in alternatives:
            print(f"  {DIM}Alternative Branch:{RESET}    {alt_dest} (Not Selected: {reason})")

    def print_execution_summary(self, final_state: dict[str, Any]) -> None:
        if not self.enabled:
            return

        print(f"\n{'='*75}")
        print(f"{BOLD}GRAPH EXECUTION SUMMARY{RESET}")
        print(f"{'='*75}")
        print(f"  {BOLD}Nodes Executed in Order:{RESET}")
        for i, node in enumerate(self.nodes_executed, 1):
            print(f"    {i}. {node}")

        print(f"\n  {BOLD}Tools Invoked in Order ({len(self.tools_called)}):{RESET}")
        for i, t in enumerate(self.tools_called, 1):
            dur = t.get("duration", 0.0)
            st_color = GREEN if t.get("status") == "SUCCESS" else RED
            print(f"    {i}. {t['tool']} [{t['source']}] ({dur:.3f}s, {t.get('provenance', 'N/A')}) - {st_color}{t.get('status')}{RESET}")

        print(f"\n  {BOLD}Conditional Routing Decisions ({len(self.routing_decisions)}):{RESET}")
        for i, r in enumerate(self.routing_decisions, 1):
            print(f"    {i}. {r['source_node']} --({r['router_fn']})--> {GREEN}{r['selected_dest']}{RESET}")
            print(f"       Condition: {r['condition']}")

        ps = final_state.get("planset")
        print(f"\n  {BOLD}Final Workflow State:{RESET}")
        print(f"    Plan ID:        {final_state.get('plan_id')}")
        print(f"    Active Version: v{final_state.get('active_version', 1)}")
        print(f"    Status:         {final_state.get('status')}")
        print(f"    Next Action:    {final_state.get('next_action')}")
        if ps and ps.plans:
            plan_a = ps.plans[0].option
            print(f"    Plan A Target:  {plan_a.mandi_name} (Score: Rs {plan_a.score:,.0f})")
        if final_state.get("diff"):
            diff = final_state["diff"]
            print(f"    Proposed Diff:  v{diff.old_version} -> v{diff.new_version} ({diff.primary_reason})")
        print(f"{'='*75}\n")


# Global tracer singleton
_TRACER: ExecutionTracer = ExecutionTracer(enabled=True)


def get_tracer() -> ExecutionTracer:
    return _TRACER


def inspect_compiled_graph_topology(graph: Any) -> None:
    """
    Directly inspects the compiled LangGraph object to display registered nodes,
    normal static edges, and conditional branch specs.
    """
    print(f"\n{BLUE}{BOLD}========================================================================{RESET}")
    print(f"{BLUE}{BOLD}LANGGRAPH ARCHITECTURE TOPOLOGY INSPECTION{RESET}")
    print(f"{BLUE}{BOLD}========================================================================{RESET}")

    # 1. Registered Nodes
    nodes = list(graph.nodes.keys())
    print(f"\n{BOLD}1. Registered Graph Nodes ({len(nodes)}):{RESET}")
    for n in nodes:
        node_obj = graph.nodes.get(n)
        print(f"   - {BOLD}{n:<20}{RESET} (Node runner: {type(node_obj).__name__})")

    # 2. Fixed Edges
    builder = getattr(graph, "builder", None)
    if builder:
        edges = list(builder.edges)
        print(f"\n{BOLD}2. Normal Edges ({len(edges)}):{RESET}")
        for src, dest in edges:
            print(f"   {src} ---> {dest}")

        # 3. Conditional Edges / Branches
        branches = builder.branches
        print(f"\n{BOLD}3. Conditional Edges & Routing Functions ({len(branches)}):{RESET}")
        for src_node, branch_dict in branches.items():
            if isinstance(branch_dict, dict):
                items = branch_dict.items()
            else:
                items = [(getattr(b, "name", str(i)), b) for i, b in enumerate(branch_dict)]

            for branch_name, branch_spec in items:
                path_obj = getattr(branch_spec, "path", branch_spec)
                fn_name = getattr(path_obj, "__name__", str(branch_name))
                ends_dict = getattr(branch_spec, "ends", {})
                ends = list(ends_dict.keys()) if ends_dict else []
                print(f"   {BOLD}{src_node}{RESET} --[ {MAGENTA}{fn_name}(){RESET} ]--> {ends}")
                for dest in ends:
                    print(f"       -> {dest}")

    print(f"{BLUE}{BOLD}========================================================================{RESET}\n")
