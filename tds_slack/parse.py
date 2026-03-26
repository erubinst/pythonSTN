# File to parse through request and initial schedule files
import pandas as pd
from tds_slack.utils import *


def load_request_data(request_data):

    templates = {t["name"]: t for t in request_data.get("templates", [])}
    orders = {o["name"]: o for o in request_data.get("orders", [])}
    resources = {r["name"]: r for r in request_data.get("resourceTypes", [])}
    
    return {
        "templates": templates,
        "orders": orders,
        "resources": resources,
    }


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
            if cz_datetime:
                start_time = minutes_since_cz(downtime["start_time"], cz_datetime)
                end_time = minutes_since_cz(downtime["end_time"], cz_datetime)
            else:        
                start_time = downtime["start_time"]
                end_time = downtime["end_time"]
            rows.append({
                'resource_name': name.lower(),
                'start_time': start_time,
                'end_time': end_time,
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
        # assume single capability per subtask for now, can be extended to multiple if needed
        capability = subtask.get("requiredCapabilities", [None])[0]
        if cz_datetime:
            est = minutes_since_cz(order["earliestStartDate"], cz_datetime)
            lft = minutes_since_cz(order["dueDate"], cz_datetime)
        else:
            est = order["earlieststartdate"]
            lft = order["duedate"]
        rows.append({
            "task_name": order_name,
            "capability": capability,
            "est": est,
            "lft": lft,
            "locations": [order['start-location'], order['end-location']],
            "duration": subtask.get("duration", None),
            "task_type": subtask.get("task_type","NA")
        })
    return pd.DataFrame(rows)


def load_resources_and_tasks(request_dict, cz_datetime_str=None):
    cz_datetime = datetime.fromisoformat(cz_datetime_str) if cz_datetime_str else None
    request_data = load_request_data(request_dict)

    resources_df = load_resources_df(request_data["resources"])
    downtimes_df = load_downtimes_df(request_data['resources'], cz_datetime)
    tasks_df = load_tasks_df(request_data["templates"], request_data["orders"], cz_datetime)

    return resources_df, downtimes_df, tasks_df