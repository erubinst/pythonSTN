# File to parse through request and initial schedule files
import json
import pandas as pd
from .utils import *


def load_request_data(request_data):

    templates = {t["name"]: t for t in request_data.get("templates", [])}
    orders = {o["name"]: o for o in request_data.get("orders", [])}
    resources = {r["name"]: r for r in request_data.get("resourceTypes", [])}
    constraints = request_data.get("order-constraints", [])
    order_constraints = []
    for constraint in constraints:
        src = constraint.get("source")
        dst = constraint.get("destination")

        order_constraints.append((src,dst))
    
    return {
        "templates": templates,
        "orders": orders,
        "resources": resources,
        "order_constraints": order_constraints # adjust this to the format you want for order constarints
    }


def schedule_json_to_df(initial_schedule_path: str) -> pd.DataFrame:
    """
    Load the initial_schedule.json file and convert resource timelines
    into a normalized DataFrame.

    Returns columns:
        resourceName, order, capability
    """
    with open(initial_schedule_path, "r") as f:
        schedule_data = json.load(f)

    return schedule_dict_to_df(schedule_data)


def schedule_dict_to_df(schedule_data):
    rows = []
    for res_entry in schedule_data.get("resources", []):
        res_name = res_entry["resourceName"]
        for tentry in res_entry.get("timeline", []):
            rows.append({
                "resource_name": res_name,
                "order": tentry.get("order"),
                "capability": tentry.get("capability"),
            })

    return pd.DataFrame(rows)
    


def load_resources_df(resources_dict):
    """Return a DataFrame of resources and their capabilities."""
    rows = []
    for name, r in resources_dict.items():
        rows.append({
            "resource_name": name.lower(),
            "capabilities": ", ".join(r.get("capabilities", [])),
            'location': r['location'],
        })
    return pd.DataFrame(rows)

def load_downtimes_df(resources_dict, cz_datetime):
    rows = []
    for name, r in resources_dict.items():
        for downtime in r['downtimes']:
            rows.append({
                'resource_name': name.lower(),
                'start_time': minutes_since_cz(downtime["start_time"], cz_datetime),
                'end_time': minutes_since_cz(downtime["end_time"], cz_datetime),
                'location': downtime['location'],
                'duration': downtime['duration']
            })
    return pd.DataFrame(rows)


def load_tasks_df(templates_dict, orders_dict, cz_datetime):
    """Return a DataFrame of tasks/orders with required capabilities, est, lft, duration."""
    rows = []
    for order_name, order in orders_dict.items():
        template_name = order["tasks"][0]  # assume one template per order
        template = templates_dict.get(template_name)
        if not template:
            continue
        subtask = template["subtasks"][0]  # assume one subtask per template
        rows.append({
            "task_name": order_name,
            "required_capabilities": ", ".join(subtask.get("requiredCapabilities", [])),
            "est": minutes_since_cz(order["earlieststartdate"], cz_datetime),
            "lft": minutes_since_cz(order["duedate"], cz_datetime),
            "locations": [order['start-location'], order['end-location']],
            "duration": subtask.get("duration", None),
            "task_type": subtask.get("task_type","NA")
        })
    return pd.DataFrame(rows)


def load_resources_and_tasks(request_dict, cz_datetime_str):
    cz_datetime = datetime.fromisoformat(cz_datetime_str)
    request_data = load_request_data(request_dict)

    resources_df = load_resources_df(request_data["resources"])
    downtimes_df = load_downtimes_df(request_data['resources'], cz_datetime)
    tasks_df = load_tasks_df(request_data["templates"], request_data["orders"], cz_datetime)
    order_constraints = request_data["order_constraints"]

    return resources_df, downtimes_df, tasks_df, order_constraints