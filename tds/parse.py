# File to parse through request and initial schedule files
import json
import pandas as pd
from utils import *


def load_request_data(request_path):
    """Load request.json and return dicts for templates, orders, and resources."""
    with open(request_path, "r") as f:
        request_data = json.load(f)

    templates = {t["name"]: t for t in request_data.get("templates", [])}
    orders = {o["name"]: o for o in request_data.get("orders", [])}
    resources = {r["name"]: r for r in request_data.get("resourceTypes", [])}
    # load in order constraints
    return {
        "templates": templates,
        "orders": orders,
        "resources": resources,
        "order_constraints": request_data.get("order-constraints", [])
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

    rows = []
    for res_entry in schedule_data.get("resources", []):
        res_name = res_entry["resourceName"]
        for tentry in res_entry.get("timeline", []):
            rows.append({
                "resourceName": res_name,
                "order": tentry.get("order"),
                "capability": tentry.get("capability"),
            })

    return pd.DataFrame(rows)


def load_resources_df(resources_dict):
    """Return a DataFrame of resources and their capabilities."""
    rows = []
    for name, r in resources_dict.items():
        rows.append({
            "resource_name": name,
            "capabilities": ", ".join(r.get("capabilities", [])),
            'location': r['location']
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
            "duration": subtask.get("duration", None)
        })
    return pd.DataFrame(rows)


def load_travel_matrix(travel_matrix_path):
    """Load travel time matrix from JSON file."""
    with open(travel_matrix_path, "r") as f:
        travel_data = json.load(f)
    return travel_data  


def load_resources_and_tasks(request_path, travel_matrix_path, cz_datetime_str="2025-05-19T00:00"):
    cz_datetime = datetime.fromisoformat(cz_datetime_str)
    request_data = load_request_data(request_path)

    # TODO: add order constraints 
    resources_df = load_resources_df(request_data["resources"])
    tasks_df = load_tasks_df(request_data["templates"], request_data["orders"], cz_datetime)
    travel_matrix_dict = load_travel_matrix(travel_matrix_path)

    return resources_df, tasks_df, travel_matrix_dict #TODO: return order constraints as well