"""Cała odpowiedź wyszukiwania naraz - etapy planner.plan_flow_stages
sklejone w jeden słownik, tak jak po ostatnim etapie widzi ją front."""
import planner


def plan_flow(*args, **kwargs):
    result = {}
    for _, delta in planner.plan_flow_stages(*args, **kwargs):
        result.update(delta)
    return result
